"""Version A runner: entries, exits, caps, retries, hard stops, Ctrl+C and crash/resume. No network, no chain."""
from decimal import Decimal as D

import httpx
import pytest

from anchorium import autonomous as A
from anchorium import money as M
from anchorium.agent.checkpoint import CheckpointStore
from anchorium.agent.orchestrator import USDC, Agent, AgentConfig
from anchorium.agent.signals import Signal
from anchorium.near.guard_client import Written
from anchorium.near.intents import Quote, Token
from anchorium.near.live import ExecutionConfig

WNEAR = 'nep141:wrap.near'
T0 = A.ist_ms('2026-11-12T12:00:00+05:30')
MIN = 60_000


class FakeGuard:
    """The deployed v1 guard's rules that matter here: entry limits, SELL needs an OPEN position, slippage cap,
    idempotent decisions per signal hash, positions updated by outcomes."""

    def __init__(self):
        self.contract_id, self.network = 'guard.test', 'testnet'
        self.paused = False
        self.pos = {}
        self.decisions, self.by_hash = [], {}
        self.crash_after = None  # 'record_decision' | 'record_outcome': raise once after the tx "landed"
        self.today_decisions = 0

    def policy(self): return {'paused': self.paused, 'max_slippage_bps': 150, 'max_notional_per_trade_usd_cents': 200}

    def stats(self):
        return {'today': {'decisions': self.today_decisions + len(self.decisions)}}
    def roles(self): return {'owner': 'o', 'agent': 'a'}
    def positions(self): return [dict(p) for p in self.pos.values()]

    def check_trade(self, asset, side, notional, slippage):
        if slippage is not None and slippage > 150:
            r = 'SLIPPAGE_ABOVE_MAX'
        elif side == 'SELL':
            p = self.pos.get(asset)
            r = 'OK' if p and p['state'] == 'OPEN' and p['exit'] is None else 'NO_OPEN_POSITION'
        elif self.paused:
            r = 'PAUSED'
        elif notional > 200:
            r = 'NOTIONAL_ABOVE_MAX'
        elif asset in self.pos:
            r = 'POSITION_ALREADY_OPEN'
        else:
            r = 'OK'
        return {'allowed': r == 'OK', 'reason': r}

    def record_decision(self, **a):
        if a['signal_hash'] in self.by_hash:
            d = self.decisions[self.by_hash[a['signal_hash']]]
        else:
            v = self.check_trade(a['asset'], a['side'], a['notional_usd_cents'], a['slippage_bps'])
            allowed = v['allowed'] and a['agent_allowed']
            d = {**a, 'id': len(self.decisions), 'allowed': allowed,
                 'reason': v['reason'] if not v['allowed'] else ('OK' if a['agent_allowed'] else 'AGENT_REJECTED'),
                 'outcome': None}
            self.by_hash[a['signal_hash']] = d['id']
            self.decisions.append(d)
            if allowed and a['side'] == 'BUY':
                self.pos[a['asset']] = {'asset': a['asset'], 'state': 'PENDING_ENTRY', 'amount': '0', 'exit': None,
                                        'cost_usd_cents': 0}
            elif allowed:
                self.pos[a['asset']]['exit'] = d['id']
        if self.crash_after == 'record_decision':
            self.crash_after = None
            raise KeyboardInterrupt('process killed after the decision landed')
        return Written(d, f'tx-d{d["id"]}', [])

    def record_outcome(self, **a):
        d = self.decisions[a['decision_id']]
        if d['outcome'] is None:
            d['outcome'] = a
            if d['side'] == 'BUY':
                self.pos[d['asset']].update(state='OPEN', amount=str(a['amount_out']))
            else:
                self.pos.pop(d['asset'])
        if self.crash_after == 'record_outcome':
            self.crash_after = None
            raise KeyboardInterrupt('process killed after the outcome landed')
        return Written(d, f'tx-o{d["id"]}', [])

    def legs(self, side):
        return [d for d in self.decisions if d['side'] == side]


