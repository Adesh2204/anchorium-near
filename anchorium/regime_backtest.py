"""Offline backtest of the regime-aware, profit-only round trader on recorded public NEAR 1m candles.

Inputs (all recorded, all public):
* Binance USD-M NEARUSDT 1m klines (data.binance.vision daily archives, SHA256 checked). Hyperliquid's public API
  keeps only ~3 days of 1m candles, so it cannot supply 60 days; its 15m/1h history is used as a cross-check.
* Frozen-strategy signals: `signals.evaluate_latest` (the live agent's exact path, 26 h window) on every minute.
* Leg costs: real 1Click DRY quotes for $2 in each direction, measured on the guaranteed output (`minAmountOut`)
  against the Hyperliquid mid (fee 20 bps + slippage). Bootstrapped per quote with a fixed seed.
* Gas per leg: the measured mainnet `ft_transfer` (0.000217 NEAR) + the 1Click deposit address's USDC/wNEAR
  storage registration (0.00125 NEAR, `storage_balance_bounds`), as in the live exit floor.
* USD/INR: ECB reference rates (Frankfurter), the rate of each day (previous business day on weekends).

Fixed before the run (never tuned here): the regime's six parameters (`regime.DEFAULT_PARAMS`), the cost filter
1.3%, PROFIT_TARGET_INR 1.00, $2 per trade, 1 open round, 3 rounds per IST day, tax 31.2% per disposal.
"""
from __future__ import annotations

import bisect
import json
import random
import statistics
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from anchorium.agent import ledger as L
from anchorium.agent import regime as R

IST = timezone(timedelta(hours=5, minutes=30))
MIN_MS = 60_000
GAS_LEG_NEAR = Decimal('0.0002170906627479') + Decimal('0.00125')
COST_FILTER = Decimal('0.013')
ORDER_USD = Decimal('2.00')
MIN_ORDER_USD = Decimal('1.00')
MAX_ROUNDS_PER_DAY = 3
START_INR = Decimal('500')


# ----- data -----

def load_binance(directory: Path) -> list[dict[str, Any]]:
    """Binance kline CSVs (inside the daily zips) -> Hyperliquid-shaped rows (t, T, s, i, o, h, l, c, v)."""
    rows: list[dict[str, Any]] = []
    for f in sorted(Path(directory).glob('NEARUSDT-1m-*.zip')):
        with zipfile.ZipFile(f) as z:
            for line in z.read(z.namelist()[0]).decode().splitlines():
                p = line.split(',')
                if not p[0].isdigit():
                    continue  # header
                rows.append({'t': int(p[0]), 'T': int(p[6]), 's': 'NEAR', 'i': '1m', 'o': p[1], 'h': p[2],
                             'l': p[3], 'c': p[4], 'v': p[5]})
    rows.sort(key=lambda r: r['t'])
    for a, b in zip(rows, rows[1:]):
        if b['t'] - a['t'] != MIN_MS:
            raise ValueError(f'gap or duplicate in candles at {a["t"]} -> {b["t"]}')
    return rows


def bars_1m(rows: Sequence[dict[str, Any]]) -> list[R.Bar]:
    return [R.Bar(r['t'], r['t'] + MIN_MS, Decimal(r['o']), Decimal(r['h']), Decimal(r['l']), Decimal(r['c']))
            for r in rows]


_ROWS: list[dict[str, Any]] = []


def _init(rows):
    global _ROWS
    _ROWS = rows


def _signals_for(span: tuple[int, int]) -> list[dict[str, Any]]:
    from anchorium.agent import signals as sig  # noqa: PLC0415
    lo, hi = span
    out = []
    need = sig.LOOKBACK_MS // MIN_MS + 2
    for i in range(lo, hi):
        now_ms = _ROWS[i]['T'] + 1  # just after bar i closed
        s, _ = sig.evaluate_latest(_ROWS[max(0, i - need):i + 1], 'NEAR', now_ms)
        if s is not None:
            out.append({'bar_open_ms': s.bar_open_ms, 'direction': s.direction, 'signal_type': s.signal_type,
                        'close': s.close})
    return out


