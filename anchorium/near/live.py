"""LIVE-mode gates and the only code path that moves funds (a NEP-141 deposit to a 1Click deposit address).

DRY_RUN is the default. A LIVE deposit happens only when ALL of these hold:

1. `LIVE=1` and `DRY_RUN=0` are both set explicitly (contradictory flags are refused);
2. the trade's notional is <= `MAX_LIVE_NOTIONAL_USD`, which itself may not exceed the hard cap of 2 USD;
3. the session's live total stays <= the hard cap of 5 USD;
4. the guard contract recorded an ALLOWED decision for the trade;
5. a human types the exact confirmation phrase in an interactive terminal.

The deposit is signed by a separate mainnet trading account (`TRADER_ACCOUNT_ID`, key in
`~/.near-credentials/mainnet`). NEP-141 `ft_transfer` needs a 1 yoctoNEAR deposit, which function-call keys
cannot attach, so this account holds a full-access key. Fund it with only the few dollars you accept risking.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

HARD_MAX_TRADE_USD = Decimal('2')
HARD_MAX_TOTAL_USD = Decimal('5')


class LiveGateError(RuntimeError):
    """A LIVE precondition failed. Nothing was signed or sent."""


@dataclass(frozen=True)
class ExecutionConfig:
    live: bool
    max_trade_usd: Decimal
    max_total_usd: Decimal = HARD_MAX_TOTAL_USD

    @property
    def mode(self) -> str:
        return 'LIVE' if self.live else 'DRY_RUN'

    @property
    def banner(self) -> str:
        return f'LIVE (cap ${self.max_trade_usd}/trade, ${self.max_total_usd} total)' if self.live else 'DRY RUN'

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ExecutionConfig:
        live_flag = env.get('LIVE', '0').strip()
        dry_flag = env.get('DRY_RUN', '1').strip()
        if live_flag not in ('0', '1') or dry_flag not in ('0', '1'):
            raise LiveGateError('LIVE and DRY_RUN must be 0 or 1')
        if live_flag != '1':
            return cls(live=False, max_trade_usd=Decimal('0'))
        if dry_flag != '0':
            raise LiveGateError('LIVE=1 requires DRY_RUN=0 as well (refusing contradictory flags)')
        raw_cap = env.get('MAX_LIVE_NOTIONAL_USD', '2').strip()
        try:
            cap = Decimal(raw_cap)
        except InvalidOperation as exc:
            raise LiveGateError(f'MAX_LIVE_NOTIONAL_USD is not a number: {raw_cap!r}') from exc
        if not Decimal('0') < cap <= HARD_MAX_TRADE_USD:
            raise LiveGateError(f'MAX_LIVE_NOTIONAL_USD must be > 0 and <= {HARD_MAX_TRADE_USD} (got {cap})')
        return cls(live=True, max_trade_usd=cap)


def confirmation_phrase(notional_usd: Decimal, symbol: str) -> str:
    return f'LIVE {notional_usd:.2f} USD {symbol}'


def check_live_trade(config: ExecutionConfig, notional_usd: Decimal, spent_live_usd: Decimal,
                     guard_allowed: bool, entry_cost_usd: Decimal | None = None) -> None:
    """`entry_cost_usd` is set only for an exit that sells an open position: the per-trade cap then applies to what
    the entry spent (the amount put at risk), since a profitable exit is worth more than the entry. The session
    total still counts the exit's full value, so an exit can never be worth more than the total cap leaves."""
    if not config.live:
        raise LiveGateError('not in LIVE mode')
    if not guard_allowed:
        raise LiveGateError('the guard contract did not allow this trade')
    capped = notional_usd if entry_cost_usd is None else entry_cost_usd
    if notional_usd <= 0 or capped <= 0 or capped > config.max_trade_usd:
        what = 'notional' if entry_cost_usd is None else 'entry cost of the position being sold'
        raise LiveGateError(f'{what} ${capped} exceeds the per-trade cap ${config.max_trade_usd}')
    if spent_live_usd + notional_usd > config.max_total_usd:
        raise LiveGateError(f'session total would reach ${spent_live_usd + notional_usd} > ${config.max_total_usd}')


def require_typed_confirmation(phrase: str, details: Mapping[str, object] | None = None, *,
                               prompt: Callable[[str], str] = input,
                               is_tty: Callable[[], bool] = lambda: sys.stdin.isatty(),
                               out: Callable[[str], None] = print) -> None:
    """Shows every detail of the deposit, then requires the exact phrase typed by a human."""
    if not is_tty():
        raise LiveGateError('LIVE confirmation needs an interactive terminal')
    if details:
        out('\n=== LIVE DEPOSIT: review before confirming ===')
        for key, value in details.items():
            out(f'  {key:<22} {value}')
    typed = prompt(f'\nType exactly "{phrase}" to send this deposit (anything else aborts): ')
    if typed.strip() != phrase:
        raise LiveGateError('confirmation did not match: aborted, nothing sent')


def deposit_nep141(near_client, token_contract: str, deposit_address: str, amount_human: Decimal) -> str:
    """Sends `amount_human` of a NEP-141 token to a 1Click deposit address. Returns the transaction hash.

    Called only by the orchestrator after `check_live_trade` and `require_typed_confirmation` pass.
    """
    result = near_client.ft_transfer(token_contract, deposit_address, str(amount_human), register=True)
    tx_hash = result.transaction_hash
    if not tx_hash:
        raise LiveGateError('deposit sent but no transaction hash returned')
    return tx_hash
