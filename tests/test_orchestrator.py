"""Orchestrator: every path with fakes (no network, no chain). Crash safety and idempotency included."""
from decimal import Decimal

import pytest

from anchorium.agent.checkpoint import CheckpointStore
from anchorium.agent.orchestrator import USDC, Agent, AgentConfig
from anchorium.agent.signals import Signal
from anchorium.near.intents import FUNDS_LOCATION, OneClickClient, Quote, StatusUpdate, Token, Tracking
from anchorium.near.live import ExecutionConfig, LiveGateError
from anchorium.near.guard_client import Written

WNEAR = 'nep141:wrap.near'
POLICY = {'max_slippage_bps': 100, 'max_notional_per_trade_usd_cents': 200}
TOKENS = {USDC: Token(USDC, 'USDC', 'near', 6, Decimal('1'), USDC.removeprefix('nep141:')),
          WNEAR: Token(WNEAR, 'wNEAR', 'near', 24, Decimal('4'), 'wrap.near')}


def signal(direction='LONG', bar=1_000_000):
    return Signal(coin='NEAR', direction=direction, signal_type='BREAKOUT_LONG' if direction == 'LONG' else
                  'BREAKDOWN_SHORT', bar_open_ms=bar, bar_close_ms=bar + 59_999, close='4.0', vwap='3.9',
                  atr_14='0.01', ema_9='3.95', ema_21='3.9')


class FakeChain:
    """Minimal guard: reader + writer over shared state, with the contract's idempotency and override rule."""

    def __init__(self, verdict='OK', positions=None):
        self.contract_id, self.network = 'guard.test', 'testnet'
        self.verdict, self._positions = verdict, positions or []
        self.decisions, self.by_hash, self.calls = [], {}, []
        self.crash_after_send = None

    # reader
    def policy(self): return POLICY
    def positions(self): return self._positions
    def roles(self): return {'owner': 'o.testnet', 'agent': 'a.testnet'}
    def check_trade(self, asset, side, notional, slippage):
        reason = 'NOTIONAL_ABOVE_MAX' if notional > 200 else self.verdict
        return {'allowed': reason == 'OK', 'reason': reason}

    # writer
    def record_decision(self, **a):
        self.calls.append('record_decision')
        if a['signal_hash'] in self.by_hash:
            d = self.decisions[self.by_hash[a['signal_hash']]]
        else:
            contract = self.check_trade(a['asset'], a['side'], a['notional_usd_cents'], a['slippage_bps'])
            if a['slippage_bps'] > POLICY['max_slippage_bps']:
                contract = {'allowed': False, 'reason': 'SLIPPAGE_ABOVE_MAX'}
            allowed = contract['allowed'] and a['agent_allowed']
            reason = contract['reason'] if not contract['allowed'] else ('OK' if a['agent_allowed'] else 'AGENT_REJECTED')
            d = {**a, 'id': len(self.decisions), 'allowed': allowed, 'reason': reason, 'outcome': None}
            self.by_hash[a['signal_hash']] = d['id']
            self.decisions.append(d)
        logs = ['EVENT_JSON:{"event":"verdict_override"}'] if a['agent_allowed'] and not d['allowed'] else []
        if self.crash_after_send == 'record_decision':
            self.crash_after_send = None
            raise ConnectionError('process killed after the transaction was sent')
        return Written(d, f'tx{len(self.calls)}', logs)

    def record_outcome(self, **a):
        self.calls.append('record_outcome')
        d = self.decisions[a['decision_id']]
        pnl = -5 if d['side'] == 'SELL' and a['status'] in ('SUCCESS', 'DRY_RUN') else None
        d['outcome'] = {**a, 'realized_pnl_usd_cents': pnl}
        return Written(d, f'tx{len(self.calls)}', [])