class FakeOneClick:
    """Quotes at `price` less a per-leg cost (fraction of the input's value). `costs` overrides the next quotes."""

    def __init__(self):
        self.price = D('4.00')
        self.cost = D('0.004')
        self.costs = []
        self.n = 0
        self.fail = 0

    def tokens(self, refresh=False):
        return {USDC: Token(USDC, 'USDC', 'near', 6, D('1'), USDC.removeprefix('nep141:')),
                WNEAR: Token(WNEAR, 'wNEAR', 'near', 24, self.price, 'wrap.near')}

    def token(self, asset): return self.tokens()[asset]

    def quote(self, req):
        if self.fail:
            self.fail -= 1
            raise httpx.ConnectError('down')
        self.n += 1
        c = self.costs.pop(0) if self.costs else self.cost
        if req.origin_asset == USDC:
            out = M.to_units(M.usdc(req.amount) / self.price * (1 - c), 24)
        else:
            out = M.to_units(M.wnear(req.amount) * self.price * (1 - c), 6)
        live = {} if req.dry else {'depositAddress': f'dep{self.n}', 'deadline': '2099-01-01T00:00:00Z'}
        body = {'correlationId': f'q{self.n}', 'timestamp': 't', 'signature': 's',
                'quoteRequest': {'dry': req.dry, 'originAsset': req.origin_asset,
                                 'destinationAsset': req.destination_asset, 'appFees': [{'fee': 20}]},
                'quote': {'amountIn': str(req.amount), 'amountOut': str(out + 1), 'minAmountOut': str(out), **live}}
        return Quote.from_api(body)


class FakeMarket:
    def __init__(self, oc, clock):
        self.oc, self.clock, self.signals = oc, clock, {}

    def mid(self): return self.oc.price

    def entry_signal(self, now_ms):
        bar = now_ms - now_ms % MIN - MIN
        kind = self.signals.get(bar)
        if kind is None:
            return None
        return sig(bar, kind)

    def exit_signal(self, kind, attempt, round_n, now_ms):
        bar = now_ms - now_ms % MIN - MIN
        return Signal('NEAR', 'SHORT', kind, bar, bar + 59_999, str(self.oc.price), '0', '0', '0', '0',
                      source=f'test:{kind}:r{round_n}#{attempt}')


def sig(bar, direction='LONG'):
    return Signal('NEAR', direction, 'BREAKOUT_LONG' if direction == 'LONG' else 'BREAKDOWN_SHORT', bar,
                  bar + 59_999, '4', '4', '0.01', '4', '4')


class Clock:
    def __init__(self, t=T0 / 1000):
        self.t = t
        self.slept = []

    def __call__(self): return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class World:
    def __init__(self, tmp_path, **cfg):
        self.tmp = tmp_path
        self.guard, self.oc, self.clock = FakeGuard(), FakeOneClick(), Clock()
        self.market = FakeMarket(self.oc, self.clock)
        self.notes, self.out = [], []
        self.cfg_kw = {'session': 't', 'tick_s': 60, **cfg}
        self.runner = self.make()

    def make(self):
        agent = Agent(AgentConfig(execution=ExecutionConfig(live=False, max_trade_usd=D('0')), session_label='t',
                                  paper_fill_worst_case=True, live_total_entries_only=True),
                      self.guard, self.oc, CheckpointStore(self.tmp / 'cp.sqlite'), writer=self.guard,
                      log=lambda e: None)
        cfg = A.AutoConfig(report_path='report.md', **self.cfg_kw)
        r = A.Runner(cfg, agent, self.market, A.Wallet(False), A.StateFile(self.tmp / 'state.json'),
                     fetch_fx=lambda: {'usd_inr': '93.50', 'source': 'test-fx', 'fetched_at': 'now'},
                     notify=lambda t, m: self.notes.append((t, m)), clock=self.clock, sleep=self.clock.sleep,
                     print_=self.out.append, root=self.tmp)
        return r

    def restart(self):
        self.runner = self.make()
        self.runner.start()
        return self.runner

    def minute(self, n=1):
        self.clock.t += 60 * n

    def long_now(self):
        """A LONG signal on the bar that just completed."""
        now = int(self.clock.t * 1000)
        self.market.signals[now - now % MIN - MIN] = 'LONG'

    def events(self):
        return [e['event'] for e in self.runner.state['events']]

    def rnd(self, i=-1):
        return self.runner.state['rounds'][i]


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path)
    world.runner.start()
    return world


