from decimal import Decimal as D

import pytest

from anchorium.agent import regime as R


def bars(closes, start=0, step=R.H1_MS, spread='0.01'):
    out = []
    for i, c in enumerate(closes):
        c = D(str(c))
        out.append(R.Bar(start + i * step, start + (i + 1) * step, c, c + D(spread), c - D(spread), c))
    return out


def test_ema_seeded_by_sma_and_none_before():
    e = R.ema_series([D(1), D(2), D(3), D(4)], 3)
    assert e[:2] == [None, None]
    assert e[2] == D(2)
    assert e[3] == D(2) + D('0.5') * (D(4) - D(2))


def test_atr_wilder():
    b = bars([10, 10, 10, 10], spread='0.5')  # every TR = 1.0
    a = R.atr_series(b, 3)
    assert a[:2] == [None, None] and a[2] == D(1) and a[3] == D(1)


def test_aggregate_aligned_complete_buckets_only():
    one = bars([1 + i * 0.01 for i in range(40)], start=5 * 60_000, step=60_000)  # starts mid-bucket at :05
    agg = R.aggregate(one, R.M15_MS)
    assert [b.open_ms for b in agg] == [R.M15_MS, 2 * R.M15_MS]
    assert agg[0].open == one[10].open and agg[0].close == one[24].close


def test_aggregate_drops_bucket_with_gap():
    one = bars([1] * 30, step=60_000)
    del one[3]
    assert [b.open_ms for b in R.aggregate(one, R.M15_MS)] == [R.M15_MS]


def _h1(trend):
    return R.H1Indicators(bars([5 + trend * i for i in range(80)]))


def test_bull_bear_chop_rules():
    up, down, flat = _h1(D('0.02')), _h1(D('-0.02')), _h1(D(0))
    at = 80 * R.H1_MS
    assert R.classify(up, at, D('7'), D('0.001')).regime == R.BULL
    assert R.classify(down, at, D('3'), D('0.001')).regime == R.BEAR
    # price on the wrong side of EMA20 -> CHOP
    assert R.classify(up, at, D('5'), D('0.001')).reason == 'NO_TREND'
    assert R.classify(down, at, D('9'), D('0.001')).reason == 'NO_TREND'
    assert R.classify(flat, at, D('5'), D('0.0001')).regime == R.CHOP


def test_chop_when_atr_below_twice_cost():
    up = _h1(D('0.02'))
    at = 80 * R.H1_MS
    raw = R.classify(up, at, D('7'), D('0.001'))
    assert raw.regime == R.BULL
    cost_at_edge = raw.atr_pct / 2
    assert R.classify(up, at, D('7'), cost_at_edge).regime == R.BULL  # ATR% == 2x cost is not below
    assert R.classify(up, at, D('7'), cost_at_edge + D('1e-12')).reason == 'ATR_BELOW_COST'


def test_no_lookahead_uses_only_closed_h1_bars():
    h = _h1(D('0.02'))
    assert h.last_index_at(10 * R.H1_MS - 1) == 8  # bar 9 closes at 10h exactly
    assert h.last_index_at(10 * R.H1_MS) == 9


def test_warmup_is_chop():
    h = R.H1Indicators(bars([5] * 30))
    assert R.classify(h, 30 * R.H1_MS, D(5), D('0.001')).reason == 'WARMUP'


def test_hysteresis_needs_two_consecutive():
    h = R.Hysteresis(2)
    assert h.update(R.BULL) == R.CHOP
    assert h.update(R.BULL) == R.BULL
    assert h.update(R.BEAR) == R.BULL
    assert h.update(R.CHOP) == R.BULL  # candidate reset: BEAR then CHOP is not 2 of the same
    assert h.update(R.CHOP) == R.CHOP
    assert h.update(R.BULL) == R.CHOP
    assert h.update(R.CHOP) == R.CHOP  # back to confirmed clears the candidate
    assert h.update(R.BULL) == R.CHOP
    assert h.update(R.BULL) == R.BULL


def test_hysteresis_rejects_unknown():
    with pytest.raises(ValueError):
        R.Hysteresis(2).update('SIDEWAYS')
    with pytest.raises(ValueError):
        R.Hysteresis(0)


@pytest.mark.parametrize('direction,regime,ok', [
    ('LONG', R.BULL, True), ('SHORT', R.BEAR, True), ('LONG', R.BEAR, False), ('SHORT', R.BULL, False),
    ('LONG', R.CHOP, False), ('SHORT', R.CHOP, False)])
def test_signal_regime_agreement(direction, regime, ok):
    assert R.agrees(direction, regime) is ok


def test_regime_series_and_lookup():
    h1 = _h1(D('0.02'))
    m15 = bars([7 + i * D('0.001') for i in range(8)], start=80 * R.H1_MS, step=R.M15_MS)
    s = R.regime_series(m15, h1, D('0.001'))
    assert [p.confirmed for p in s[:3]] == [R.CHOP, R.BULL, R.BULL]
    assert R.regime_at(s, s[1].at_ms - 1).confirmed == R.CHOP
    assert R.regime_at(s, s[1].at_ms).confirmed == R.BULL
    assert R.regime_at(s, 0) is None


def test_params_are_six_and_fixed():
    assert R.DEFAULT_PARAMS == R.RegimeParams(20, 50, 1, 14, D('2'), 2)
    assert len(R.DEFAULT_PARAMS.__dataclass_fields__) == 6