class FakeOneClick:
    authenticated = False

    def __init__(self, slippage_out=None, tracking=None):
        self.quotes, self.submitted, self.tracking = [], [], tracking
        self.out = slippage_out  # wNEAR base units for the guaranteed output

    def token(self, asset): return TOKENS[asset]

    def tokens(self, refresh=False): return TOKENS

    def quote(self, req):
        self.quotes.append(req)
        if req.origin_asset == USDC:  # $2 -> 0.5 wNEAR at $4
            amount_out = 5 * 10**23
            min_out = self.out if self.out is not None else 4995 * 10**20
        else:
            amount_out, min_out = 1_990_000, 1_985_000
        body = {'correlationId': f'c{len(self.quotes)}', 'timestamp': 't', 'signature': 's',
                'quoteRequest': {'dry': req.dry, 'originAsset': req.origin_asset, 'destinationAsset': req.destination_asset,
                                 'appFees': [{'fee': 20}]},
                'quote': {'amountIn': str(req.amount), 'amountOut': str(amount_out), 'minAmountOut': str(min_out),
                          'timeEstimate': 10, **({} if req.dry else {'depositAddress': 'dep-1',
                                                                      'deadline': '2099-01-01T00:00:00Z'})}}
        return Quote.from_api(body)

    def submit_deposit(self, *a, **k): self.submitted.append(a)

    def track(self, deposit_address, memo=None, on_update=None, **k):
        result = self.tracking.pop(0)
        for u in result.timeline:
            on_update and on_update(u)
        return result


def update(status, details=None):
    return StatusUpdate(status, 't', 't', FUNDS_LOCATION[status], details or {})


def make(tmp_path, chain=None, oneclick=None, writer=True, live=False, confirm=lambda phrase, details=None: None,
         trader=None):
    chain = chain or FakeChain()
    events = []
    config = AgentConfig(execution=ExecutionConfig(live=live, max_trade_usd=Decimal('2')),
                         trader_account='trader.near' if live else None)
    agent = Agent(config, chain, oneclick or FakeOneClick(), CheckpointStore(tmp_path / 'cp.sqlite'),
                  writer=chain if writer else None, trader_near=trader, confirm=confirm, log=events.append)
    return agent, chain, events


def kinds(events):
    return [e['event'] for e in events]


def test_dry_run_entry_is_journaled_with_a_paper_outcome(tmp_path):
    agent, chain, events = make(tmp_path)
    row = agent.process(signal())
    assert row['state'] == 'DONE' and row['data']['result'] == 'DRY_RUN'
    assert chain.calls == ['record_decision', 'record_outcome']
    d = chain.decisions[0]
    assert d['allowed'] and d['side'] == 'BUY' and d['mode'] == 'DRY_RUN' and d['notional_usd_cents'] == 200
    assert d['outcome']['status'] == 'DRY_RUN' and d['outcome']['tx_ref'] == 'dry:c1'
    assert kinds(events) == ['signal', 'guard_check', 'quote', 'decision_journaled', 'outcome_journaled']
    assert agent.oneclick.quotes[0].dry is True


def test_path_a_without_key_never_writes(tmp_path):
    agent, chain, events = make(tmp_path, writer=False)
    row = agent.process(signal())
    assert row['state'] == 'SKIPPED' and chain.calls == []
    would = [e for e in events if e['event'] == 'would_journal'][0]
    assert would['args']['agent_allowed'] is True and would['method'] == 'record_decision'


def test_short_without_position_is_skipped_locally(tmp_path):
    agent, chain, events = make(tmp_path)
    row = agent.process(signal('SHORT'))
    assert row['state'] == 'SKIPPED' and row['data']['skip_reason'] == 'SHORT_SIGNAL_WITHOUT_SPOT_POSITION'
    assert chain.calls == [] and agent.oneclick.quotes == []