def buy(w):
    w.long_now()
    w.runner.tick()
    assert w.rnd()['status'] == 'OPEN', w.events()


def test_long_signal_buys_two_dollars_at_the_worst_case_fill(w):
    buy(w)
    b = w.rnd()['buy']
    assert int(b['usdc_units']) == 2_000_000
    q = w.guard.legs('BUY')[0]
    assert q['allowed'] and q['outcome']['amount_out'] == b['wnear_units']  # journaled = booked = minAmountOut
    assert w.runner.state['paper_usdc_units'] == w.runner.state['start_usdc_units'] - 2_000_000
    assert ('Anchorium PAPER: BUY' in [t for t, _ in w.notes])


def test_profit_exit_sells_and_prints_a_receipt_that_clears_the_target(w):
    buy(w)
    w.minute()
    w.runner.tick()
    assert w.rnd()['status'] == 'OPEN'  # flat price: hold
    w.oc.price = D('4.12')
    w.minute()
    w.runner.tick()
    r = w.rnd()
    assert r['status'] == 'CLOSED' and r['exit_reason'] == M.PROFIT_EXIT
    assert D(r['after_tax_inr']) >= D('1.00')
    assert 'ROUND #1  CLOSED: PROFIT_EXIT' in r['receipt_text']
    sell = w.guard.legs('SELL')[0]
    assert sell['allowed'] and sell['outcome']['amount_out'] == r['sell']['usdc_units']
    assert w.runner.state['paper_usdc_units'] == w.runner.state['start_usdc_units'] - 2_000_000 + int(
        r['sell']['usdc_units'])
    assert (w.tmp / 'report.md').read_text().count('ROUND #1  CLOSED') == 1


def test_fresh_quote_recheck_blocks_a_profit_exit_that_no_longer_passes(w):
    buy(w)
    w.oc.price = D('4.12')
    w.oc.costs = [D('0.004'), D('0.05')]  # the watcher's quote passes; the fresh one before the sell does not
    w.minute()
    w.runner.tick()
    assert w.rnd()['status'] == 'OPEN' and 'PROFIT_RECHECK_FAILED' in w.events()
    assert w.guard.legs('SELL') == []  # nothing journaled, nothing sold
    w.minute()
    w.runner.tick()
    assert w.rnd()['status'] == 'CLOSED'


def test_stop_loss_sells_at_a_loss_and_starts_the_cooldown(w):
    buy(w)
    w.oc.price = D('3.80')
    w.minute()
    w.runner.tick()
    r = w.rnd()
    assert r['exit_reason'] == M.STOP_LOSS and D(r['after_tax_inr']) < 0
    assert 'Anchorium PAPER: STOP_LOSS' in [t for t, _ in w.notes]
    assert w.runner.state['cooldown_until_ms'] == int(w.clock.t * 1000) + 3_600_000
    w.oc.price = D('4.00')
    w.minute()
    w.long_now()
    w.runner.tick()
    assert w.events()[-1] == 'SKIPPED_COOLDOWN' and len(w.guard.legs('BUY')) == 1
    w.minute(61)
    w.long_now()
    w.runner.tick()
    assert w.rnd()['status'] == 'OPEN' and len(w.guard.legs('BUY')) == 2


def test_stop_loss_just_above_minus_5_pct_holds(w):
    buy(w)
    w.oc.price = D('3.86')  # about -4.6% net after costs and gas
    w.minute()
    w.runner.tick()
    assert w.rnd()['status'] == 'OPEN'
    assert w.runner.state['last_status']['check']['decision'] == M.HOLD


