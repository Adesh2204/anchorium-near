"""PayPal Orders API v2 client, sandbox only.

Flow (https://developer.paypal.com/docs/api/orders/v2/):
    POST /v1/oauth2/token                       client-credentials access token
    POST /v2/checkout/orders                    intent=CAPTURE, one purchase unit in USD
    (buyer approves in the PayPal popup / approve link with a sandbox personal account)
    POST /v2/checkout/orders/{id}/capture       server-side capture, idempotent via PayPal-Request-Id

Safety:
* Refuses any base URL that is not the PayPal **sandbox** (`api-m.sandbox.paypal.com`). This edition never moves
  real money.
* Top-up amounts are validated server-side (Decimal, 2 dp, between MIN_TOPUP_USD and MAX_TOPUP_USD).
* The amount credited to the ledger is the amount PayPal reports as captured, never the amount the browser sent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

import httpx

SANDBOX_BASE_URL = 'https://api-m.sandbox.paypal.com'
SANDBOX_JS_SDK = 'https://www.paypal.com/sdk/js'
MIN_TOPUP_USD = Decimal('1.00')
MAX_TOPUP_USD = Decimal('50.00')
CUSTOM_ID_PREFIX = 'anchorium-budget'


class PayPalError(RuntimeError):
    """A PayPal API call failed or returned something this client will not trust."""


def parse_amount(value: Any) -> Decimal:
    """A top-up amount in USD: 2 decimal places, inside [MIN_TOPUP_USD, MAX_TOPUP_USD]."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PayPalError(f'invalid amount: {value!r}') from exc
    if not amount.is_finite() or amount != amount.quantize(Decimal('0.01'), rounding=ROUND_DOWN):
        raise PayPalError(f'amount must have at most 2 decimals: {value!r}')
    if not MIN_TOPUP_USD <= amount <= MAX_TOPUP_USD:
        raise PayPalError(f'amount must be between ${MIN_TOPUP_USD} and ${MAX_TOPUP_USD}: {value!r}')
    return amount


@dataclass(frozen=True)
class Capture:
    order_id: str
    capture_id: str
    status: str          # COMPLETED / PENDING / DECLINED ...
    amount_usd: Decimal
    custom_id: str | None
    payer_email: str | None
    raw: dict[str, Any]


def approve_url(order: dict[str, Any]) -> str | None:
    for link in order.get('links') or []:
        if link.get('rel') in ('payer-action', 'approve'):
            return link.get('href')
    return None


def parse_capture(body: dict[str, Any]) -> Capture:
    try:
        unit = body['purchase_units'][0]
        cap = unit['payments']['captures'][0]
        if cap['amount']['currency_code'] != 'USD':
            raise PayPalError(f'unexpected currency {cap["amount"]["currency_code"]}')
        return Capture(order_id=body['id'], capture_id=cap['id'], status=cap['status'],
                       amount_usd=Decimal(cap['amount']['value']),
                       custom_id=cap.get('custom_id') or unit.get('custom_id'),
                       payer_email=(body.get('payer') or {}).get('email_address'), raw=body)
    except (KeyError, IndexError, TypeError, InvalidOperation) as exc:
        raise PayPalError(f'unexpected capture response: {exc}') from exc


class PayPalClient:
    def __init__(self, client_id: str, client_secret: str, base_url: str = SANDBOX_BASE_URL,
                 transport: httpx.BaseTransport | None = None, timeout: float = 20.0):
        if base_url.rstrip('/') != SANDBOX_BASE_URL:
            raise PayPalError(f'sandbox only: refusing base URL {base_url!r} (expected {SANDBOX_BASE_URL})')
        if not client_id or not client_secret:
            raise PayPalError('PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required (sandbox app credentials)')
        self.client_id = client_id
        self._secret = client_secret
        self._http = httpx.Client(base_url=SANDBOX_BASE_URL, timeout=timeout, transport=transport)
        self._token: tuple[str, float] | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | Any) -> PayPalClient:
        return cls(env.get('PAYPAL_CLIENT_ID', ''), env.get('PAYPAL_CLIENT_SECRET', ''),
                   env.get('PAYPAL_BASE_URL') or SANDBOX_BASE_URL)

    # ----- auth -----

    def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        r = self._http.post('/v1/oauth2/token', data={'grant_type': 'client_credentials'},
                            auth=(self.client_id, self._secret))
        if r.status_code != 200:
            raise PayPalError(f'oauth2/token HTTP {r.status_code}: {r.text[:200]}')
        body = r.json()
        self._token = (body['access_token'], time.time() + int(body.get('expires_in', 300)))
        return self._token[0]

    def _call(self, method: str, path: str, *, json: dict[str, Any] | None = None,
              request_id: str | None = None) -> dict[str, Any]:
        headers = {'Authorization': f'Bearer {self._access_token()}', 'Content-Type': 'application/json',
                   'Prefer': 'return=representation'}
        if request_id:
            headers['PayPal-Request-Id'] = request_id
        r = self._http.request(method, path, json=json, headers=headers)
        if r.status_code >= 400:
            try:
                detail = r.json()
                msg = detail.get('details', [{}])[0].get('issue') or detail.get('name') or r.text[:200]
            except ValueError:
                msg = r.text[:200]
            raise PayPalError(f'{method} {path} HTTP {r.status_code}: {msg}')
        return r.json()

    # ----- Orders v2 -----

    def create_order(self, amount_usd: Any, *, custom_id: str, return_url: str | None = None,
                     cancel_url: str | None = None) -> dict[str, Any]:
        amount = parse_amount(amount_usd)
        unit: dict[str, Any] = {
            'reference_id': 'anchorium-paper-budget',
            'custom_id': custom_id[:127],
            'description': 'Anchorium agent budget top-up (PayPal sandbox, paper trading)',
            'amount': {'currency_code': 'USD', 'value': f'{amount:.2f}'},
        }
        body: dict[str, Any] = {'intent': 'CAPTURE', 'purchase_units': [unit]}
        if return_url:
            body['payment_source'] = {'paypal': {'experience_context': {
                'brand_name': 'Anchorium', 'user_action': 'PAY_NOW', 'shipping_preference': 'NO_SHIPPING',
                'return_url': return_url, 'cancel_url': cancel_url or return_url}}}
        return self._call('POST', '/v2/checkout/orders', json=body, request_id=f'create-{custom_id}'[:108])

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._call('GET', f'/v2/checkout/orders/{_safe_id(order_id)}')

    def capture_order(self, order_id: str) -> Capture:
        oid = _safe_id(order_id)
        body = self._call('POST', f'/v2/checkout/orders/{oid}/capture', request_id=f'capture-{oid}')
        return parse_capture(body)


def _safe_id(order_id: str) -> str:
    if not order_id or not order_id.isalnum() or len(order_id) > 64:
        raise PayPalError(f'invalid order id: {order_id!r}')
    return order_id
