"""NEAR Intents 1Click API client: tokens, quote, deposit notification and status tracking.

Follows the 1Click quickstart (https://docs.near-intents.org/integration/distribution-channels/1click-api/quickstart)
and the OpenAPI spec at https://1click.chaindefuser.com/docs/v0/openapi.yaml (v0.1.10):

    GET /v0/tokens -> POST /v0/quote (dry) -> [LIVE: POST /v0/quote (not dry) -> deposit -> POST /v0/deposit/submit]
    -> GET /v0/status until a terminal status

There is no 1Click testnet. Dry quotes (`dry: true`) are real mainnet quotes that never create a deposit
address, which is what DRY_RUN mode uses. This module never moves funds itself; see `anchorium.near.live` for
the separately gated deposit.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Any

import httpx

DEFAULT_BASE_URL = 'https://1click.chaindefuser.com'

IN_PROGRESS = ('PENDING_DEPOSIT', 'KNOWN_DEPOSIT_TX', 'PROCESSING')
TERMINAL = ('SUCCESS', 'REFUNDED', 'FAILED', 'INCOMPLETE_DEPOSIT')
ALL_STATUSES = IN_PROGRESS + TERMINAL

#: Where the user's funds are at each 1Click status (shown in the CLI and the dashboard timeline).
FUNDS_LOCATION = {
    'PENDING_DEPOSIT': 'In your account: waiting for the deposit to the 1Click deposit address',
    'KNOWN_DEPOSIT_TX': 'In transit: deposit transaction seen on the origin chain',
    'PROCESSING': 'Inside NEAR Intents: a solver is executing the swap',
    'SUCCESS': 'Delivered: output tokens are in the recipient account',
    'REFUNDED': 'Refunded: input tokens returned to the refund address',
    'INCOMPLETE_DEPOSIT': 'At the deposit address: deposit was below the required amount (top up or wait for refund)',
    'FAILED': 'Swap failed: check the NEAR Intents explorer for the deposit address',
    'TIMEOUT': 'Unknown: no terminal status before the local timeout; polling resumes on restart',
}

#: Mapping from a terminal 1Click status to the guard contract's OutcomeStatus.
OUTCOME_FOR_STATUS = {
    'SUCCESS': 'SUCCESS',
    'REFUNDED': 'REFUNDED',
    'FAILED': 'FAILED',
    # Funds sit at the deposit address until topped up or refunded: the trade did not happen.
    'INCOMPLETE_DEPOSIT': 'FAILED',
}


class OneClickError(RuntimeError):
    """A 1Click API call failed. `status_code` is the HTTP status (None for transport errors)."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class Token:
    asset_id: str
    symbol: str
    blockchain: str
    decimals: int
    price_usd: Decimal
    contract_address: str | None

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> Token:
        return cls(asset_id=row['assetId'], symbol=row['symbol'], blockchain=row['blockchain'],
                   decimals=int(row['decimals']), price_usd=Decimal(str(row['price'])),
                   contract_address=row.get('contractAddress'))

    def to_units(self, amount: Decimal) -> int:
        """Human amount -> base units, rounded down."""
        return int((amount * (Decimal(10) ** self.decimals)).to_integral_value(rounding='ROUND_DOWN'))

    def from_units(self, units: int | str) -> Decimal:
        return Decimal(int(units)) / (Decimal(10) ** self.decimals)


