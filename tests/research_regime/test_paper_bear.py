"""Paper bear-round experiment: open on SHORT, profit-only buy-back, receipt reconciles, regime filter skips."""
from dataclasses import dataclass
from decimal import Decimal as D

from research.regime import ledger as L
from research.regime import paper_bear as PB
from research.regime import regime as R


@dataclass
class Q:
    min_amount_out: int
    quote_id: str = 'q'


@dataclass
class Sig:
    direction: str
    bar_open_ms: int
    bar_close_ms: int
    signal_type: str = 'BREAKDOWN_SHORT'
    close: str = '3.47'


def make(tmp_path, monkeypatch, regime_filter=False, cost=D('0.005')):
    b = PB.Bear('t', regime_filter, 10 ** 15, 1, tmp_path)
    clock = {'t': 1_000_000_000_000, 'p': D('3.50')}
    monkeypatch.setattr(PB, 'now_ms', lambda: clock['t'])
    b.mid = lambda: clock['p']
    b.fx = lambda: D('93.70')

    def quote(origin, dest, amount):  # worst-case output at the current price less `cost`
        if origin == PB.WNEAR_ASSET:
            return Q(L.units(L.USDC, L.human(L.WNEAR, amount) * clock['p'] * (1 - cost)))
        return Q(L.units(L.WNEAR, L.human(L.USDC, amount) / clock['p'] * (1 - cost)))
    b.quote = quote
    b.state = {'session': 't', 'regime_filter': regime_filter, 'started_at': 's', 'until': 'u', 'rounds': [],
               'last_bar_ms': 0, 'skips': {}, 'start_price': '3.5', 'start_fx': '93.7',
               'start_usdc_units': 2_000_000, 'start_wnear_units': 10 ** 24}
    b.led.acquire(L.USDC, 2_000_000, D('187'), 0, 'start')
    b.led.acquire(L.WNEAR, 10 ** 24, D('328'), 0, 'start')
    return b, clock


def short_now(monkeypatch, clock):
    s = Sig('SHORT', clock['t'] - 120_000, clock['t'] - 60_000)
    monkeypatch.setattr(PB.sig, 'evaluate_latest', lambda *a: (s, {}))
    monkeypatch.setattr(PB.sig, 'HyperliquidCandles', lambda: type('F', (), {'fetch': lambda *a: []})())


def test_short_opens_holds_on_rise_closes_on_fall_with_reconciled_receipt(tmp_path, monkeypatch):
    b, clock = make(tmp_path, monkeypatch)
    short_now(monkeypatch, clock)
    b.tick()
    rnd = b.open_round()
    assert rnd and b.led.balance(L.WNEAR) < 10 ** 24
    clock['p'] = D('3.60')
    b.tick()
    assert b.open_round()['status'] == 'OPEN'
    clock['p'] = D('3.30')
    b.tick()
    assert rnd['status'] == 'CLOSED' and D(rnd['net_after_tax_inr']) >= PB.TARGET_INR
    assert 'YOURS AFTER TAX' in rnd['receipt']
    b.render()


def test_long_is_skipped_and_regime_filter_skips_short_in_chop(tmp_path, monkeypatch):
    b, clock = make(tmp_path, monkeypatch, regime_filter=True)
    short_now(monkeypatch, clock)
    b.candles = lambda *a: []
    monkeypatch.setattr(PB.R, 'current_regime', lambda *a: None)
    b.tick()
    assert b.state['skips'] == {'SKIPPED_REGIME': 1} and b.open_round() is None
    assert R.CHOP in b.state['events'][-1]['text']


def test_cost_filter(tmp_path, monkeypatch):
    b, clock = make(tmp_path, monkeypatch, cost=D('0.01'))
    short_now(monkeypatch, clock)
    b.tick()
    assert b.state['skips'] == {'SKIPPED_COST': 1}