def test_time_exit_sells_at_the_cut_off(tmp_path):
    w = World(tmp_path, time_exit_ms=T0 + 10 * MIN)
    w.runner.start()
    buy(w)
    w.minute(5)
    w.runner.tick()
    assert w.rnd()['status'] == 'OPEN'
    w.clock.t = (T0 + 10 * MIN) / 1000
    w.runner.tick()
    assert w.rnd()['exit_reason'] == M.TIME_EXIT


def test_no_buy_after_the_buy_cut_off(tmp_path):
    w = World(tmp_path, no_buy_after_ms=T0 + MIN)
    w.runner.start()
    w.minute(2)
    w.long_now()
    w.runner.tick()
    assert w.events()[-1] == 'SKIPPED_BUY_CUTOFF' and w.guard.decisions == []


def test_one_open_round_signals_while_open_do_nothing(w):
    buy(w)
    w.minute()
    w.long_now()
    w.runner.tick()
    assert len(w.guard.legs('BUY')) == 1 and len(w.runner.state['rounds']) == 1


def test_three_rounds_per_session(w):
    for _ in range(3):
        w.oc.price = D('4.00')
        w.minute()
        buy(w)
        w.oc.price = D('4.20')
        w.minute()
        w.runner.tick()
        assert w.rnd()['status'] == 'CLOSED'
    w.oc.price = D('4.00')
    w.minute()
    w.long_now()
    w.runner.tick()
    assert w.events()[-1] == 'SKIPPED_ROUND_CAP' and len(w.guard.legs('BUY')) == 3


def test_session_loss_cap_stops_for_good(tmp_path):
    w = World(tmp_path, loss_cap_inr=D('5'))
    w.runner.start()
    buy(w)
    w.oc.price = D('3.60')
    w.minute()
    w.runner.tick()
    assert w.runner.state['loss_cap_hit'] and 'SESSION_LOSS_CAP' in w.events()
    assert w.runner.run() == 'SESSION_LOSS_CAP already hit'


def test_cost_filter_skips_and_journals_at_most_every_15_min(w):
    w.oc.cost = D('0.01')  # ~2.0% + gas > 1.6%
    w.long_now()
    w.runner.tick()
    assert w.events()[-1] == 'SKIPPED_COST'
    d = w.guard.decisions[-1]
    assert not d['allowed'] and d['reason'] == 'AGENT_REJECTED' and d['agent_reason'].startswith('SKIPPED_COST')
    w.minute()
    w.long_now()
    w.runner.tick()
    assert w.events()[-1] == 'SKIPPED_COST' and len(w.guard.decisions) == 1  # second one journaled locally
    assert w.runner.state['rounds'] == []


def test_guard_rejected_stop_sell_is_retried_and_journaled_then_waits_for_the_owner(w):
    buy(w)
    w.oc.price = D('3.80')
    w.oc.cost = D('0.02')  # 200 bps > the guard's 150: every sell attempt is rejected
    for _ in range(31):
        w.minute()
        w.runner.tick()
    rejected = [d for d in w.guard.legs('SELL') if not d['allowed']]
    assert len(rejected) >= 30  # every attempt journaled
    assert w.rnd()['exit_blocked'] and 'EXIT_BLOCKED' in w.events()
    n = len(w.guard.decisions)
    w.minute()
    w.runner.tick()
    assert len(w.guard.decisions) == n  # waiting for the owner: no more attempts
    w.rnd()['exit_blocked'], w.rnd()['reject_since_ms'] = False, None  # --resume-exit
    w.oc.cost = D('0.004')
    w.minute()
    w.runner.tick()
    assert w.rnd()['status'] == 'CLOSED' and w.rnd()['exit_reason'] == M.STOP_LOSS


def test_network_errors_back_off_then_pause(w):
    w.oc.fail = 2
    w.long_now()
    w.runner.tick()
    assert w.clock.slept[:2] == [5, 15] and w.rnd()['status'] == 'OPEN'
    w.oc.fail = 99
    w.minute()
    w.long_now()
    with pytest.raises(A.NetworkDown):
        w.runner.tick()
    assert w.clock.slept[-4:] == [5, 15, 60, 60]


def test_guard_paused_while_flat_is_a_hard_stop(w):
    w.guard.paused = True
    w.long_now()
    with pytest.raises(A.HardStop, match='GUARD_PAUSED'):
        w.runner.tick()


