"""Regime backtest: variants A/B/C over 60 recorded days (days 1-30 sanity, days 31-60 out-of-sample).

    python research/regime/backtest_regime.py            # downloads/verifies data, computes signals (cached), runs
    python research/regime/backtest_regime.py --seeds 200

Writes docs/data/regime_backtest_results.json (every number in docs/regime_backtest.md comes from it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.regime import regime as R  # noqa: E402
from research.regime.engine import (  # noqa: E402
    VARIANTS, CostModel, FX, bars_1m, compute_signals, load_binance, metrics, round_rows, simulate)

DATA = ROOT / 'var' / 'backtest' / 'binance'
URL = 'https://data.binance.vision/data/futures/um/daily/klines/NEARUSDT/1m/NEARUSDT-1m-{d}.zip'
FIRST_TEST_DAY = date(2026, 9, 12)
DAYS = 60
WARMUP_DAYS = 4
QUOTES = ROOT / 'docs' / 'data' / 'quote_costs_2026-11-12.json'
FX_FILE = ROOT / 'docs' / 'data' / 'fx_usd_inr_ecb.json'
OUT = ROOT / 'docs' / 'data' / 'regime_backtest_results.json'
MANIFEST = ROOT / 'docs' / 'data' / 'binance_near_1m_sha256.txt'
HL = 'https://api.hyperliquid.xyz/info'


def ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def download() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    lines = []
    d = FIRST_TEST_DAY - timedelta(days=WARMUP_DAYS)
    while d < FIRST_TEST_DAY + timedelta(days=DAYS):
        f = DATA / f'NEARUSDT-1m-{d}.zip'
        for suf in ('', '.CHECKSUM'):
            p = Path(str(f) + suf)
            if not p.exists():
                subprocess.run(['curl', '-sf', '-o', str(p), URL.format(d=d) + suf], check=True)
        want = Path(str(f) + '.CHECKSUM').read_text().split()[0]
        got = hashlib.sha256(f.read_bytes()).hexdigest()
        if want != got:
            raise SystemExit(f'checksum mismatch {f.name}')
        lines.append(f'{got}  {f.name}')
        d += timedelta(days=1)
    MANIFEST.write_text('\n'.join(lines) + '\n')


def fx_rates() -> dict[str, str]:
    if not FX_FILE.exists():
        start = FIRST_TEST_DAY - timedelta(days=WARMUP_DAYS + 7)
        end = FIRST_TEST_DAY + timedelta(days=DAYS)
        url = f'https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols=INR'
        body = httpx.get(url, timeout=30).raise_for_status().json()
        FX_FILE.write_text(json.dumps({'source': 'ECB reference rates via Frankfurter', 'url': url,
                                       'rates': {d: str(v['INR']) for d, v in body['rates'].items()}}, indent=1))
    return json.loads(FX_FILE.read_text())['rates']


def signals(rows, workers: int) -> list[dict]:
    key = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()[:16]
    cache = ROOT / 'var' / 'backtest' / f'signals_{key}.json'
    if cache.exists():
        return json.loads(cache.read_text())
    t = time.time()
    out = compute_signals(rows, ms(FIRST_TEST_DAY), ms(FIRST_TEST_DAY + timedelta(days=DAYS)), workers)
    print(f'signals: {len(out)} in {time.time() - t:.0f}s', flush=True)
    cache.write_text(json.dumps(out))
    (ROOT / 'docs' / 'data' / 'regime_backtest_signals.json').write_text(json.dumps(
        {'source': 'frozen strategy via signals.evaluate_latest on Binance USD-M NEARUSDT 1m', 'data_sha': key,
         'signals': out}, indent=0))
    return out


def hl_crosscheck(rows, cost) -> dict:
    """Regime agreement: Binance-derived vs Hyperliquid-derived 15m/1h bars over the shared history."""
    now = int(time.time() * 1000)
    get = lambda iv, start: httpx.post(HL, json={'type': 'candleSnapshot', 'req': {  # noqa: E731
        'coin': 'NEAR', 'interval': iv, 'startTime': start, 'endTime': now}}, timeout=30).json()
    h15 = R.bars_from_hl(get('15m', now - 60 * 86_400_000), R.M15_MS, now)
    h1 = R.bars_from_hl(get('1h', now - 80 * 86_400_000), R.H1_MS, now)
    hl = {p.at_ms: p.confirmed for p in R.regime_series(h15, R.H1Indicators(h1), cost)}
    b1 = bars_1m(rows)
    bn = {p.at_ms: p.confirmed for p in R.regime_series(R.aggregate(b1, R.M15_MS),
                                                        R.H1Indicators(R.aggregate(b1, R.H1_MS)), cost)}
    lo = max(min(hl), min(bn)) + 3 * 86_400_000  # skip the first 3 days of HL 15m (hysteresis/EMA warm-up)
    common = [t for t in bn if t in hl and t >= lo]
    agree = sum(hl[t] == bn[t] for t in common)
    closes_hl = {b.close_ms: b.close for b in h15}
    basis = [abs(closes_hl[b.close_ms] / b.close - 1) for b in R.aggregate(b1, R.M15_MS) if b.close_ms in closes_hl]
    return {'bars_compared': len(common), 'agreement_pct': round(100 * agree / len(common), 2),
            'from': datetime.fromtimestamp(common[0] / 1000, timezone.utc).isoformat(),
            'to': datetime.fromtimestamp(common[-1] / 1000, timezone.utc).isoformat(),
            'median_abs_close_basis_bps': round(float(statistics.median(basis)) * 10_000, 2)}


def hl_signal_crosscheck(rows) -> dict:
    """Frozen-strategy signals on 10 Nov (UTC) from Hyperliquid 1m vs Binance 1m (Hyperliquid keeps ~3.5 days)."""
    from anchorium.agent import signals as sig  # noqa: PLC0415
    day = ms(date(2026, 11, 10))
    hl = httpx.post(HL, json={'type': 'candleSnapshot', 'req': {'coin': 'NEAR', 'interval': '1m',
                                                                'startTime': day - 27 * 3_600_000,
                                                                'endTime': day + 86_400_000}}, timeout=30).json()
    out = {}
    for name, src in (('hyperliquid', hl), ('binance', [r for r in rows if day - 27 * 3_600_000 <= r['t'] < day + 86_400_000])):
        found = {}
        for m in range(1440):
            now = day + (m + 1) * 60_000
            s, _ = sig.evaluate_latest(src, 'NEAR', now)
            if s is not None:
                found[s.bar_open_ms] = s.direction
        out[name] = found
    a, b = out['hyperliquid'], out['binance']
    same = sum(1 for t in a if b.get(t) == a[t])
    near = sum(1 for t in a if any(b.get(t + k * 60_000) == a[t] for k in range(-3, 4)))
    return {'day_utc': '2026-11-10', 'hyperliquid_signals': len(a), 'binance_signals': len(b),
            'same_bar_same_direction': same, 'within_3_bars_same_direction': near,
            'hl_first_bar': min(int(r['t']) for r in hl)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=200)
    ap.add_argument('--workers', type=int, default=9)
    a = ap.parse_args()
    download()
    rows = load_binance(DATA)
    sigs = signals(rows, a.workers)
    fx = FX(fx_rates())
    costs = CostModel.from_samples(QUOTES)
    stress = CostModel.from_samples(QUOTES, fixed_quantile=0.9)
    rt = costs.median_round_trip()
    b1 = bars_1m(rows)
    regimes = R.regime_series(R.aggregate(b1, R.M15_MS), R.H1Indicators(R.aggregate(b1, R.H1_MS)), rt)
    halves = {'days_1_30_sanity': (ms(FIRST_TEST_DAY), ms(FIRST_TEST_DAY + timedelta(days=30))),
              'days_31_60_oos': (ms(FIRST_TEST_DAY + timedelta(days=30)), ms(FIRST_TEST_DAY + timedelta(days=DAYS)))}
    out = {'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
           'params': {'regime': {k: str(v) for k, v in R.DEFAULT_PARAMS.__dict__.items()},
                      'cost_filter': '0.013', 'profit_target_inr': '1.00', 'order_usd': '2.00',
                      'max_rounds_per_ist_day': 3, 'gas_leg_near': '0.0014670906627479',
                      'median_round_trip_cost': str(rt), 'stress_fixed_leg_cost': str(stress.fixed)},
           'variants': VARIANTS, 'signals_total': len(sigs),
           'signals_by_type': {k: sum(s['signal_type'] == k for s in sigs) for k in sorted({s['signal_type'] for s in sigs})},
           'halves': {}}
    for name, (lo, hi) in halves.items():
        pts = [p for p in regimes if lo < p.at_ms <= hi]
        share = {g: round(100 * sum(p.confirmed == g for p in pts) / len(pts), 1) for g in R.REGIMES}
        reasons = {}
        for p in pts:
            reasons[p.raw.reason] = reasons.get(p.raw.reason, 0) + 1
        first, last = Decimal(next(r for r in rows if r['t'] == lo)['o']), Decimal(next(r for r in rows if r['t'] == hi - 60_000)['c'])
        h = {'from': datetime.fromtimestamp(lo / 1000, timezone.utc).date().isoformat(),
             'to_exclusive': datetime.fromtimestamp(hi / 1000, timezone.utc).date().isoformat(),
             'near_open': str(first), 'near_close': str(last),
             'near_change_pct': str(((last / first - 1) * 100).quantize(Decimal('0.01'))),
             'regime_share_pct': share, 'raw_reason_counts': reasons,
             'signals': sum(lo <= s['bar_open_ms'] < hi for s in sigs), 'runs': {}}
        for v in VARIANTS:
            t = time.time()
            res = simulate(v, rows, sigs, regimes, fx, costs, lo, hi, seed=0)
            m = metrics(res)
            m['rounds'] = round_rows(res)
            m['stress_p90_cost'] = {k: metrics(simulate(v, rows, sigs, regimes, fx, stress, lo, hi))[k]
                                    for k in ('rounds_closed', 'open_at_end', 'realized_net_after_tax_inr',
                                              'vs_start_inr', 'vs_hold_inr', 'vs_hold_liquidation_inr')}
            dist = []
            for seed in range(1, a.seeds + 1):
                mm = metrics(simulate(v, rows, sigs, regimes, fx, costs, lo, hi, seed=seed))
                dist.append((mm['vs_hold_inr'], mm['realized_net_after_tax_inr'], mm['rounds_closed'],
                             mm['vs_hold_liquidation_inr']))
            q = lambda xs, f: sorted(xs)[int(f * (len(xs) - 1))]  # noqa: E731
            m['seeds'] = {'n': len(dist),
                          'vs_hold_inr_p10_p50_p90': [str(q([d[0] for d in dist], f)) for f in (0.1, 0.5, 0.9)],
                          'realized_inr_p10_p50_p90': [str(q([d[1] for d in dist], f)) for f in (0.1, 0.5, 0.9)],
                          'rounds_closed_p10_p50_p90': [q([d[2] for d in dist], f) for f in (0.1, 0.5, 0.9)],
                          'vs_hold_liquidation_inr_p10_p50_p90': [str(q([d[3] for d in dist], f))
                                                                  for f in (0.1, 0.5, 0.9)]}
            h['runs'][v] = m
            print(name, v, {k: m[k] for k in ('rounds_opened', 'rounds_closed', 'realized_net_after_tax_inr',
                                              'vs_start_inr', 'vs_hold_inr', 'pct_time_in_open_round')},
                  f'{time.time() - t:.0f}s', flush=True)
        # path dependence: the same half started 0..9 days later (fresh INR 500 wallet each), seed 0
        h['start_offset_sensitivity'] = {}
        for off in range(10):
            s0 = lo + off * 86_400_000
            row = {}
            for v in VARIANTS:
                mm = metrics(simulate(v, rows, sigs, regimes, fx, costs, s0, hi, seed=0))
                row[v] = {k: mm[k] for k in ('rounds_opened', 'rounds_closed', 'realized_net_after_tax_inr',
                                             'open_round_unrealized_inr', 'vs_hold_inr', 'vs_hold_liquidation_inr',
                                             'pct_time_in_open_round')}
            h['start_offset_sensitivity'][f'+{off}d'] = row
        out['halves'][name] = h
    full = {}
    for v in VARIANTS:
        mm = metrics(simulate(v, rows, sigs, regimes, fx, costs, halves['days_1_30_sanity'][0],
                              halves['days_31_60_oos'][1], seed=0))
        mm.pop('rounds', None)
        full[v] = mm
    out['full_60_days_one_wallet'] = full
    try:
        out['hyperliquid_crosscheck'] = hl_crosscheck(rows, rt)
    except Exception as exc:  # noqa: BLE001
        out['hyperliquid_crosscheck'] = {'error': f'{type(exc).__name__}: {exc}'}
    try:
        out['hyperliquid_signal_crosscheck'] = hl_signal_crosscheck(rows)
    except Exception as exc:  # noqa: BLE001
        out['hyperliquid_signal_crosscheck'] = {'error': f'{type(exc).__name__}: {exc}'}
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print('wrote', OUT)


if __name__ == '__main__':
    main()
