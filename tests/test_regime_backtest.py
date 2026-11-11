"""Backtest engine invariants on synthetic data (no network)."""
from decimal import Decimal as D

from anchorium import regime_backtest as B
from anchorium.agent import ledger as L
from anchorium.agent import regime as R

T0 = 1_790_000_000_000 - 1_790_000_000_000 % 86_400_000  # a UTC midnight


def rows_for(prices):
    out = []
    for i, p in enumerate(prices):
        t = T0 - 60_000 + i * 60_000  # one bar before T0 for the start price
        s = str(p)
        out.append({'t': t, 'T': t + 59_999, 's': 'NEAR', 'i': '1m', 'o': s, 'h': s, 'l': s, 'c': s, 'v': '1'})
    return out


COSTS = B.CostModel(buy=(D('0.004'), D('0.006')), sell=(D('0.004'), D('0.006')))
FXR = B.FX({'2026-01-01': '88.5'})


def run(variant, prices, sigs, regime=R.BULL, costs=COSTS, target=D('1.00')):
    rows = rows_for(prices)
    regimes = [R.RegimePoint(T0, regime, None)]
    return B.simulate(variant, rows, sigs, regimes, FXR, costs, T0, rows[-1]['t'] + 60_000, target_inr=target)


def sig(minute, direction):
    return {'bar_open_ms': T0 + minute * 60_000, 'direction': direction,
            'signal_type': 'BREAKOUT_LONG' if direction == 'LONG' else 'BREAKDOWN_SHORT', 'close': '0'}


def test_bull_round_closes_only_at_or_above_target():
    prices = [D(4)] * 5 + [D(4) + D('0.002') * i for i in range(400)]
    res = run('B', prices, [sig(2, 'LONG')])
    closed = [r for r in res.rounds if r.close_ms]
    assert len(closed) == 1
    assert closed[0].net_after_tax_inr >= D('1.00')
    m = B.metrics(res)
    assert m['rounds_closed'] == 1 and m['win_pct'] == D('100.00')


def test_falling_market_never_closes_a_bull_round():
    prices = [D(4)] * 5 + [D(4) - D('0.001') * i for i in range(400)]
    res = run('A', prices, [sig(2, 'LONG')])
    assert res.rounds and res.rounds[0].close_ms is None
    assert D(res.rounds[0].unrealized['net_after_tax_inr']) < 0
    assert res.minutes_open == res.minutes - 3


def test_bear_round_profit_is_extra_near():
    prices = [D(4)] * 5 + [D(4) - D('0.002') * i for i in range(400)]
    res = run('C', prices, [sig(2, 'SHORT')], regime=R.BEAR)
    r = res.rounds[0]
    assert r.kind == L.BEAR_ROUND and r.close_ms
    assert r.close_swap.bought_units > r.open_swap.sold_units  # more wNEAR back than sold
    assert r.close_swap.sold_units == r.open_swap.bought_units  # spends exactly the USDC received


def test_regime_gate_and_long_only():
    prices = [D(4)] * 50
    assert run('B', prices, [sig(2, 'LONG')], regime=R.CHOP).skips == {'SKIPPED_REGIME': 1}
    assert run('B', prices, [sig(2, 'SHORT')], regime=R.BEAR).skips == {'SKIPPED_SHORT_LONG_ONLY': 1}
    assert run('C', prices, [sig(2, 'SHORT')], regime=R.BULL).skips == {'SKIPPED_REGIME': 1}
    assert run('A', prices, [sig(2, 'LONG')], regime=R.BEAR).rounds  # A ignores the regime


def test_cost_filter():
    expensive = B.CostModel(buy=(D('0.009'),), sell=(D('0.005'),))
    res = run('A', [D(4)] * 50, [sig(2, 'LONG')], costs=expensive)
    assert res.skips == {'SKIPPED_COST': 1} and not res.rounds


def test_one_open_round_and_three_per_day():
    up = [D(4)] * 5 + [D(4) + D('0.003') * i for i in range(1400)]
    sigs = [sig(m, 'LONG') for m in range(2, 1400, 20)]
    res = run('A', up, sigs)
    days = {}
    for r in res.rounds:
        days[B._ist_day(r.open_ms)] = days.get(B._ist_day(r.open_ms), 0) + 1
    assert max(days.values()) <= 3
    spans = [(r.open_ms, r.close_ms or 10 ** 18) for r in res.rounds]
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))  # never two open at once
    assert all(D(r.open_swap.sold_units) <= L.units(L.USDC, D('2.00')) for r in res.rounds)  # <= $2 per trade


def test_fill_is_the_guaranteed_amount_of_the_rechecked_quote():
    costs = B.CostModel(buy=(D('0.005'),), sell=(D('0.005'),))
    prices = [D(4)] * 5 + [D(4) + D('0.002') * i for i in range(400)]
    r = run('A', prices, [sig(2, 'LONG')], costs=costs).rounds[0]
    p = r.close_swap.near_usd
    assert r.close_swap.bought_units == L.units(L.USDC, L.human(L.WNEAR, r.open_swap.bought_units) * p * D('0.995'))
