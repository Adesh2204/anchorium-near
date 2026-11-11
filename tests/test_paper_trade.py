"""Paper-trade session: entry/exit flow, max-hold TIME_EXIT, retry after a rejected exit, report arithmetic.
No network: fakes for the chain, 1Click, candles, FX and gas."""
from decimal import Decimal

from anchorium import paper_trade as pt
from anchorium.agent import signals as sig
from anchorium.agent.checkpoint import CheckpointStore
from anchorium.agent.orchestrator import USDC, Agent, AgentConfig
from anchorium.near.intents import Quote, Token
from tests.test_orchestrator import POLICY, TOKENS, WNEAR, FakeChain, FakeOneClick, signal

T0 = 1_800_000_000_000  # ms


class Clock:
    def __init__(self):
        self.now = T0 / 1000

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps = getattr(self, 'sleeps', 0) + 1
        assert self.sleeps < 1000, 'the session loop did not finish'  # a hang becomes a failure
        self.now += seconds


class PaperChain(FakeChain):
    """Adds what the paper session reads: decision timestamps and the open position."""

    def __init__(self, clock, slippage_reject_first_exit=False):
        super().__init__()
        self.clock, self.reject_exit = clock, slippage_reject_first_exit

    def positions(self):
        return self._positions

    def check_trade(self, asset, side, notional, slippage):
        if side == 'SELL':  # like the contract: exits skip the entry limits (they need an open position)
            return {'allowed': bool(self._positions), 'reason': 'OK' if self._positions else 'NO_OPEN_POSITION'}
        return super().check_trade(asset, side, notional, slippage)

    def policy(self):
        return {**POLICY, 'max_trades_per_day': 10, 'max_daily_loss_usd_cents': 100, 'cooldown_secs': 60,
                'max_open_positions': 1, 'paused': False}

    def record_decision(self, **a):
        if a['side'] == 'SELL' and self.reject_exit:
            self.reject_exit = False
            a = {**a, 'slippage_bps': 999}
        written = super().record_decision(**a)
        written.result.setdefault('timestamp_ms', int(self.clock() * 1000))
        return written

    def record_outcome(self, **a):
        written = super().record_outcome(**a)
        d = self.decisions[a['decision_id']]
        if d['side'] == 'BUY':
            self._positions = [{'asset': d['asset'], 'state': 'OPEN', 'amount': a['amount_out'], 'mode': 'DRY_RUN'}]
        else:
            self._positions = []
            written.result['outcome']['realized_pnl_usd_cents'] = 4
        return written


class PaperOneClick(FakeOneClick):
    """Reference prices: USDC $0.9998; wNEAR $4.00 until the entry quote, then $4.10."""

    def __init__(self):
        super().__init__()
        self.prices = {USDC: Decimal('0.9998'), WNEAR: Decimal('4')}

    def tokens(self, refresh=False):
        return {a: Token(t.asset_id, t.symbol, t.blockchain, t.decimals, self.prices[a], t.contract_address)
                for a, t in TOKENS.items()}

    def token(self, asset):
        return self.tokens()[asset]

    def quote(self, req):
        base = super().quote(req)
        body = base.raw
        qq = body['quote']
        if req.origin_asset == USDC:
            qq.update(amountInFormatted='2', amountOutFormatted='0.5')
            self.prices[WNEAR] = Decimal('4.10')
        else:
            # 0.5 wNEAR -> 2.04 USDC (min 2.035): 75 bps worst case at $4.10, inside the test policy's 100 bps
            qq.update(amountInFormatted='0.5', amountOut='2040000', amountOutFormatted='2.04', minAmountOut='2035000')
        body['timestamp'] = '2027-01-15T08:00:00Z'
        return Quote.from_api(body)


def setup(tmp_path, monkeypatch, reject_first_exit=False, feed_signals=None):
    clock = Clock()
    chain = PaperChain(clock, reject_first_exit)
    agent = Agent(AgentConfig(session_label='paper-test'), chain, PaperOneClick(), CheckpointStore(tmp_path / 's.sqlite'),
                  writer=chain, log=lambda e: None)
    monkeypatch.setattr(pt, 'fetch_fx', lambda: {'usd_inr': '93.7956', 'source': 'test', 'url': 'x',
                                                 'fetched_at': 't', 'cross_check': {}})
    script = list(feed_signals or [])

    def evaluate_latest(rows, coin, now_ms):
        return (script.pop(0) if script else None), {'status': 'NO_SIGNAL'}
    monkeypatch.setattr(sig, 'evaluate_latest', evaluate_latest)

    class Feed:
        def fetch(self, coin, now_ms):
            return []
    bars = iter(range(10_000))
    exit_signal = lambda coin, kind: signal('SHORT', bar=T0 + 60_000 * next(bars)).__class__(  # noqa: E731
        **{**signal('SHORT', bar=T0 + 60_000 * next(bars)).to_dict(), 'signal_type': kind, 'source': 'max-hold-timer'})
    return clock, chain, agent, Feed(), exit_signal