def test_refunded_leg_is_a_hard_stop(w):
    rnd = {'n': 1}
    with pytest.raises(A.HardStop, match='REFUNDED'):
        w.runner.book_buy(rnd, {'state': 'DONE', 'data': {'result': 'REFUNDED',
                                                          'timeline': [{'status': 'REFUNDED'}]}})
    with pytest.raises(A.HardStop, match='INCOMPLETE_DEPOSIT'):
        w.runner.book_buy(rnd, {'state': 'DONE', 'data': {'result': 'FAILED',
                                                          'timeline': [{'status': 'INCOMPLETE_DEPOSIT'}]}})


def test_run_records_a_hard_stop_and_does_not_restart_it(w):
    w.guard.paused = True
    w.long_now()
    assert w.runner.run().startswith('HARD_STOP: GUARD_PAUSED')
    assert w.runner.state['hard_stop'] and 'Anchorium PAPER: HARD_STOP' in [t for t, _ in w.notes]
    w.guard.paused = False
    assert w.restart().run().startswith('HARD_STOP already recorded')


def test_ctrl_c_finishes_the_step_saves_and_exits(w):
    buy(w)
    w.runner.stop_requested = True
    assert w.runner.run() == 'CTRL_C'
    saved = A.StateFile(w.tmp / 'state.json').load()
    assert saved['finished']['reason'] == 'CTRL_C' and saved['rounds'][0]['status'] == 'OPEN'


def test_crash_after_buy_decision_resumes_without_a_second_buy(w):
    w.guard.crash_after = 'record_decision'
    w.long_now()
    with pytest.raises(KeyboardInterrupt):
        w.runner.tick()
    assert A.StateFile(w.tmp / 'state.json').load()['rounds'][0]['status'] == 'BUYING'
    r = w.restart()
    r.tick()
    assert w.rnd()['status'] == 'OPEN'
    assert len(w.guard.legs('BUY')) == 1 and w.guard.legs('BUY')[0]['outcome'] is not None


def test_crash_after_sell_outcome_resumes_and_closes_once(w):
    buy(w)
    w.oc.price = D('4.12')
    w.guard.crash_after = 'record_outcome'
    w.minute()
    with pytest.raises(KeyboardInterrupt):
        w.runner.tick()
    r = w.restart()
    r.tick()
    assert w.rnd()['status'] == 'CLOSED'
    assert len(w.guard.legs('SELL')) == 1 and len(w.guard.legs('BUY')) == 1
    r.tick()  # flat again; nothing more happens without a new signal
    assert len(w.guard.decisions) == 2


def test_interrupted_live_deposit_is_detected_and_never_resent(w):
    store = w.runner.agent.store
    store.create('h' * 64, 'LIVE', 'NEAR', {})
    store.advance('h' * 64, 'DEPOSIT_PENDING')
    with pytest.raises(A.HardStop, match='DEPOSIT_NEEDS_REVIEW'):
        w.restart()


def test_run_until_and_stop_after_one_round(tmp_path):
    w = World(tmp_path, stop_after_closed=1, run_until_ms=T0 + 30 * MIN)
    w.long_now()
    w.oc.price = D('4.00')

    def tick_hook(orig=w.runner.tick):
        orig()
        if w.runner.state['rounds'] and w.rnd()['status'] == 'OPEN':
            w.oc.price = D('4.12')
    w.runner.tick = tick_hook
    assert w.runner.run() == '1 ROUND(S) CLOSED'
    (tmp_path / 'b').mkdir(exist_ok=True)
    w2 = World(tmp_path / 'b', run_until_ms=T0 + 5 * MIN)
    assert w2.runner.run().startswith('RUN_UNTIL')


def test_session_lock_blocks_a_second_session(tmp_path):
    a, b = A.SessionLock(tmp_path / 'LOCK'), A.SessionLock(tmp_path / 'LOCK')
    a.acquire()
    assert A.SessionLock.held(tmp_path / 'LOCK')
    with pytest.raises(A.HardStop, match='ANOTHER_SESSION'):
        b.acquire()
    a.release()
    assert not A.SessionLock.held(tmp_path / 'LOCK')