def compute_signals(rows: list[dict[str, Any]], start_ms: int, end_ms: int, workers: int = 8) -> list[dict]:
    """Frozen-strategy signal on every bar opened in [start_ms, end_ms), evaluated exactly as the live agent does."""
    from multiprocessing import get_context  # noqa: PLC0415
    idx = [i for i, r in enumerate(rows) if start_ms <= r['t'] < end_ms]
    lo, hi = idx[0], idx[-1] + 1
    step = 2000
    spans = [(a, min(a + step, hi)) for a in range(lo, hi, step)]
    with get_context('spawn').Pool(workers, initializer=_init, initargs=(rows,)) as pool:
        parts = pool.map(_signals_for, spans)
    return [s for p in parts for s in p]


@dataclass(frozen=True)
class CostModel:
    """Worst-case leg costs (fractions of the input's market value), from real dry quotes."""
    buy: tuple[Decimal, ...]   # USDC -> wNEAR
    sell: tuple[Decimal, ...]  # wNEAR -> USDC
    fee: Decimal = Decimal('0.0020')
    fixed: Decimal | None = None  # stress mode: this cost for every quote

    @classmethod
    def from_samples(cls, path: Path, fixed_quantile: float | None = None) -> CostModel:
        data = json.loads(Path(path).read_text())
        ok = [s for s in data['samples'] if 'error' not in s]
        buy = tuple(Decimal(s['buy']['leg_cost_bps']) / 10_000 for s in ok)
        sell = tuple(Decimal(s['sell']['leg_cost_bps']) / 10_000 for s in ok)
        fee = Decimal(ok[0]['buy']['app_fee_bps']) / 10_000
        fixed = None
        if fixed_quantile is not None:
            both = sorted(buy + sell)
            fixed = both[min(len(both) - 1, int(fixed_quantile * len(both)))]
        return cls(buy, sell, fee, fixed)

    def draw(self, rng: random.Random, side: str) -> Decimal:
        if self.fixed is not None:
            return self.fixed
        pool = self.buy if side == 'buy' else self.sell
        return pool[rng.randrange(len(pool))]

    def median(self, side: str) -> Decimal:
        return Decimal(statistics.median(self.buy if side == 'buy' else self.sell))

    def median_round_trip(self) -> Decimal:
        return self.median('buy') + self.median('sell')


class FX:
    def __init__(self, daily: dict[str, str]):
        self.days = sorted(daily)
        self.rates = {d: Decimal(str(v)) for d, v in daily.items()}

    def at(self, ms: int) -> Decimal:
        day = datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()
        i = bisect.bisect_right(self.days, day) - 1
        if i < 0:
            raise ValueError(f'no FX rate on or before {day}')
        return self.rates[self.days[i]]


# ----- simulation -----

VARIANTS = {
    'A': 'current long-only (every LONG signal; no regime)',
    'B': 'regime long-only (LONG in BULL)',
    'C': 'regime long + bear (LONG in BULL, SHORT in BEAR)',
}


@dataclass
class RoundRec:
    n: int
    kind: str
    open_ms: int
    open_swap: L.Swap
    open_tax_inr: Decimal
    open_signal: str
    close_ms: int | None = None
    close_swap: L.Swap | None = None
    close_tax_inr: Decimal | None = None
    net_after_tax_inr: Decimal | None = None
    pnl_inr: Decimal | None = None
    checks: int = 0
    rechecks_failed: int = 0
    unrealized: dict[str, Any] | None = None


@dataclass
class Result:
    variant: str
    start_ms: int
    end_ms: int
    seed: int
    rounds: list[RoundRec] = field(default_factory=list)
    skips: dict[str, int] = field(default_factory=dict)
    equity: list[tuple[int, Decimal, Decimal]] = field(default_factory=list)  # (ms, equity_inr, hold_inr)
    minutes: int = 0
    minutes_open: int = 0
    gas_inr: Decimal = Decimal(0)
    tax_inr: Decimal = Decimal(0)
    end_wallet_inr: Decimal = Decimal(0)
    end_hold_inr: Decimal = Decimal(0)
    end_usdc: Decimal = Decimal(0)
    end_wnear: Decimal = Decimal(0)
    end_latent_tax_inr: Decimal = Decimal(0)  # tax if every remaining lot were sold at market at the end
    hold_latent_tax_inr: Decimal = Decimal(0)

    def skip(self, why: str) -> None:
        self.skips[why] = self.skips.get(why, 0) + 1


def _ist_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, IST).date().isoformat()


