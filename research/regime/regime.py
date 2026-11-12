"""Market-regime detector for NEAR: BULL, BEAR or CHOP. Probabilistic trend detection, not prediction.

The frozen strategy is untouched: this module only decides whether a frozen-strategy signal AGREES with the trend
(a LONG in BULL, a SHORT in BEAR). Six parameters, fixed before any backtest and never tuned on test data:

    ema_fast=20, ema_slow=50 (1h closes), slope_bars=1 (EMA20 rising = above its value 1 completed 1h bar earlier),
    atr_period=14 (1h, Wilder), atr_cost_multiple=2 (CHOP when 1h ATR% < 2 x the live round-trip cost),
    confirm_bars=2 (a new regime must hold for 2 consecutive completed 15m bars before the bot switches).

Raw rule on every completed 15m bar (price = that bar's close; only 1h bars that closed by then are used):
    BULL: EMA20 > EMA50, EMA20 rising, price > EMA20
    BEAR: EMA20 < EMA50, EMA20 falling, price < EMA20 (the exact mirror)
    CHOP: anything else, or ATR% < atr_cost_multiple x round-trip cost
The regime is a pure function of the candle history (and the cost input), so a restarted agent recomputes it
instead of trusting saved state. Indicator maths uses Decimal; no floats.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

BULL, BEAR, CHOP = 'BULL', 'BEAR', 'CHOP'
REGIMES = (BULL, BEAR, CHOP)
H1_MS = 3_600_000
M15_MS = 900_000


@dataclass(frozen=True)
class RegimeParams:
    ema_fast: int = 20
    ema_slow: int = 50
    slope_bars: int = 1
    atr_period: int = 14
    atr_cost_multiple: Decimal = Decimal('2')
    confirm_bars: int = 2


DEFAULT_PARAMS = RegimeParams()


@dataclass(frozen=True)
class Bar:
    open_ms: int
    close_ms: int  # exclusive end: open_ms + interval
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class RawRegime:
    regime: str
    reason: str
    at_ms: int
    price: Decimal
    ema_fast: Decimal | None
    ema_fast_prev: Decimal | None
    ema_slow: Decimal | None
    atr_pct: Decimal | None
    cost_frac: Decimal

    def to_dict(self) -> dict[str, Any]:
        f = lambda v: None if v is None else str(v)  # noqa: E731
        return {'regime': self.regime, 'reason': self.reason, 'at_ms': self.at_ms, 'price': str(self.price),
                'ema_fast': f(self.ema_fast), 'ema_fast_prev': f(self.ema_fast_prev), 'ema_slow': f(self.ema_slow),
                'atr_pct': f(self.atr_pct), 'cost_frac': str(self.cost_frac)}


def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """EMA with alpha = 2/(period+1), seeded by the SMA of the first `period` values; None before that."""
    out: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = Decimal(2) / Decimal(period + 1)
    e = sum(values[:period], Decimal(0)) / period
    out[period - 1] = e
    for i in range(period, len(values)):
        e = e + alpha * (values[i] - e)
        out[i] = e
    return out


def atr_series(bars: Sequence[Bar], period: int) -> list[Decimal | None]:
    """Wilder ATR: SMA of the first `period` true ranges, then (prev*(n-1) + tr)/n. None before that."""
    out: list[Decimal | None] = [None] * len(bars)
    trs: list[Decimal] = []
    for i, b in enumerate(bars):
        tr = b.high - b.low if i == 0 else max(b.high - b.low, abs(b.high - bars[i - 1].close),
                                              abs(b.low - bars[i - 1].close))
        trs.append(tr)
    if len(bars) < period:
        return out
    a = sum(trs[:period], Decimal(0)) / period
    out[period - 1] = a
    for i in range(period, len(bars)):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def aggregate(bars_1m: Sequence[Bar], interval_ms: int) -> list[Bar]:
    """Complete, UTC-aligned bars of `interval_ms` built from contiguous 1m bars. A bucket with a missing minute
    is dropped (never filled)."""
    out: list[Bar] = []
    need = interval_ms // 60_000
    bucket: list[Bar] = []
    for b in bars_1m:
        start = b.open_ms - b.open_ms % interval_ms
        if bucket and bucket[0].open_ms - bucket[0].open_ms % interval_ms != start:
            bucket = []
        if not bucket and b.open_ms != start:
            continue  # joined mid-bucket: wait for the next aligned bucket
        if bucket and b.open_ms != bucket[-1].open_ms + 60_000:
            bucket = []
            continue
        bucket.append(b)
        if len(bucket) == need:
            out.append(Bar(start, start + interval_ms, bucket[0].open, max(x.high for x in bucket),
                           min(x.low for x in bucket), bucket[-1].close))
            bucket = []
    return out


class H1Indicators:
    """EMA fast/slow and ATR on completed 1h bars, looked up by time without look-ahead."""

    def __init__(self, bars_1h: Sequence[Bar], params: RegimeParams = DEFAULT_PARAMS):
        self.bars = list(bars_1h)
        closes = [b.close for b in self.bars]
        self.fast = ema_series(closes, params.ema_fast)
        self.slow = ema_series(closes, params.ema_slow)
        self.atr = atr_series(self.bars, params.atr_period)
        self.params = params
        self._close_ms = [b.close_ms for b in self.bars]

    def last_index_at(self, at_ms: int) -> int:
        """Index of the newest 1h bar that had closed by `at_ms` (-1 if none)."""
        import bisect  # noqa: PLC0415
        return bisect.bisect_right(self._close_ms, at_ms) - 1


def classify(h1: H1Indicators, at_ms: int, price: Decimal, cost_frac: Decimal) -> RawRegime:
    """The raw (unconfirmed) regime at `at_ms` with the market at `price`."""
    p = h1.params
    i = h1.last_index_at(at_ms)
    j = i - p.slope_bars
    fast = h1.fast[i] if i >= 0 else None
    fast_prev = h1.fast[j] if j >= 0 else None
    slow = h1.slow[i] if i >= 0 else None
    atr = h1.atr[i] if i >= 0 else None
    atr_pct = atr / price if atr is not None else None
    mk = lambda regime, reason: RawRegime(regime, reason, at_ms, price, fast, fast_prev, slow, atr_pct,  # noqa: E731
                                         cost_frac)
    if fast is None or fast_prev is None or slow is None or atr_pct is None:
        return mk(CHOP, 'WARMUP')
    if atr_pct < p.atr_cost_multiple * cost_frac:
        return mk(CHOP, 'ATR_BELOW_COST')
    if fast > slow and fast > fast_prev and price > fast:
        return mk(BULL, 'TREND_UP')
    if fast < slow and fast < fast_prev and price < fast:
        return mk(BEAR, 'TREND_DOWN')
    return mk(CHOP, 'NO_TREND')


class Hysteresis:
    """Confirmed regime: switches only after `confirm_bars` consecutive raw readings of the new regime."""

    def __init__(self, confirm_bars: int = DEFAULT_PARAMS.confirm_bars, initial: str = CHOP):
        if confirm_bars < 1:
            raise ValueError('confirm_bars must be >= 1')
        self.confirm_bars = confirm_bars
        self.confirmed = initial
        self.candidate: str | None = None
        self.count = 0

    def update(self, raw: str) -> str:
        if raw not in REGIMES:
            raise ValueError(f'unknown regime {raw}')
        if raw == self.confirmed:
            self.candidate, self.count = None, 0
        elif raw == self.candidate:
            self.count += 1
        else:
            self.candidate, self.count = raw, 1
        if self.candidate is not None and self.count >= self.confirm_bars:
            self.confirmed, self.candidate, self.count = self.candidate, None, 0
        return self.confirmed


@dataclass(frozen=True)
class RegimePoint:
    at_ms: int  # close time of the 15m bar: the regime applies from here until the next 15m close
    confirmed: str
    raw: RawRegime


def regime_series(bars_15m: Sequence[Bar], h1: H1Indicators, cost_frac: Callable[[int], Decimal] | Decimal,
                  params: RegimeParams = DEFAULT_PARAMS) -> list[RegimePoint]:
    """Confirmed regime after every completed 15m bar. `cost_frac` is the round-trip cost (fraction) to use at
    each bar close, or a constant."""
    hyst = Hysteresis(params.confirm_bars)
    out = []
    for b in bars_15m:
        c = cost_frac(b.close_ms) if callable(cost_frac) else cost_frac
        raw = classify(h1, b.close_ms, b.close, c)
        out.append(RegimePoint(b.close_ms, hyst.update(raw.regime), raw))
    return out


def regime_at(series: Sequence[RegimePoint], at_ms: int) -> RegimePoint | None:
    """The confirmed regime in force at `at_ms` (from the newest 15m close <= at_ms)."""
    import bisect  # noqa: PLC0415
    i = bisect.bisect_right([p.at_ms for p in series], at_ms) - 1
    return series[i] if i >= 0 else None


def agrees(direction: str, regime: str) -> bool:
    """Entry gate: only a LONG in BULL or a SHORT in BEAR opens a round. Everything else is SKIPPED_REGIME."""
    return (direction == 'LONG' and regime == BULL) or (direction == 'SHORT' and regime == BEAR)


def bars_from_hl(rows: Sequence[dict[str, Any]], interval_ms: int, now_ms: int) -> list[Bar]:
    """Hyperliquid candleSnapshot rows -> completed Bars (close time <= now), sorted and de-duplicated."""
    by_open = {}
    for r in rows:
        t = int(r['t'])
        if t + interval_ms <= now_ms:
            by_open[t] = Bar(t, t + interval_ms, Decimal(str(r['o'])), Decimal(str(r['h'])), Decimal(str(r['l'])),
                             Decimal(str(r['c'])))
    return [by_open[k] for k in sorted(by_open)]


def current_regime(rows_15m: Sequence[dict[str, Any]], rows_1h: Sequence[dict[str, Any]], now_ms: int,
                   cost_frac: Decimal, params: RegimeParams = DEFAULT_PARAMS) -> RegimePoint | None:
    """Live: the confirmed regime now, replayed over the fetched history with today's cost (deterministic)."""
    s = regime_series(bars_from_hl(rows_15m, M15_MS, now_ms), H1Indicators(bars_from_hl(rows_1h, H1_MS, now_ms),
                                                                           params), cost_frac, params)
    return s[-1] if s else None