def test_status_text_shows_the_last_five_events(w):
    buy(w)
    w.minute()
    w.runner.tick()
    text = A.status_text(w.runner.state)
    assert 'round #1 OPEN' in text and 'last status:' in text
    assert text.count('\n  [') == min(5, len(w.runner.state['events']))
    for _ in range(4):
        w.runner.event('X', 'filler')
    assert A.status_text(w.runner.state).count('\n  [') == 5


# ----- regressions from the independent review -----

class FakeTrader:
    """Mainnet trader account: balances + an ft_transfer that can fail after (maybe) broadcasting."""

    def __init__(self):
        self.transfers = []
        self.fail_with = None
        self.usdc, self.wnear, self.near = 5_000_000, 0, 10 ** 23

    def ft_transfer(self, token, to, amount, register=True):
        self.transfers.append((token, to, amount))
        if self.fail_with:
            raise self.fail_with
        raise AssertionError('not expected in this test')

    def ft_balance(self, contract, account):
        return self.usdc if contract != 'wrap.near' else self.wnear

    def account(self, account):
        return type('Acct', (), {'amount': self.near})()


def live_world(tmp_path):
    w = World(tmp_path)
    trader = FakeTrader()
    agent = Agent(AgentConfig(execution=ExecutionConfig(live=True, max_trade_usd=D('2'), max_total_usd=D('6')),
                              session_label='t', trader_account='trader.near', paper_fill_worst_case=True,
                              live_total_entries_only=True),
                  w.guard, w.oc, CheckpointStore(tmp_path / 'live.sqlite'), writer=w.guard, trader_near=trader,
                  confirm=lambda *a, **k: None, log=lambda e: None)
    w.runner = A.Runner(A.AutoConfig(session='t', live=True, report_path='report.md'), agent, w.market,
                        A.Wallet(True, trader, 'trader.near'), A.StateFile(tmp_path / 'live.json'),
                        fetch_fx=lambda: {'usd_inr': '93.50', 'source': 'test-fx', 'fetched_at': 'now'},
                        notify=lambda t, m: w.notes.append((t, m)), clock=w.clock, sleep=w.clock.sleep,
                        print_=w.out.append, root=tmp_path)
    w.runner.start()
    return w, trader


def test_live_network_error_during_a_deposit_hard_stops_and_never_resends(tmp_path):
    w, trader = live_world(tmp_path)
    trader.fail_with = TimeoutError('RPC timed out after broadcasting?')
    w.long_now()
    with pytest.raises(A.HardStop, match='DEPOSIT_NEEDS_REVIEW'):
        w.runner.tick()
    assert len(trader.transfers) == 1  # one deposit attempt, never re-sent
    rows = w.runner.agent.store.db.execute("SELECT state FROM runs").fetchall()
    assert ('NEEDS_REVIEW',) in rows
    # a restart hard-stops on the parked row instead of buying again
    with pytest.raises(A.HardStop, match='NEEDS_REVIEW'):
        w.runner.start()
    assert len(trader.transfers) == 1


def test_orchestrator_never_reruns_a_deposit_pending_leg(w):
    s = sig(1_000_000)
    h = w.runner.agent._hash(s)
    store = w.runner.agent.store
    store.create(h, 'DRY_RUN', 'NEAR', {'signal': s.to_dict()})
    store.advance(h, 'DEPOSIT_PENDING')
    row = w.runner.agent.process(s)
    assert row['state'] == 'NEEDS_REVIEW' and w.guard.decisions == []


def test_checkpoint_rejects_illegal_transitions(tmp_path):
    store = CheckpointStore(tmp_path / 'x.sqlite')
    store.create('a' * 64, 'LIVE', 'NEAR', {})
    store.advance('a' * 64, 'DEPOSIT_PENDING')
    with pytest.raises(ValueError):
        store.advance('a' * 64, 'SKIPPED')
    store.advance('a' * 64, 'NEEDS_REVIEW')
    with pytest.raises(ValueError):
        store.advance('a' * 64, 'DONE')


