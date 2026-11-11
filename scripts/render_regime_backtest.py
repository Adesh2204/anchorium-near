"""Prints the markdown tables of docs/regime_backtest.md from docs/data/regime_backtest_results.json (no hand-copied
numbers)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
d = json.loads((ROOT / 'docs' / 'data' / 'regime_backtest_results.json').read_text())
NAMES = {'A': 'A long-only (current)', 'B': 'B regime long-only', 'C': 'C regime long + bear'}


def inr(v):
    if v is None:
        return 'n/a'
    v = float(v)
    return f'{"+" if v > 0 else ""}{v:.2f}'


def main_table(h):
    out = ['| Variant | Rounds opened / closed | Win % | Realized net after tax (INR) | Open round at end, unrealized after tax (INR) '
           '| Equity vs start (INR) | vs just holding (INR) | vs holding, liquidation basis (INR) | Max drawdown INR (%) '
           '| Avg hold (h) | Time in an open round | Tax reserve (INR) | Gas (INR) |',
           '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for v, m in h['runs'].items():
        unreal = ', '.join(inr(x) for x in m['open_round_unrealized_inr']) or 'none'
        out.append(f"| {NAMES[v]} | {m['rounds_opened']} / {m['rounds_closed']} | {m['win_pct']}% | "
                   f"{inr(m['realized_net_after_tax_inr'])} | {unreal} | {inr(m['vs_start_inr'])} | "
                   f"{inr(m['vs_hold_inr'])} | {inr(m['vs_hold_liquidation_inr'])} | "
                   f"{m['max_drawdown_inr']} ({m['max_drawdown_pct']}%) | {m['avg_hold_h'] if m['avg_hold_h'] is not None else 'n/a'} | "
                   f"{m['pct_time_in_open_round']}% | {m['tax_reserve_inr']} | {m['gas_inr']} |")
    return '\n'.join(out)


def robustness(h):
    out = ['| Variant | Realized INR p10 / p50 / p90 | vs holding INR p10 / p50 / p90 | Rounds closed p10 / p50 / p90 '
           '| Stress (every quote at the p90 cost): closed, realized, vs holding |', '|---|---|---|---|---|']
    for v, m in h['runs'].items():
        s, st = m['seeds'], m['stress_p90_cost']
        out.append(f"| {v} | {' / '.join(inr(x) for x in s['realized_inr_p10_p50_p90'])} | "
                   f"{' / '.join(inr(x) for x in s['vs_hold_inr_p10_p50_p90'])} | "
                   f"{' / '.join(str(x) for x in s['rounds_closed_p10_p50_p90'])} | "
                   f"{st['rounds_closed']}, {inr(st['realized_net_after_tax_inr'])}, {inr(st['vs_hold_inr'])} |")
    return '\n'.join(out)


def offsets(h):
    out = ['| Start | A: closed, realized, vs hold | B: closed, realized, vs hold | C: closed, realized, vs hold |',
           '|---|---|---|---|']
    for off, row in h['start_offset_sensitivity'].items():
        cells = [f"{r['rounds_closed']}, {inr(r['realized_net_after_tax_inr'])}, {inr(r['vs_hold_inr'])}"
                 for r in row.values()]
        out.append(f'| {off} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(out)


def full():
    out = ['| Variant | Rounds opened / closed (bear) | Realized (INR) | Open at end, unrealized (INR) | vs start | vs holding '
           '| vs holding, liquidation basis | Max drawdown (INR) | Time in an open round |', '|---|---|---|---|---|---|---|---|---|']
    for v, m in d['full_60_days_one_wallet'].items():
        out.append(f"| {NAMES[v]} | {m['rounds_opened']} / {m['rounds_closed']} ({m['bear_closed']}) | "
                   f"{inr(m['realized_net_after_tax_inr'])} | {', '.join(inr(x) for x in m['open_round_unrealized_inr']) or 'none'} | "
                   f"{inr(m['vs_start_inr'])} | {inr(m['vs_hold_inr'])} | {inr(m['vs_hold_liquidation_inr'])} | "
                   f"{m['max_drawdown_inr']} | {m['pct_time_in_open_round']}% |")
    return '\n'.join(out)


if __name__ == '__main__':
    part = sys.argv[1]
    if part == 'full':
        print(full())
    else:
        half, kind = part.split(':')
        h = d['halves'][half]
        print({'main': main_table, 'robust': robustness, 'offsets': offsets}[kind](h))