def simulate(variant: str, rows: Sequence[dict[str, Any]], signals: Iterable[dict[str, Any]],
             regimes: Sequence[R.RegimePoint], fx: FX, costs: CostModel, start_ms: int, end_ms: int,
             seed: int = 0, target_inr: Decimal = L.DEFAULT_PROFIT_TARGET_INR) -> Result:
    """One variant over [start_ms, end_ms). Decisions are taken at each 1m bar's close with that bar's close as
    the market price; quotes are the market price less a drawn worst-case leg cost. A fill is the guaranteed
    amount of the quote that passed the fresh re-check."""
    with localcontext() as ctx:
        ctx.prec = L.PREC
        rng = random.Random(seed)
        res = Result(variant, start_ms, end_ms, seed)
        by_open = {s['bar_open_ms']: s for s in signals}
        reg_ms = [p.at_ms for p in regimes]
        i0 = next(i for i, r in enumerate(rows) if r['t'] == start_ms)
        p0 = Decimal(rows[i0 - 1]['c'])
        fx0 = fx.at(start_ms)
        cap_usd = START_INR / fx0
        led = L.Ledger()
        u0 = L.units(L.USDC, cap_usd / 2)
        n0 = L.units(L.WNEAR, cap_usd / 2 / p0)
        led.acquire(L.USDC, u0, L.value_usd(L.USDC, u0, p0) * fx0, start_ms, 'start')
        led.acquire(L.WNEAR, n0, L.value_usd(L.WNEAR, n0, p0) * fx0, start_ms, 'start')
        open_r: RoundRec | None = None
        per_day: dict[str, int] = {}
        gas_inr = Decimal(0)
        i = i0
        while i < len(rows) and rows[i]['t'] < end_ms:
            r = rows[i]
            t = r['T'] + 1
            p = Decimal(r['c'])
            rate = fx.at(t)
            res.minutes += 1
            if open_r is not None:
                res.minutes_open += 1
                cur: RoundRec = open_r
                kind = cur.kind
                side = 'sell' if kind == L.BULL_ROUND else 'buy'

                def quote_units(c: Decimal) -> int:
                    if kind == L.BULL_ROUND:  # sell the round's wNEAR for USDC
                        return L.units(L.USDC, L.human(L.WNEAR, cur.open_swap.bought_units) * p * (1 - c))
                    return L.units(L.WNEAR, L.human(L.USDC, cur.open_swap.bought_units) / p * (1 - c))

                def check(c: Decimal) -> tuple[int, L.CloseCheck]:
                    got = quote_units(c)
                    return got, L.close_check(led, kind, cur.open_swap, cur.open_tax_inr, got, p, rate,
                                              GAS_LEG_NEAR, target_inr, t)
                open_r.checks += 1
                _, first = check(costs.draw(rng, side))
                if first.clears:
                    got, fresh = check(costs.draw(rng, side))  # fresh quote right before the deposit
                    if fresh.clears:
                        sw = L.Swap(L.ROUND_OTHER[kind], open_r.open_swap.bought_units, L.ROUND_BASE[kind], got, p,
                                    rate, GAS_LEG_NEAR, t, f'sim-close-{open_r.n}')
                        d = L.apply_swap(led, sw)
                        gas_inr += sw.gas_usd() * rate
                        open_r.close_ms, open_r.close_swap = t, sw
                        open_r.close_tax_inr = d.tax_reserve_inr
                        open_r.pnl_inr = fresh.pnl_inr
                        open_r.net_after_tax_inr = fresh.pnl_inr - open_r.open_tax_inr - d.tax_reserve_inr
                        if open_r.net_after_tax_inr != fresh.net_after_tax_inr:
                            raise AssertionError('booked close differs from the checked close')
                        open_r = None
                    else:
                        open_r.rechecks_failed += 1
            elif r['t'] in by_open:
                s = by_open[r['t']]
                rp = R.regime_at(regimes, t) if reg_ms else None
                regime = rp.confirmed if rp else R.CHOP
                kind = None
                if variant == 'A':
                    kind = L.BULL_ROUND if s['direction'] == 'LONG' else None
                    why = 'SKIPPED_SHORT_LONG_ONLY'
                elif variant == 'B':
                    kind = L.BULL_ROUND if R.agrees(s['direction'], regime) and s['direction'] == 'LONG' else None
                    why = 'SKIPPED_REGIME' if s['direction'] == 'LONG' else 'SKIPPED_SHORT_LONG_ONLY'
                else:
                    if R.agrees(s['direction'], regime):
                        kind = L.BULL_ROUND if s['direction'] == 'LONG' else L.BEAR_ROUND
                    why = 'SKIPPED_REGIME'
                if kind is None:
                    res.skip(why)
                elif per_day.get(_ist_day(t), 0) >= MAX_ROUNDS_PER_DAY:
                    res.skip('SKIPPED_ROUND_CAP')
                else:
                    side_open = 'buy' if kind == L.BULL_ROUND else 'sell'
                    side_close = 'sell' if kind == L.BULL_ROUND else 'buy'
                    c = costs.draw(rng, side_open)
                    if c + costs.median(side_close) > COST_FILTER:
                        res.skip('SKIPPED_COST')
                    else:
                        base = L.ROUND_BASE[kind]
                        avail_usd = L.value_usd(base, led.balance(base), p)
                        usd = min(ORDER_USD, avail_usd * Decimal('0.98'))
                        if usd < MIN_ORDER_USD:
                            res.skip('SKIPPED_BALANCE')
                        else:
                            if kind == L.BULL_ROUND:
                                sold = L.units(L.USDC, usd)
                                got = L.units(L.WNEAR, L.human(L.USDC, sold) / p * (1 - c))
                            else:
                                sold = L.units(L.WNEAR, usd / p)
                                got = L.units(L.USDC, L.human(L.WNEAR, sold) * p * (1 - c))
                            n = len(res.rounds) + 1
                            sw = L.Swap(base, sold, L.ROUND_OTHER[kind], got, p, rate, GAS_LEG_NEAR, t, f'sim-open-{n}')
                            d = L.apply_swap(led, sw)
                            gas_inr += sw.gas_usd() * rate
                            open_r = RoundRec(n, kind, t, sw, d.tax_reserve_inr, s['signal_type'])
                            res.rounds.append(open_r)
                            per_day[_ist_day(t)] = per_day.get(_ist_day(t), 0) + 1
            if (t - start_ms) % (15 * MIN_MS) == 0:
                tax = sum((d.tax_reserve_inr for d in led.disposals), Decimal(0))
                hold = (L.value_usd(L.USDC, u0, p) + L.value_usd(L.WNEAR, n0, p)) * rate
                res.equity.append((t, L.wallet_inr(led, p, rate) - tax - gas_inr, hold))
            i += 1
        last = rows[i - 1]
        p, t = Decimal(last['c']), last['T'] + 1
        rate = fx.at(t)
        if open_r is not None:  # still open: value with a median-cost worst-case quote, report unrealized
            kind = open_r.kind
            c = costs.median('sell' if kind == L.BULL_ROUND else 'buy')
            if kind == L.BULL_ROUND:
                got = L.units(L.USDC, L.human(L.WNEAR, open_r.open_swap.bought_units) * p * (1 - c))
            else:
                got = L.units(L.WNEAR, L.human(L.USDC, open_r.open_swap.bought_units) / p * (1 - c))
            cc = L.close_check(led, kind, open_r.open_swap, open_r.open_tax_inr, got, p, rate, GAS_LEG_NEAR,
                               target_inr, t)
            open_r.unrealized = cc.to_dict()
        res.tax_inr = sum((d.tax_reserve_inr for d in led.disposals), Decimal(0))
        res.gas_inr = gas_inr
        res.end_wallet_inr = L.wallet_inr(led, p, rate)
        res.end_hold_inr = (L.value_usd(L.USDC, u0, p) + L.value_usd(L.WNEAR, n0, p)) * rate
        res.end_usdc, res.end_wnear = L.human(L.USDC, led.balance(L.USDC)), L.human(L.WNEAR, led.balance(L.WNEAR))
        res.end_latent_tax_inr = latent_tax(led, p, rate)
        hold_led = L.Ledger()
        hold_led.acquire(L.USDC, u0, L.value_usd(L.USDC, u0, p0) * fx0, start_ms, 'start')
        hold_led.acquire(L.WNEAR, n0, L.value_usd(L.WNEAR, n0, p0) * fx0, start_ms, 'start')
        res.hold_latent_tax_inr = latent_tax(hold_led, p, rate)
        return res