def test_reject_window_restarts_for_a_new_stop_episode(w):
    buy(w)
    w.oc.price = D('3.80')
    w.oc.cost = D('0.02')  # rejected
    w.minute()
    w.runner.tick()
    assert w.rnd()['reject_since_ms'] is not None
    w.oc.price, w.oc.cost = D('4.00'), D('0.004')  # recovers: HOLD ends the episode
    w.minute()
    w.runner.tick()
    assert w.rnd()['reject_since_ms'] is None
    w.minute(120)
    w.oc.price, w.oc.cost = D('3.80'), D('0.02')  # a new stop, rejected once
    w.runner.tick()
    assert not w.rnd()['exit_blocked']  # a fresh 30 min window, not "blocked after one try"
    w.oc.cost = D('0.004')
    w.minute()
    w.runner.tick()
    assert w.rnd()['status'] == 'CLOSED' and w.rnd()['exit_reason'] == M.STOP_LOSS


def test_resumed_buy_rechecks_the_gates(tmp_path):
    w = World(tmp_path, no_buy_after_ms=T0 + 5 * MIN)
    w.runner.start()
    orig = w.runner.leg
    w.runner.leg = lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt('killed before the leg'))
    w.long_now()
    with pytest.raises(KeyboardInterrupt):
        w.runner.tick()
    assert w.rnd()['status'] == 'BUYING'
    w.runner.leg = orig
    w.clock.t = (T0 + 10 * MIN) / 1000  # restarted after the buy cut-off
    r = w.restart()
    r.tick()
    assert w.rnd()['status'] == 'ABORTED' and w.guard.decisions == []


def test_loss_cap_is_derived_from_closed_rounds_on_restart(tmp_path):
    w = World(tmp_path, loss_cap_inr=D('5'))
    w.runner.start()
    buy(w)
    w.oc.price = D('3.60')
    w.minute()
    w.runner.tick()
    st = A.StateFile(tmp_path / 'state.json').load()
    st['loss_cap_hit'] = False  # as if the process died before the flag was saved
    A.StateFile(tmp_path / 'state.json').save(st)
    assert w.make().run() == 'SESSION_LOSS_CAP already hit'


def test_unexpected_error_is_a_notified_hard_stop(w):
    def boom(now):
        raise RuntimeError('contract panic DAILY_DECISION_LIMIT')
    w.market.entry_signal = boom
    assert w.runner.run().startswith('HARD_STOP: UNEXPECTED_ERROR')
    assert 'Anchorium PAPER: HARD_STOP' in [t for t, _ in w.notes]


def test_skip_journaling_keeps_a_reserve_of_daily_decisions(w):
    w.guard.today_decisions = 60  # only 40 of 100 left
    w.oc.cost = D('0.01')
    w.long_now()
    w.runner.tick()
    assert w.events()[-1] == 'SKIPPED_COST' and w.guard.decisions == []


def test_stale_signal_is_not_bought(w):
    w.long_now()
    w.minute(4)
    w.market.signals[int(w.clock.t * 1000) - int(w.clock.t * 1000) % MIN - MIN] = None
    s = sig(int(w.clock.t * 1000) - 5 * MIN)
    assert w.runner.entry_block(int(w.clock.t * 1000), s) == 'STALE_SIGNAL'


def test_sell_refused_before_any_deposit_is_retried_not_a_hard_stop(w):
    buy(w)
    row = {'state': 'DONE', 'data': {'result': 'FAILED', 'outcome_args': {'tx_ref': 'aborted-before-deposit:QUOTE_EXPIRED'},
                                     'decision': {'reason': 'OK'}}}
    w.rnd()['pending_exit'] = {'reason': M.STOP_LOSS, 'attempt': 1, 'started_ms': int(w.clock.t * 1000),
                               'floor_units': None, 'signal': {}}
    w.runner.book_sell(w.rnd(), row)
    assert w.rnd()['status'] == 'OPEN' and 'SELL_NOT_ALLOWED' in w.events()
