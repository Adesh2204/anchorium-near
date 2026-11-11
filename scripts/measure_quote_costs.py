"""Samples real 1Click DRY quotes for a $2 NEAR round trip and measures each leg's worst-case cost.

For every sample: the Hyperliquid NEAR mid (public `allMids`), then a dry quote USDC -> wNEAR for $2 and a dry quote
wNEAR -> USDC for the wNEAR that $2 buys at the mid. The leg cost is measured on the quote's GUARANTEED output
(`minAmountOut`) against the mid, so it contains the 1Click fee, price impact, reference lag and the requested
tolerance. `slippage_bps = leg_cost_bps - app_fee_bps`. Nothing is deposited (dry quotes create no deposit address).

    python scripts/measure_quote_costs.py --samples 40 --out docs/data/quote_costs.json
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from anchorium.agent.orchestrator import DRY_RUN_PLACEHOLDER_RECIPIENT, USDC
from anchorium.near.intents import OneClickClient, QuoteRequest

WNEAR = 'nep141:wrap.near'
HL = 'https://api.hyperliquid.xyz/info'


def hl_mid(client: httpx.Client) -> Decimal:
    return Decimal(client.post(HL, json={'type': 'allMids'}).raise_for_status().json()['NEAR'])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--samples', type=int, default=40)
    ap.add_argument('--gap-s', type=float, default=4.0)
    ap.add_argument('--usd', default='2.00')
    ap.add_argument('--tolerance-bps', type=int, default=10)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    oc, http = OneClickClient(), httpx.Client(timeout=20)
    tokens = oc.tokens(refresh=True)
    usdc, wnear = tokens[USDC], tokens[WNEAR]
    usd = Decimal(a.usd)
    rows = []
    for i in range(a.samples):
        try:
            mid = hl_mid(http)
            buy = oc.quote(QuoteRequest(USDC, WNEAR, usdc.to_units(usd), DRY_RUN_PLACEHOLDER_RECIPIENT,
                                        DRY_RUN_PLACEHOLDER_RECIPIENT, a.tolerance_bps, dry=True))
            near_in = wnear.to_units(usd / mid)
            sell = oc.quote(QuoteRequest(WNEAR, USDC, near_in, DRY_RUN_PLACEHOLDER_RECIPIENT,
                                         DRY_RUN_PLACEHOLDER_RECIPIENT, a.tolerance_bps, dry=True))
            mid_after = hl_mid(http)
        except Exception as exc:  # noqa: BLE001  (a failed sample is recorded, never silently dropped)
            rows.append({'i': i, 'at': datetime.now(timezone.utc).isoformat(), 'error': f'{type(exc).__name__}: {exc}'})
            time.sleep(a.gap_s)
            continue
        ref = (mid + mid_after) / 2
        buy_min = wnear.from_units(buy.min_amount_out)
        sell_min = usdc.from_units(sell.min_amount_out)
        buy_cost = 1 - buy_min * ref / usd
        sell_cost = 1 - sell_min / (wnear.from_units(near_in) * ref)
        rows.append({'i': i, 'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'hl_mid': str(ref),
                     'buy': {'correlation_id': buy.correlation_id, 'amount_in': str(usdc.to_units(usd)),
                             'amount_out': str(buy.amount_out), 'min_amount_out': str(buy.min_amount_out),
                             'app_fee_bps': buy.app_fee_bps,
                             'leg_cost_bps': str((buy_cost * 10_000).quantize(Decimal('0.01')))},
                     'sell': {'correlation_id': sell.correlation_id, 'amount_in': str(near_in),
                              'amount_out': str(sell.amount_out), 'min_amount_out': str(sell.min_amount_out),
                              'app_fee_bps': sell.app_fee_bps,
                              'leg_cost_bps': str((sell_cost * 10_000).quantize(Decimal('0.01')))}})
        print(i, rows[-1]['buy']['leg_cost_bps'], rows[-1]['sell']['leg_cost_bps'], flush=True)
        time.sleep(a.gap_s)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({'measured': 'worst-case leg cost = 1 - value(minAmountOut)/value(amountIn) at the '
                                             'Hyperliquid NEAR mid (avg of before/after)',
                                 'usd': a.usd, 'tolerance_bps': a.tolerance_bps,
                                 'samples': rows}, indent=1))


if __name__ == '__main__':
    main()