def latent_tax(led: L.Ledger, near_usd: Decimal, usd_inr: Decimal) -> Decimal:
    """Sum of per-disposal tax if every remaining lot were sold at market now (losses not offset)."""
    total = Decimal(0)
    for asset in (L.USDC, L.WNEAR):
        for lot in led.lots[asset]:
            total += L.tax_on(L.value_usd(asset, lot.units, near_usd) * usd_inr - lot.cost_inr)
    return total


def metrics(res: Result) -> dict[str, Any]:
    closed = [r for r in res.rounds if r.close_ms is not None]
    opened = res.rounds
    q = lambda v: Decimal(v).quantize(Decimal('0.01'))  # noqa: E731  (display only)
    peak, mdd, xpeak, xmdd = None, Decimal(0), None, Decimal(0)
    for _, e, h in res.equity:
        peak = e if peak is None or e > peak else peak
        mdd = max(mdd, peak - e)
        x = e - h
        xpeak = x if xpeak is None or x > xpeak else xpeak
        xmdd = max(xmdd, xpeak - x)
    peak_val = max((e for _, e, _ in res.equity), default=START_INR)
    equity_end = res.end_wallet_inr - res.tax_inr - res.gas_inr
    holds = [(r.close_ms - r.open_ms) / 3_600_000 for r in closed if r.close_ms is not None]
    open_end = [r for r in opened if r.close_ms is None]
    return {
        'variant': res.variant, 'rounds_opened': len(opened), 'rounds_closed': len(closed),
        'bull_closed': sum(r.kind == L.BULL_ROUND for r in closed), 'bear_closed': sum(r.kind == L.BEAR_ROUND for r in closed),
        'open_at_end': len(open_end),
        'win_pct': None if not opened else q(Decimal(100) * len(closed) / len(opened)),
        'realized_net_after_tax_inr': q(sum((r.net_after_tax_inr or Decimal(0) for r in closed), Decimal(0))),
        'open_round_unrealized_inr': [q(Decimal(r.unrealized['net_after_tax_inr'])) for r in open_end if r.unrealized],
        'tax_reserve_inr': q(res.tax_inr), 'gas_inr': q(res.gas_inr),
        'end_equity_after_tax_inr': q(equity_end),
        'vs_start_inr': q(equity_end - START_INR), 'vs_hold_inr': q(equity_end - res.end_hold_inr),
        'vs_hold_liquidation_inr': q(equity_end - res.end_latent_tax_inr
                                     - (res.end_hold_inr - res.hold_latent_tax_inr)),
        'latent_tax_inr': q(res.end_latent_tax_inr), 'hold_latent_tax_inr': q(res.hold_latent_tax_inr),
        'hold_vs_start_inr': q(res.end_hold_inr - START_INR),
        'max_drawdown_inr': q(mdd), 'max_drawdown_pct': q(Decimal(100) * mdd / peak_val),
        'max_drawdown_vs_hold_inr': q(xmdd),
        'avg_hold_h': None if not holds else round(statistics.mean(holds), 2),
        'max_hold_h': None if not holds else round(max(holds), 2),
        'pct_time_in_open_round': q(Decimal(100) * res.minutes_open / res.minutes),
        'skips': dict(sorted(res.skips.items())),
        'end_wallet': {'usdc': str(res.end_usdc.quantize(Decimal('0.000001'))),
                       'wnear': str(res.end_wnear.quantize(Decimal('0.000001')))},
    }