@dataclass(frozen=True)
class QuoteRequest:
    origin_asset: str
    destination_asset: str
    amount: int  # base units of the origin asset (EXACT_INPUT)
    recipient: str
    refund_to: str
    slippage_tolerance_bps: int
    dry: bool = True
    deadline_seconds: int = 300

    def to_api(self, now: datetime | None = None) -> dict[str, Any]:
        if self.amount <= 0:
            raise ValueError('amount must be positive')
        if not 0 <= self.slippage_tolerance_bps <= 10_000:
            raise ValueError('slippage_tolerance_bps out of range')
        now = now or datetime.now(timezone.utc)
        deadline = (now + timedelta(seconds=self.deadline_seconds)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        return {
            'dry': self.dry,
            'swapType': 'EXACT_INPUT',
            'slippageTolerance': self.slippage_tolerance_bps,
            'originAsset': self.origin_asset,
            'depositType': 'ORIGIN_CHAIN',
            'destinationAsset': self.destination_asset,
            'amount': str(self.amount),
            'refundTo': self.refund_to,
            'refundType': 'ORIGIN_CHAIN',
            'recipient': self.recipient,
            'recipientType': 'DESTINATION_CHAIN',
            'deadline': deadline,
        }


@dataclass(frozen=True)
class Quote:
    correlation_id: str
    signature: str
    timestamp: str
    dry: bool
    amount_in: int
    amount_out: int
    min_amount_out: int
    time_estimate_s: float
    deposit_address: str | None
    deposit_memo: str | None
    deadline: str | None
    app_fee_bps: int
    raw: dict[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, body: dict[str, Any]) -> Quote:
        q, req = body['quote'], body.get('quoteRequest', {})
        return cls(
            correlation_id=body['correlationId'], signature=body['signature'], timestamp=body['timestamp'],
            dry=bool(req.get('dry', True)), amount_in=int(q['amountIn']), amount_out=int(q['amountOut']),
            min_amount_out=int(q['minAmountOut']), time_estimate_s=float(q.get('timeEstimate', 0)),
            deposit_address=q.get('depositAddress'), deposit_memo=q.get('depositMemo'), deadline=q.get('deadline'),
            app_fee_bps=sum(int(f.get('fee', 0)) for f in req.get('appFees') or []), raw=body)

    @property
    def quote_id(self) -> str:
        """Stable id for the journal: the 1Click correlation id (dry quotes have no deposit address)."""
        return self.correlation_id


def worst_case_slippage_bps(quote: Quote, origin: Token, destination: Token) -> int:
    """Most value the swap can lose versus 1Click's reference prices, in basis points (rounded up, >= 0).

    Compares the guaranteed output (`minAmountOut`) with the input, both valued at the `/v0/tokens` USD prices.
    It therefore includes price impact, fees and the requested slippage tolerance. A quote better than the
    reference prices gives 0. This is the figure checked against the guard's `max_slippage_bps`.
    """
    value_in = origin.from_units(quote.amount_in) * origin.price_usd
    if value_in <= 0:
        raise OneClickError('cannot value the input amount')
    value_min_out = destination.from_units(quote.min_amount_out) * destination.price_usd
    loss = (Decimal(1) - value_min_out / value_in) * Decimal(10_000)
    return max(0, int(loss.to_integral_value(rounding=ROUND_CEILING)))


@dataclass(frozen=True)
class StatusUpdate:
    status: str
    updated_at: str
    observed_at: str
    funds_location: str
    swap_details: dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL


@dataclass
class Tracking:
    """Result of polling a deposit address: the full timeline and the final (or last seen) status."""
    deposit_address: str
    timeline: list[StatusUpdate]
    timed_out: bool = False

    @property
    def final_status(self) -> str:
        if self.timed_out:
            return 'TIMEOUT'
        return self.timeline[-1].status if self.timeline else 'TIMEOUT'

    @property
    def outcome_status(self) -> str | None:
        """Guard OutcomeStatus for a terminal result, else None (never guess an outcome)."""
        return None if self.timed_out else OUTCOME_FOR_STATUS.get(self.final_status)


class OneClickClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, jwt: str | None = None, *,
                 transport: httpx.BaseTransport | None = None, timeout: float = 15.0):
        headers = {'Accept': 'application/json', 'User-Agent': 'anchorium-near/0.1'}
        if jwt:
            headers['Authorization'] = f'Bearer {jwt}'
        self.authenticated = bool(jwt)
        self._http = httpx.Client(base_url=base_url.rstrip('/'), headers=headers, timeout=timeout,
                                  transport=transport)
        self._tokens: dict[str, Token] | None = None

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise OneClickError(f'{method} {path}: {type(exc).__name__}: {exc}') from exc
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.status_code >= 400:
            message = body.get('message') if isinstance(body, dict) else str(body)[:200]
            raise OneClickError(f'{method} {path} -> HTTP {response.status_code}: {message}',
                                response.status_code, body)
        return body

    # ----- tokens -----

    def tokens(self, refresh: bool = False) -> dict[str, Token]:
        if self._tokens is None or refresh:
            rows = self._request('GET', '/v0/tokens')
            self._tokens = {row['assetId']: Token.from_api(row) for row in rows}
        return self._tokens

    def token(self, asset_id: str) -> Token:
        tokens = self.tokens()
        if asset_id not in tokens:
            raise OneClickError(f'asset {asset_id} is not supported by 1Click')
        return tokens[asset_id]

    # ----- quote -----

    def quote(self, request: QuoteRequest) -> Quote:
        quote = Quote.from_api(self._request('POST', '/v0/quote', json=request.to_api()))
        if quote.dry != request.dry:
            raise OneClickError('quote dry flag does not match the request')
        if not request.dry and not quote.deposit_address:
            raise OneClickError('live quote has no deposit address')
        if quote.min_amount_out > quote.amount_out:
            raise OneClickError('quote minAmountOut exceeds amountOut')
        return quote

    # ----- deposit + status -----

    def submit_deposit(self, deposit_address: str, tx_hash: str, *, near_sender: str | None = None,
                       memo: str | None = None) -> Any:
        body: dict[str, Any] = {'depositAddress': deposit_address, 'txHash': tx_hash}
        if near_sender:
            body['nearSenderAccount'] = near_sender
        if memo:
            body['memo'] = memo
        return self._request('POST', '/v0/deposit/submit', json=body)

    def status(self, deposit_address: str, memo: str | None = None) -> StatusUpdate:
        params = {'depositAddress': deposit_address}
        if memo:
            params['depositMemo'] = memo
        body = self._request('GET', '/v0/status', params=params)
        status = body.get('status')
        if status not in ALL_STATUSES:
            raise OneClickError(f'unknown 1Click status {status!r}', body=body)
        return StatusUpdate(status=status, updated_at=body.get('updatedAt', ''),
                            observed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                            funds_location=FUNDS_LOCATION[status], swap_details=body.get('swapDetails') or {})

    def track(self, deposit_address: str, *, memo: str | None = None, timeout_s: float = 600.0,
              poll_s: float = 5.0, on_update: Callable[[StatusUpdate], None] | None = None,
              sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
              max_transient_errors: int = 5) -> Tracking:
        """Polls until a terminal status or the timeout. Records each status change once, in order."""
        tracking = Tracking(deposit_address, [])
        deadline = clock() + timeout_s
        errors = 0
        while True:
            try:
                update = self.status(deposit_address, memo)
                errors = 0
            except OneClickError as exc:
                if exc.status_code is not None and exc.status_code < 500 and exc.status_code != 429:
                    raise
                errors += 1
                if errors > max_transient_errors:
                    raise
                update = None
            if update and (not tracking.timeline or tracking.timeline[-1].status != update.status):
                tracking.timeline.append(update)
                if on_update:
                    on_update(update)
            if update and update.terminal:
                return tracking
            if clock() >= deadline:
                tracking.timed_out = True
                return tracking
            sleep(poll_s)