def test_short_with_open_position_sells_the_position_amount(tmp_path):
    chain = FakeChain(positions=[{'asset': WNEAR, 'state': 'OPEN', 'amount': str(5 * 10**23)}])
    agent, chain, _ = make(tmp_path, chain=chain)
    agent.process(signal('SHORT'))
    req = agent.oneclick.quotes[0]
    assert req.origin_asset == WNEAR and req.destination_asset == USDC and req.amount == 5 * 10**23
    assert chain.decisions[0]['side'] == 'SELL' and chain.decisions[0]['notional_usd_cents'] == 199


def test_slippage_above_policy_is_journaled_as_rejected(tmp_path):
    agent, chain, _ = make(tmp_path, oneclick=FakeOneClick(slippage_out=49 * 10**22))  # $1.96 -> 200 bps
    row = agent.process(signal())
    d = chain.decisions[0]
    assert not d['allowed'] and d['reason'] == 'SLIPPAGE_ABOVE_MAX'
    assert d['agent_allowed'] is False and d['agent_reason'] == 'SLIPPAGE_ABOVE_MAX:200'
    assert chain.calls == ['record_decision'] and row['data']['result'] == 'REJECTED'


def test_cooldown_is_a_local_skip_but_pause_is_journaled(tmp_path):
    agent, chain, _ = make(tmp_path, chain=FakeChain(verdict='COOLDOWN'))
    assert agent.process(signal())['data']['skip_reason'] == 'GUARD_PRECHECK:COOLDOWN'
    assert chain.calls == []
    agent2, chain2, _ = make(tmp_path / 'b', chain=FakeChain(verdict='PAUSED'))
    agent2.process(signal())
    assert chain2.decisions[0]['reason'] == 'PAUSED' and chain2.decisions[0]['agent_allowed'] is False
    assert agent2.oneclick.quotes == [], 'no quote is requested once the guard says no'


def test_rerun_is_idempotent(tmp_path):
    agent, chain, events = make(tmp_path)
    agent.process(signal())
    agent.process(signal())
    assert chain.calls == ['record_decision', 'record_outcome']
    assert events[-1]['event'] == 'already_processed'


def test_crash_after_decision_sent_resumes_without_double_journaling(tmp_path):
    chain = FakeChain()
    chain.crash_after_send = 'record_decision'
    agent, chain, _ = make(tmp_path, chain=chain)
    with pytest.raises(ConnectionError):
        agent.process(signal())
    assert agent.store.get(signal().signal_hash('DRY_RUN'))['state'] == 'DECISION_PENDING'
    restarted, _, _ = make(tmp_path, chain=chain)  # same checkpoint file, new process
    row = restarted.process(signal())
    assert row['state'] == 'DONE'
    assert len(chain.decisions) == 1, 'the contract saw the same signal hash and returned the same decision'


def test_compromised_agent_is_overridden_by_the_contract(tmp_path):
    agent, chain, events = make(tmp_path)
    agent.process(signal(), compromised=True)
    d = chain.decisions[0]
    assert d['agent_allowed'] is True and d['notional_usd_cents'] == 500
    assert not d['allowed'] and d['reason'] == 'NOTIONAL_ABOVE_MAX'
    assert [e for e in events if e['event'] == 'decision_journaled'][0]['verdict_override'] is True


class FakeTrader:
    def __init__(self, fail=False, balance=10**30):
        self.sent, self.fail, self.balance = [], fail, balance

    def ft_balance(self, token, account):
        return self.balance

    def ft_transfer(self, token, receiver, amount, register):
        if self.fail:
            raise ConnectionError('killed while broadcasting')
        self.sent.append((token, receiver, amount))
        from types import SimpleNamespace
        return SimpleNamespace(transaction_hash='DepositTx')


def live_agent(tmp_path, tracking, **kw):
    return make(tmp_path, oneclick=FakeOneClick(tracking=tracking), live=True,
                trader=kw.pop('trader', FakeTrader()), **kw)