def round_rows(res: Result) -> list[dict[str, Any]]:
    out = []
    for r in res.rounds:
        out.append({'n': r.n, 'kind': r.kind, 'signal': r.open_signal,
                    'open_ist': datetime.fromtimestamp(r.open_ms / 1000, IST).strftime('%Y-%m-%d %H:%M'),
                    'open_price': str(r.open_swap.near_usd),
                    'close_ist': None if r.close_ms is None else
                    datetime.fromtimestamp(r.close_ms / 1000, IST).strftime('%Y-%m-%d %H:%M'),
                    'close_price': None if r.close_swap is None else str(r.close_swap.near_usd),
                    'hold_h': None if r.close_ms is None else round((r.close_ms - r.open_ms) / 3_600_000, 2),
                    'tax_open_inr': str(Decimal(r.open_tax_inr).quantize(Decimal('0.0001'))),
                    'tax_close_inr': None if r.close_tax_inr is None else
                    str(r.close_tax_inr.quantize(Decimal('0.0001'))),
                    'net_after_tax_inr': None if r.net_after_tax_inr is None else
                    str(r.net_after_tax_inr.quantize(Decimal('0.0001'))),
                    'checks': r.checks, 'rechecks_failed': r.rechecks_failed, 'unrealized': r.unrealized})
    return out
