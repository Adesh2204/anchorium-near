"""Renders the version A session report (docs/paper_trade_A_report.md or docs/live_trade_report.md) from the saved
session state only. Regenerated whole after every change, so a resumed session never duplicates a receipt."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from anchorium import money as M
from anchorium.near.guard_client import account_url


def render(state: dict[str, Any], *, guard: str, network: str) -> str:
    cfg = state['config']
    live = state['mode'] == 'LIVE'
    fx0 = state['fx_start']
    closed = [r for r in state['rounds'] if r['status'] == 'CLOSED']
    open_r = next((r for r in state['rounds'] if r['status'] in ('BUYING', 'OPEN', 'SELLING')), None)
    total = sum((Decimal(r['after_tax_inr']) for r in closed), Decimal(0))
    start_usd = M.usdc(state['start_usdc_units'])
    out = [
        f'# Version A {"LIVE" if live else "paper"} trade report: session `{state["session"]}`',
        '',
        f'{"LIVE: real mainnet swaps through NEAR Intents 1Click." if live else "PAPER (DRY_RUN): real 1Click dry quotes and a real testnet journal; fills are simulated at each quote" + chr(39) + "s guaranteed minimum (`minAmountOut`). No funds moved."}',
        '',
        '| | |', '| --- | --- |',
        f'| Started | {state["started_at"]} |',
        f'| Finished | {state["finished"]["at"] + ": " + state["finished"]["reason"] if state.get("finished") else "running"} |',
        f'| Capital | {start_usd:.6f} USDC = INR {Decimal(state["start_inr_at_start_fx"]):.2f} at {fx0["usd_inr"]} INR/USD ({fx0["source"]}, {fx0["fetched_at"]}) |',
        '| Strategy | Version A: long-only NEAR on the unchanged frozen strategy\'s LONG signal (Hyperliquid 1m) |',
        f'| Limits | ${cfg["order_usd"]} per trade, 1 open round, {cfg["max_rounds"]} rounds, session loss cap INR {cfg["loss_cap_inr"]}, cost filter {Decimal(cfg["cost_filter"]) * 100}% (fee + slippage + all deposit NEAR) |',
        f'| Exits | PROFIT_EXIT at INR {cfg["profit_target_inr"]} after fees, gas and a 31.2% tax reserve; STOP_LOSS at net <= -5% of entry; TIME_EXIT at the cut-off; no buys after the buy cut-off; 60 min cooldown after a stop |',
        f'| Guard / journal | [{guard}]({account_url(network, guard)}) ({network}): check_trade + record_decision/record_outcome for every leg |',
        f'| Result | {len(closed)} round(s) closed, session after tax INR {total:+.2f}{"; HARD STOP: " + state["hard_stop"] if state.get("hard_stop") else ""} |',
        '',
        '## Receipts',
        '',
    ]
    if not closed:
        out += ['No round closed in this session.', '']
    for r in closed:
        out += ['```', r['receipt_text'], '```', '']
    if open_r:
        out += ['## Open round (not closed)', '']
        b = open_r.get('buy')
        if b:
            out.append(f'Round #{open_r["n"]} bought {M.usdc(b["usdc_units"]):.6f} USDC -> {M.wnear(b["wnear_units"]):.6f} '
                       f'wNEAR @ ${b["near_usd"]} (decision #{b["decision_id"]}, {b["decision_tx_url"]}).')
            st = state.get('last_status')
            if st:
                c = st['check']
                fx = Decimal(c['usd_inr'])
                out.append(f'Last valuation on a real dry sell quote (worst case): {st["line"]}')
                out.append(f'Unrealized: net INR {Decimal(c["net_received"]) * fx:+.2f}, after tax INR '
                           f'{Decimal(c["net_after_tax_inr"]):+.2f}. This is reported as open, not as a result.')
        else:
            out.append(f'Round #{open_r["n"]} buy in progress ({open_r["status"]}).')
        out.append('')
    skips: dict[str, int] = {}
    for e in state.get('events', []):
        if e['event'].startswith('SKIPPED') or e['event'].endswith('NOT_ALLOWED'):
            skips[e['event']] = skips.get(e['event'], 0) + 1
    out += ['## Skips and refusals (last 200 events)', '',
            ', '.join(f'{k}: {v}' for k, v in sorted(skips.items())) or 'none', '',
            '## One-time setup costs (not part of any round)', '']
    if live:
        out += ['* Exchange withdrawal fee to fund the trading account: as charged by the exchange (see your '
                'exchange statement; not visible on chain).',
                '* Storage registration of the trading account on USDC and wNEAR (`make setup-trader`): '
                '0.00125 NEAR each, if not already registered.']
    else:
        out += ['None in a paper session. A LIVE session adds the exchange withdrawal fee and the trading account\'s '
                'one-time USDC/wNEAR storage registration (0.00125 NEAR each).']
    out += ['',
            '## Cost notes', '',
            '* Each deposit leg costs NEAR: the transfer\'s gas plus the storage registration of the new 1Click '
            'deposit address (0.00125 NEAR; every deposit address is new and unregistered, checked 2026-11-12). '
            + ('LIVE receipts use the measured balance change of the trading account.' if live else
               'Paper receipts use the measured estimate 0.0014670906627479 NEAR per leg.'),
            '* "Slippage" is everything between the market price (Hyperliquid NEAR mid at the quote) and the '
            'guaranteed output, apart from the 1Click fee (20 bps of the input): price impact, the quote\'s spread '
            'and the 10 bps slippage tolerance.',
            '* Display rounding: components are rounded so that they add up exactly to the rounded net; the tax line '
            'is rounded up. Exact values are kept in the session state.',
            '',
            '## Tax note', '',
            'Each $2 trade is far below the TDS threshold, so no TDS is deducted. The 31.2% reserve (30% + 4% cess on '
            'gains; losses neither taxed nor offset) is set aside to pay at ITR filing. This is a conservative '
            'reserve, not tax advice.',
            '',
            '## Events (last 40)', '']
    out += [f'* [{e["at"]}] {e["event"]}: {e["text"]}' for e in state.get('events', [])[-40:]]
    return '\n'.join(out) + '\n'
