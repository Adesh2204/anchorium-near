"""Version A money maths: exit rules, tax reserve, units and receipts (Decimal / base units only)."""
import random
from decimal import Decimal as D

import pytest

from anchorium import money as M

FX = D('93.50')
E = 2_000_000  # $2.00 entry
GAS = D('0.0117')  # USD, both legs
T_EXIT = 1_800_000_000_000


def chk(proceeds_units, gas=GAS, now=0, target=D('1.00'), entry=E):
    return M.check(entry, proceeds_units, gas, FX, target, now, T_EXIT)


def test_units_and_decimals():
    assert M.to_units(D('2.0000009'), M.USDC_DECIMALS) == 2_000_000  # rounds down
    assert M.to_units(D('0.5'), M.WNEAR_DECIMALS) == 5 * 10 ** 23
    assert M.usdc(1) == D('0.000001') and M.wnear(1) == D('1e-24')
    assert M.wnear(10 ** 24) == 1 and M.usdc('2000000') == 2


def test_formula_matches_the_spec():
    c = chk(2_100_000)
    assert c.taxable_gain == D('0.1')
    assert c.tax_reserve == D('0.0312')
    assert c.net_received == D('0.1') - GAS
    assert c.net_after_tax == D('0.1') - GAS - D('0.0312')
    assert c.net_after_tax_inr == c.net_after_tax * FX


def test_zero_gain_and_loss_have_no_tax_and_no_offset():
    assert chk(E).taxable_gain == 0 and chk(E).tax_reserve == 0
    loss = chk(1_900_000)
    assert loss.tax_reserve == 0 and loss.net_after_tax == loss.net_received == D('-0.1') - GAS


def test_profit_exit_exactly_at_target_and_one_unit_below():
    floor = M.profit_floor_units(E, GAS, FX, D('1.00'))
    at, below = chk(floor), chk(floor - 1)
    assert at.decision == M.PROFIT_EXIT and at.net_after_tax_inr >= D('1.00')
    assert below.decision == M.HOLD and below.net_after_tax_inr < D('1.00')
    # gas is NOT deducted for tax: tax is on proceeds - entry only
    assert at.tax_reserve == M.TAX_RATE * (M.usdc(floor) - 2)


def test_target_equal_to_net_clears():
    c = chk(2_050_000)
    assert M.check(E, 2_050_000, GAS, FX, c.net_after_tax_inr, 0, T_EXIT).profit_hit


def test_stop_loss_at_exactly_minus_5_pct_and_just_above():
    # net_received = P - 2 - gas = -0.10  ->  P = 1.9117 (units 1_911_700)
    exact = chk(1_911_700)
    assert exact.net_received == D('-0.1') and exact.decision == M.STOP_LOSS
    above = chk(1_911_701)
    assert above.net_received > D('-0.1') and above.decision == M.HOLD


def test_time_exit_at_the_cut_off_not_before():
    assert chk(E, now=T_EXIT - 1).decision == M.HOLD
    assert chk(E, now=T_EXIT).decision == M.TIME_EXIT


def test_priority_profit_then_stop_then_time():
    floor = M.profit_floor_units(E, GAS, FX, D('1.00'))
    assert chk(floor, now=T_EXIT).decision == M.PROFIT_EXIT
    assert chk(1_800_000, now=T_EXIT).decision == M.STOP_LOSS


def test_distances_to_target_and_stop():
    c = chk(E)
    assert c.to_target_inr() == D('1.00') - c.net_after_tax_inr
    assert c.to_stop_inr() == (c.net_received + D('0.1')) * FX


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        M.check(0, 1, GAS, FX, D(1), 0, 1)
    with pytest.raises(ValueError):
        M.profit_floor_units(E, GAS, FX, D(0))


def leg(usdc_u, wnear_u, price, gas='0.0014670906627479', at=0):
    return M.Leg(usdc_u, wnear_u, D(price), 20, D(gas), at, 'tx', 'journal')


def test_receipt_components_reconcile_exactly():
    buy = leg(2_000_000, 539_000_000_000_000_000_000_000, '3.70')
    sell = leg(2_031_500, 539_000_000_000_000_000_000_000, '3.79')
    r = M.Receipt.build(1, M.PROFIT_EXIT, buy, sell, FX, 'test')
    assert r.gross - r.fees - r.slippage - r.gas == r.received
    assert r.received == D('0.0315') - (D('0.0014670906627479') * D('3.70') + D('0.0014670906627479') * D('3.79'))
    assert r.tax == D('0.312') * D('0.0315')
    assert r.after_tax == r.received - r.tax


@pytest.mark.parametrize('seed', range(200))
def test_receipt_displayed_lines_add_up_exactly(seed):
    rng = random.Random(seed)
    p0 = D(rng.randint(250, 600)) / 100
    p1 = p0 * (1 + D(rng.randint(-800, 800)) / 10_000)
    q = M.to_units(D(2) / p0 * (1 - D(rng.randint(20, 90)) / 10_000), 24)
    out = M.to_units(M.wnear(q) * p1 * (1 - D(rng.randint(30, 90)) / 10_000), 6)
    fx = D(rng.randint(8800, 9700)) / 100
    r = M.Receipt.build(seed, M.STOP_LOSS, leg(2_000_000, q, str(p0)), leg(out, q, str(p1)), fx, 'test')
    L = r.lines()
    for i in (0, 1):  # USD, INR
        comps = [L[k][i] for k in ('gross', 'fees', 'slippage', 'gas')]
        assert sum(comps) == L['received'][i]
        assert L['received'][i] + L['tax'][i] == L['after_tax'][i]
        assert -L['tax'][i] >= (r.tax * (fx if i else 1)).quantize(D('0.01') if i else D('0.000001'), 'ROUND_FLOOR')
    assert L['received'][1] == (r.received * fx).quantize(D('0.01'), 'ROUND_HALF_EVEN')
    assert L['received'][0] == r.received.quantize(D('0.000001'), 'ROUND_HALF_EVEN')
    text = r.render(wallet_usdc=D('5.3'), wallet_inr=D('500'), session_after_tax_inr=D('-1'), ist=M.__dict__.get('IST')
                    or __import__('datetime').timezone.utc)
    assert f'ROUND #{seed}  CLOSED: STOP_LOSS' in text and 'YOURS AFTER TAX' in text


def test_receipt_rejects_partial_sell():
    with pytest.raises(ValueError):
        M.Receipt.build(1, M.PROFIT_EXIT, leg(2_000_000, 10, '4'), leg(2_000_000, 9, '4'), FX, 't')
