from decimal import Decimal as D

import pytest

from research.regime import ledger as L


def test_units_round_down_and_decimals():
    assert L.units(L.USDC, D('1.9999999')) == 1_999_999
    assert L.units(L.WNEAR, D('0.5')) == 5 * 10 ** 23
    assert L.human(L.WNEAR, 10 ** 24) == D(1)


def test_fifo_consumes_oldest_first_and_splits_cost_exactly():
    led = L.Ledger()
    led.acquire(L.WNEAR, 10 ** 24, D('300'), 1, 'a')
    led.acquire(L.WNEAR, 10 ** 24, D('100'), 2, 'b')
    d = led.dispose(L.WNEAR, 15 * 10 ** 23, D('500'), 3, 'x')
    assert d.cost_inr == D('350')  # all of a + half of b
    assert d.gain_inr == D('150') and d.tax_reserve_inr == D('46.8')
    assert [x[0] for x in d.lots_used] == ['a', 'b']
    assert led.balance(L.WNEAR) == 5 * 10 ** 23
    assert led.lots[L.WNEAR][0].cost_inr == D('50')


def test_partial_lot_cost_conserved():
    led = L.Ledger()
    led.acquire(L.USDC, 3_000_000, D('100'), 1, 'a')
    d = led.dispose(L.USDC, 1_000_000, D('40'), 2, 'x')
    assert d.cost_inr + led.lots[L.USDC][0].cost_inr == D('100')


def test_losses_not_offset_per_disposal():
    led = L.Ledger()
    led.acquire(L.WNEAR, 10 ** 24, D('100'), 1, 'a')
    led.acquire(L.WNEAR, 10 ** 24, D('100'), 2, 'b')
    loss = led.dispose(L.WNEAR, 10 ** 24, D('60'), 3, 'x')
    gain = led.dispose(L.WNEAR, 10 ** 24, D('150'), 4, 'y')
    assert loss.gain_inr == D('-40') and loss.tax_reserve_inr == 0
    assert gain.tax_reserve_inr == D('0.312') * 50  # not 0.312 * (50 - 40)


def test_dispose_more_than_held_fails_without_changing_state():
    led = L.Ledger()
    led.acquire(L.USDC, 1_000_000, D('90'), 1, 'a')
    with pytest.raises(L.InsufficientInventory):
        led.dispose(L.USDC, 1_000_001, D('90'), 2, 'x')
    assert led.balance(L.USDC) == 1_000_000 and not led.disposals


def test_peek_does_not_mutate():
    led = L.Ledger()
    led.acquire(L.USDC, 1_000_000, D('90'), 1, 'a')
    led.peek_dispose(L.USDC, 500_000, D('50'))
    assert led.balance(L.USDC) == 1_000_000 and not led.disposals


def test_ledger_round_trips_through_json():
    led = L.Ledger()
    led.acquire(L.WNEAR, 10 ** 24, D('300.123456789'), 1, 'a')
    led.dispose(L.WNEAR, 4 * 10 ** 23, D('130'), 2, 'x')
    back = L.Ledger.from_dict(led.to_dict())
    assert back.to_dict() == led.to_dict()


def test_tax_rate():
    assert L.tax_on(D('100')) == D('31.2')
    assert L.tax_on(D('-5')) == 0


# ----- round economics -----

FX = D('88.5')


def bull_setup(open_price=D('4'), cost=D('0.005')):
    """Wallet: 2 USDC + nothing else; bull round spends 2 USDC for wNEAR at open_price less `cost`."""
    led = L.Ledger()
    led.acquire(L.USDC, 2_000_000, D(2) * FX, 0, 'start')
    got = L.units(L.WNEAR, D(2) / open_price * (1 - cost))
    sw = L.Swap(L.USDC, 2_000_000, L.WNEAR, got, open_price, FX, D('0.001'), 1, 'open')
    d = L.apply_swap(led, sw)
    return led, sw, d


def test_bull_open_books_fee_as_untaxed_loss():
    led, sw, d = bull_setup()
    assert d.gain_inr < 0 and d.tax_reserve_inr == 0
    assert led.balance(L.USDC) == 0 and led.balance(L.WNEAR) == sw.bought_units


def bull_check(led, sw, received_usdc, price=D('4'), target=D('1.00')):
    return L.close_check(led, L.BULL_ROUND, sw, D(0), received_usdc, price, FX, D('0.001'), target)