def test_live_success_records_actual_amounts_and_the_timeline(tmp_path):
    done = Tracking('dep-1', [update('PENDING_DEPOSIT'), update('PROCESSING'),
                              update('SUCCESS', {'amountIn': '2000000', 'amountOut': '499000000000000000000000'})])
    agent, chain, events = live_agent(tmp_path, [done])
    row = agent.process(signal())
    assert agent.oneclick.quotes[0].dry is False
    assert agent.trader_near.sent == [(USDC.removeprefix('nep141:'), 'dep-1', '2')]
    assert chain.decisions[0]['outcome']['status'] == 'SUCCESS'
    assert chain.decisions[0]['outcome']['amount_out'] == '499000000000000000000000'
    assert [t['status'] for t in row['data']['timeline']] == ['PENDING_DEPOSIT', 'PROCESSING', 'SUCCESS']
    assert agent.store.live_spent_cents() == 200


@pytest.mark.parametrize('final', ['REFUNDED', 'FAILED', 'INCOMPLETE_DEPOSIT'])
def test_live_refund_and_failure_record_zero_output(tmp_path, final):
    agent, chain, _ = live_agent(tmp_path, [Tracking('dep-1', [update(final, {'amountOut': '123'})])])
    agent.process(signal())
    expected = 'FAILED' if final == 'INCOMPLETE_DEPOSIT' else final
    assert chain.decisions[0]['outcome']['status'] == expected
    assert chain.decisions[0]['outcome']['amount_out'] == '0'


def test_live_timeout_resumes_tracking_on_the_next_run(tmp_path):
    timeout = Tracking('dep-1', [update('PROCESSING')], timed_out=True)
    done = Tracking('dep-1', [update('SUCCESS', {'amountIn': '2000000', 'amountOut': '5'})])
    agent, chain, _ = live_agent(tmp_path, [timeout, done])
    assert agent.process(signal())['state'] == 'DEPOSITED'
    assert chain.calls == ['record_decision']
    row = agent.process(signal())
    assert row['state'] == 'DONE' and len(agent.trader_near.sent) == 1, 'never deposits twice'


def test_live_success_without_amount_out_needs_review(tmp_path):
    agent, chain, _ = live_agent(tmp_path, [Tracking('dep-1', [update('SUCCESS', {})])])
    assert agent.process(signal())['state'] == 'NEEDS_REVIEW'
    assert chain.calls == ['record_decision']


def test_live_confirmation_mismatch_sends_nothing_and_frees_the_slot(tmp_path):
    def refuse(phrase, details=None):
        assert details['deposit address'] == 'dep-1' and 'worst-case slippage' in details
        raise LiveGateError('confirmation did not match')
    agent, chain, events = live_agent(tmp_path, [], confirm=refuse)
    row = agent.process(signal())
    assert agent.trader_near.sent == []
    assert chain.decisions[0]['outcome']['status'] == 'FAILED'
    assert chain.decisions[0]['outcome']['tx_ref'].startswith('aborted-before-deposit')
    assert row['state'] == 'DONE' and agent.store.live_spent_cents() == 0


def test_crash_during_deposit_is_parked_for_a_human_never_resent(tmp_path):
    agent, chain, _ = live_agent(tmp_path, [], trader=FakeTrader(fail=True))
    with pytest.raises(ConnectionError):
        agent.process(signal())
    h = signal().signal_hash('LIVE')
    assert agent.store.get(h)['state'] == 'DEPOSIT_PENDING'
    parked = agent.store.recover()
    assert [p['state'] for p in parked] == ['NEEDS_REVIEW']
    assert agent.store.live_spent_cents() == 200, 'a possibly-sent deposit counts toward the $5 cap'
    assert agent.process(signal())['state'] == 'NEEDS_REVIEW'


def test_live_requires_agent_key_and_trader_account(tmp_path):
    with pytest.raises(LiveGateError):
        Agent(AgentConfig(execution=ExecutionConfig(live=True, max_trade_usd=Decimal('2'))), FakeChain(),
              FakeOneClick(), CheckpointStore(tmp_path / 'x.sqlite'))


