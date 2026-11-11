"""LIVE exit with the profit floor used in paper trade 2.

Sell the open LIVE position only when a fresh dry quote's guaranteed output (`minAmountOut`, after 1Click's fee and
slippage) is at least: the USDC the entry actually spent + the NEAR the entry deposit actually cost (gas and any
storage registration, measured from the trader's balance) + an estimate of the same for the exit deposit + the
target. The agent re-checks its own LIVE exit quote against that floor before journaling, and the human still types
the confirmation. If the floor is not reached by `until`, nothing is sold: the human decides (`--manual`).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from decimal import ROUND_CEILING, Decimal
from typing import Any

from anchorium.agent.orchestrator import DRY_RUN_PLACEHOLDER_RECIPIENT, USDC
from anchorium.near.intents import QuoteRequest
from anchorium.paper_trade import FT_TRANSFER_SAMPLES, utcnow

WNEAR = 'nep141:wrap.near'
YOCTO = Decimal(10) ** 24


def live_entry(store) -> dict[str, Any] | None:
    """The settled LIVE entry (BUY, SUCCESS) in the checkpoint store."""
    for (h,) in store.db.execute("SELECT signal_hash FROM runs WHERE mode = 'LIVE' AND side = 'BUY' ORDER BY rowid"):
        row = store.get(h)
        if row['state'] == 'DONE' and row['data'].get('result') == 'SUCCESS':
            return row
    return None


def deposit_near_cost(row: dict[str, Any]) -> Decimal | None:
    """NEAR the trader spent on a leg's deposit, from the balances read before and after it (None if unknown)."""
    before, after = row['data'].get('near_yocto_before'), row['data'].get('near_yocto_after')
    if before is None or after is None:
        return None
    return (Decimal(int(before)) - Decimal(int(after))) / YOCTO


def floor_parts(entry: dict[str, Any], near_usd: Decimal, target_usd: Decimal, storage_min_near: Decimal,
                usdc_decimals: int = 6) -> dict[str, Any]:
    paid = Decimal(int(entry['data']['outcome_args']['amount_in'])) / Decimal(10) ** usdc_decimals
    estimate = storage_min_near + max(FT_TRANSFER_SAMPLES.values())  # registration + gas, if nothing was measured
    entry_near = deposit_near_cost(entry)
    exit_near = max(entry_near or Decimal(0), estimate)
    entry_near = estimate if entry_near is None else entry_near
    near_cost_usd = (entry_near + exit_near) * near_usd
    floor = paid + near_cost_usd + target_usd
    return {'paid_usdc': str(paid), 'entry_near': str(entry_near), 'entry_near_measured': deposit_near_cost(entry) is not None,
            'exit_near_estimate': str(exit_near), 'near_usd': str(near_usd), 'near_cost_usd': str(near_cost_usd),
            'target_usd': str(target_usd), 'floor_usdc': str(floor),
            'floor_units': int((floor * Decimal(10) ** usdc_decimals).to_integral_value(rounding=ROUND_CEILING))}


def check(agent, entry: dict[str, Any], amount_units: int, target_usd: Decimal, storage_min_near: Decimal) -> dict:
    tokens = agent.oneclick.tokens(refresh=True)
    asset = entry['data']['quote']['quoteRequest']['destinationAsset']
    usdc = tokens[USDC]
    quote = agent.oneclick.quote(QuoteRequest(
        origin_asset=asset, destination_asset=USDC, amount=amount_units, recipient=DRY_RUN_PLACEHOLDER_RECIPIENT,
        refund_to=DRY_RUN_PLACEHOLDER_RECIPIENT, slippage_tolerance_bps=agent.config.quote_tolerance_bps, dry=True))
    parts = floor_parts(entry, tokens[WNEAR].price_usd, target_usd, storage_min_near, usdc.decimals)
    out_min = usdc.from_units(quote.min_amount_out)
    return {'at': utcnow(), 'quote_id': quote.quote_id, 'amount_out': str(usdc.from_units(quote.amount_out)),
            'min_amount_out': str(out_min), **parts,
            'net_worst_case': str(out_min - Decimal(parts['paid_usdc']) - Decimal(parts['near_cost_usd'])),
            'clears': quote.min_amount_out >= parts['floor_units']}


def wait_and_exit(agent, *, coin: str, amount_units: Callable[[], int], target_usd: Decimal, until_ms: int,
                  storage_min_near: Decimal, exit_signal: Callable[[str, str], Any], notifier, print_=print,
                  sleep=time.sleep, clock=time.time) -> dict[str, Any] | None:
    """Checks once a minute; returns the exit row once a LIVE exit was sent, or None at `until_ms` (nothing sold)."""
    entry = live_entry(agent.store)
    if entry is None:
        raise SystemExit('no settled LIVE entry in var/agent-live.sqlite')
    while clock() * 1000 < until_ms:
        try:
            c = check(agent, entry, amount_units(), target_usd, storage_min_near)
        except Exception as exc:  # noqa: BLE001 - a failed read waits for the next minute
            print_(f'[check]   {type(exc).__name__}: {exc}')
            c = None
        if c is not None:
            print_(f'[check]   {c["at"]} sell -> {c["amount_out"]} USDC (min {c["min_amount_out"]}); need min >= '
                   f'{Decimal(c["floor_usdc"]):.6f} (= {c["paid_usdc"]} paid + ${Decimal(c["near_cost_usd"]):.6f} '
                   f'NEAR costs + ${c["target_usd"]}); worst-case net {Decimal(c["net_worst_case"]):+.6f} -> '
                   f'{"SELL" if c["clears"] else "hold"}', flush=True)
            if c['clears']:
                row = agent.process(exit_signal(coin, 'PROFIT_EXIT'), min_proceeds_units=c['floor_units'])
                if row['state'] != 'SKIPPED' and row['data'].get('result') != 'REJECTED':
                    return row
        sleep(max(1.0, 60 - (clock() % 60) + 2))
    notifier('Anchorium LIVE exit', 'Profit target not reached by the deadline: nothing was sold. Your decision.')
    return None