def test_bull_profit_formula_exact():
    led, sw, _ = bull_setup()
    c = bull_check(led, sw, 2_100_000)
    gas_usd = D('0.002') * 4
    assert c.pnl_usd == D('0.1') - gas_usd
    assert c.pnl_inr == (D('0.1') - gas_usd) * FX
    # close disposal: proceeds 2.1 USDC * FX, cost = INR value at open of the wNEAR received
    expected_gain = D('2.1') * FX - sw.received_inr()
    assert c.tax_close_inr == D('0.312') * expected_gain
    assert c.net_after_tax_inr == c.pnl_inr - c.tax_close_inr


def _find_threshold(led, sw, target):
    lo, hi = 2_000_000, 3_000_000
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if bull_check(led, sw, mid, target=target).clears:
            hi = mid
        else:
            lo = mid
    return lo, hi


def test_exact_target_clears_and_just_below_does_not():
    led, sw, _ = bull_setup()
    lo, hi = _find_threshold(led, sw, D('1.00'))
    assert not bull_check(led, sw, lo).clears
    assert bull_check(led, sw, hi).clears
    # a target equal to the computed net clears exactly (>=), the smallest representable step above does not
    net = bull_check(led, sw, hi).net_after_tax_inr
    assert bull_check(led, sw, hi, target=net).clears
    assert not bull_check(led, sw, hi, target=net + D('1e-20')).clears


def test_tax_edge_round_loss_but_taxable_gain_cannot_close():
    """FIFO edge: the close sells an OLD cheap lot, so a round that made money can still owe more tax than it made."""
    led = L.Ledger()
    led.acquire(L.WNEAR, 10 ** 24, D(1) * FX, 0, 'old-cheap')  # 1 wNEAR bought at $1
    led.acquire(L.USDC, 2_000_000, D(2) * FX, 0, 'start')
    sw = L.Swap(L.USDC, 2_000_000, L.WNEAR, L.units(L.WNEAR, D('0.5')), D(4), FX, D(0), 1, 'open')
    L.apply_swap(led, sw)
    c = L.close_check(led, L.BULL_ROUND, sw, D(0), 2_050_000, D(4), FX, D(0), D('1.00'))
    assert c.pnl_inr == D('0.05') * FX  # +4.43 INR round P&L
    assert c.tax_close_inr == D('0.312') * (D('2.05') - D('0.5')) * FX  # FIFO sells the old $1 lot
    assert not c.clears


def bear_setup(price=D('4'), cost=D('0.005')):
    led = L.Ledger()
    led.acquire(L.WNEAR, 10 ** 24, D(4) * FX, 0, 'start')  # 1 wNEAR at $4
    sold = L.units(L.WNEAR, D('0.5'))
    got = L.units(L.USDC, D('0.5') * price * (1 - cost))
    sw = L.Swap(L.WNEAR, sold, L.USDC, got, price, FX, D('0.001'), 1, 'open')
    d = L.apply_swap(led, sw)
    return led, sw, d


def test_bear_profit_is_extra_near_valued_now():
    led, sw, d = bear_setup()
    assert d.tax_reserve_inr == 0  # sold at a fee-loss vs the $4 cost
    price_now = D('3.8')
    back = L.units(L.WNEAR, L.human(L.USDC, sw.bought_units) / price_now * (1 - D('0.005')))
    c = L.close_check(led, L.BEAR_ROUND, sw, d.tax_reserve_inr, back, price_now, FX, D('0.001'), D('1.00'))
    extra = L.human(L.WNEAR, back - sw.sold_units)
    assert extra > 0
    assert c.pnl_usd == extra * price_now - D('0.002') * price_now
    # the close disposes of USDC acquired at open: gain = INR value of the wNEAR received - USDC cost
    assert c.tax_close_inr == L.tax_on(L.human(L.WNEAR, back) * price_now * FX - sw.received_inr())
    assert c.net_after_tax_inr == c.pnl_inr - c.tax_close_inr


def test_bear_never_clears_when_price_rose():
    led, sw, d = bear_setup()
    back = L.units(L.WNEAR, L.human(L.USDC, sw.bought_units) / D('4.2'))
    c = L.close_check(led, L.BEAR_ROUND, sw, d.tax_reserve_inr, back, D('4.2'), FX, D('0.001'), D('1.00'))
    assert c.pnl_inr < 0 and not c.clears


def test_open_tax_counts_against_the_round():
    led, sw, _ = bull_setup()
    a = bull_check(led, sw, 2_200_000)
    b = L.close_check(led, L.BULL_ROUND, sw, D('2'), 2_200_000, D('4'), FX, D('0.001'), D('1.00'))
    assert b.net_after_tax_inr == a.net_after_tax_inr - 2


def test_round_kind_must_match_open_leg():
    led, sw, _ = bull_setup()
    with pytest.raises(ValueError):
        L.close_check(led, L.BEAR_ROUND, sw, D(0), 10 ** 24, D(4), FX, D(0), D(1))