def test_live_exit_never_sells_more_than_the_balance(tmp_path):
    chain = FakeChain(positions=[{'asset': WNEAR, 'state': 'OPEN', 'amount': str(5 * 10**23)}])
    done = Tracking('dep-1', [update('SUCCESS', {'amountIn': '1', 'amountOut': '1990000'})])
    agent, chain, _ = make(tmp_path, chain=chain, oneclick=FakeOneClick(tracking=[done]), live=True,
                           trader=FakeTrader(balance=4 * 10**23))
    agent.process(signal('SHORT'))
    assert agent.oneclick.quotes[0].amount == 4 * 10**23
    assert chain.decisions[0]['quote_id'] == 'c1:dep-1', 'LIVE quote_id carries the deposit address'


def test_live_expired_quote_is_aborted_before_deposit(tmp_path):
    class Expired(FakeOneClick):
        def quote(self, req):
            q = super().quote(req)
            q.raw['quote']['deadline'] = '2000-01-01T00:00:00Z'
            return q
    agent, chain, _ = make(tmp_path, oneclick=Expired(tracking=[]), live=True, trader=FakeTrader())
    agent.process(signal())
    assert agent.trader_near.sent == []
    assert 'QUOTE_EXPIRED' in chain.decisions[0]['outcome']['tx_ref']


class ExitChain(FakeChain):
    """Like the contract: an exit needs an open position and skips the entry notional cap."""

    def check_trade(self, asset, side, notional, slippage):
        if side == 'SELL':
            return {'allowed': bool(self._positions), 'reason': 'OK' if self._positions else 'NO_OPEN_POSITION'}
        return super().check_trade(asset, side, notional, slippage)


class ProfitableExit(FakeOneClick):
    def quote(self, req):
        q = super().quote(req)
        q.raw['quote'].update(amountOut='2020000', minAmountOut='2018000')
        return Quote.from_api(q.raw)


class MeteredTrader(FakeTrader):
    """Also answers `account()`: the NEAR balance drops by 0.0015 NEAR per deposit."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.yocto = 10 ** 24

    def account(self, account_id):
        from types import SimpleNamespace
        return SimpleNamespace(amount=self.yocto)

    def ft_transfer(self, *a, **kw):
        self.yocto -= 1_500_000_000_000_000_000_000
        return super().ft_transfer(*a, **kw)


def test_live_profitable_exit_is_capped_by_the_entry_cost_not_the_proceeds(tmp_path):
    chain = ExitChain(positions=[{'asset': WNEAR, 'state': 'OPEN', 'amount': str(5 * 10**23), 'cost_usd_cents': 200}])
    done = Tracking('dep-1', [update('SUCCESS', {'amountIn': str(5 * 10**23), 'amountOut': '2020000'})])
    agent, chain, _ = make(tmp_path, chain=chain, oneclick=ProfitableExit(tracking=[done]), live=True,
                           trader=MeteredTrader())
    row = agent.process(signal('SHORT'))
    assert row['data']['result'] == 'SUCCESS' and len(agent.trader_near.sent) == 1  # $2.02 exit of a $2.00 entry
    assert int(row['data']['near_yocto_before']) - int(row['data']['near_yocto_after']) == 1_500_000_000_000_000_000_000


def test_live_exit_without_a_known_entry_cost_keeps_the_proceeds_cap(tmp_path):
    chain = ExitChain(positions=[{'asset': WNEAR, 'state': 'OPEN', 'amount': str(5 * 10**23)}])
    agent, chain, _ = make(tmp_path, chain=chain, oneclick=ProfitableExit(tracking=[]), live=True,
                           trader=FakeTrader())
    agent.process(signal('SHORT'))
    assert agent.trader_near.sent == [] and 'per-trade cap' in chain.decisions[0]['outcome']['tx_ref']