def test_entry_then_time_exit_after_max_hold(tmp_path, monkeypatch):
    clock, chain, agent, feed, exit_signal = setup(tmp_path, monkeypatch, feed_signals=[signal('LONG', bar=T0)])
    session = pt.Session(tmp_path / 's.json')
    state = pt.run(agent, chain, coins=['NEAR'], session=session, paper_inr=Decimal('500'), max_hold_s=4 * 3600,
                   print_=lambda *a: None, sleep=clock.sleep, clock=clock, feed=feed, exit_signal=exit_signal)
    entry, exit_ = pt.legs(agent.store)
    assert entry['side'] == 'BUY' and exit_['side'] == 'SELL'
    assert exit_['data']['signal']['signal_type'] == 'TIME_EXIT'
    held = pt.decision_time_ms(exit_) - pt.decision_time_ms(entry)
    assert 4 * 3600 * 1000 <= held < 4 * 3600 * 1000 + 3 * 60_000  # exits within a bar or two of the limit
    assert state['paper_usd'] == str((Decimal(500) / Decimal('93.7956')).quantize(Decimal('0.000001')))
    assert [c['allowed'] for c in state['guard_precheck']] == [True, True, True]
    assert chain.calls == ['record_decision', 'record_outcome'] * 2


def test_strategy_short_exits_before_the_timer(tmp_path, monkeypatch):
    clock, chain, agent, feed, exit_signal = setup(
        tmp_path, monkeypatch, feed_signals=[signal('LONG', bar=T0), None, signal('SHORT', bar=T0 + 120_000)])
    pt.run(agent, chain, coins=['NEAR'], session=pt.Session(tmp_path / 's.json'), paper_inr=Decimal('500'),
           print_=lambda *a: None, sleep=clock.sleep, clock=clock, feed=feed, exit_signal=exit_signal)
    assert pt.legs(agent.store)[1]['data']['signal']['signal_type'] == 'BREAKDOWN_SHORT'


def test_rejected_exit_is_journaled_and_retried_next_bar(tmp_path, monkeypatch):
    clock, chain, agent, feed, exit_signal = setup(tmp_path, monkeypatch, reject_first_exit=True,
                                                   feed_signals=[signal('LONG', bar=T0)])
    pt.run(agent, chain, coins=['NEAR'], session=pt.Session(tmp_path / 's.json'), paper_inr=Decimal('500'),
           max_hold_s=600, print_=lambda *a: None, sleep=clock.sleep, clock=clock, feed=feed, exit_signal=exit_signal)
    assert [d['allowed'] for d in chain.decisions] == [True, False, True]
    assert chain.decisions[1]['reason'] == 'SLIPPAGE_ABOVE_MAX'


def test_report_reconciles_and_shows_every_journal_entry(tmp_path, monkeypatch):
    clock, chain, agent, feed, exit_signal = setup(tmp_path, monkeypatch, reject_first_exit=True,
                                                   feed_signals=[signal('LONG', bar=T0)])
    session = pt.Session(tmp_path / 's.json')
    pt.run(agent, chain, coins=['NEAR'], session=session, paper_inr=Decimal('500'), max_hold_s=600,
           print_=lambda *a: None, sleep=clock.sleep, clock=clock, feed=feed, exit_signal=exit_signal)
    state = session.load()
    assert state['near_usd_at_finish'] == '4.10'
    g = pt.gather(agent.store, state, agent_id='a.testnet',
                  gas_lookup=lambda tx, sender: {'gas_burnt': 3_000_000_000_000, 'near_burnt': Decimal('0.0003')})
    e, x = g['entry'], g['exit']
    assert e['quantity'] == Decimal('0.5') and e['usdc'] == Decimal('2') and e['exec_price'] == Decimal('4')
    assert e['ref_price'] == Decimal('4') and x['ref_price'] == Decimal('4.10') and x['usdc'] == Decimal('2.04')
    assert e['fee_usd'] == Decimal('1.9996') * 20 / 10_000
    assert e['total_cost_usd'] == Decimal('1.9996') - Decimal('2.0')       # quote better than reference: negative
    assert x['total_cost_usd'] == Decimal('2.05') - Decimal('2.04') * Decimal('0.9998')
    assert g['gross_usd'] == Decimal('0.5') * (Decimal('4.10') - Decimal('4'))
    assert g['net_usdc_before_gas'] == Decimal('0.04')
    assert g['gross_usd'] - g['costs_usd'] + g['peg_residual'] == g['net_usdc_before_gas']
    assert len(g['journal_gas']) == 4 and g['journal_near'] == Decimal('0.0012')
    text = pt.render_report(g, state, guard='guard.test', network='testnet')
    assert 'TIME_EXIT (max hold reached)' in text and 'REJECTED (SLIPPAGE_ABOVE_MAX)' in text
    assert '93.7956 INR per USD' in text and '+0.04 USD' in text
    assert text.count('testnet.nearblocks.io/txns/') >= 3 + 4  # 3 decisions + 2 outcomes in the journal + 4 gas rows
