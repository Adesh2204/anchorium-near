"""Fiat on-ramp: PayPal sandbox client, funding ledger, the NEAR AI Cloud co-pilot and the orchestrator's veto hook.
No network: PayPal and NEAR AI Cloud are faked with httpx.MockTransport (see tests/near_ai_fake.py)."""
import hashlib
import json
from decimal import Decimal

import httpx
import pytest

from anchorium.ai.copilot import Copilot, Review, rules_topup_review, rules_trade_review
from anchorium.ai.near_ai import NearAIClient, UsageLedger
from anchorium.paypal.client import (Capture, PayPalClient, PayPalError, approve_url,
                                     parse_amount)
from anchorium.paypal.ledger import FundingLedger
from tests.near_ai_fake import ALLOW, MODEL, VETO, FakeNearAI
from tests.test_orchestrator import FakeChain, kinds, make, signal

# ------------------------------------------------------------------------------------------------ PayPal fakes


class FakePayPal:
    """Just enough of the Orders v2 sandbox API to exercise the client."""

    def __init__(self):
        self.orders, self.calls, self.capture_count = {}, [], 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path, request.headers.get('PayPal-Request-Id')))
        if path == '/v1/oauth2/token':
            assert request.headers['Authorization'].startswith('Basic ')
            return httpx.Response(200, json={'access_token': 'A21', 'expires_in': 32000})
        assert request.headers['Authorization'] == 'Bearer A21'
        if path == '/v2/checkout/orders' and request.method == 'POST':
            body = json.loads(request.content)
            oid = f'5O190127TN{len(self.orders):06d}'
            self.orders[oid] = {'body': body, 'status': 'CREATED'}
            return httpx.Response(201, json={'id': oid, 'status': 'CREATED', 'links': [
                {'rel': 'approve', 'href': f'https://www.sandbox.paypal.com/checkoutnow?token={oid}'}]})
        oid = path.split('/')[4]
        if oid not in self.orders:
            return httpx.Response(404, json={'name': 'RESOURCE_NOT_FOUND', 'details': [{'issue': 'INVALID_RESOURCE_ID'}]})
        unit = self.orders[oid]['body']['purchase_units'][0]
        if request.method == 'GET':
            return httpx.Response(200, json={'id': oid, 'status': self.orders[oid]['status']})
        self.capture_count += 1
        return httpx.Response(201, json={
            'id': oid, 'status': 'COMPLETED', 'payer': {'email_address': 'buyer@personal.example.com'},
            'purchase_units': [{'reference_id': unit['reference_id'], 'payments': {'captures': [{
                'id': f'CAP{oid[-6:]}', 'status': 'COMPLETED', 'custom_id': unit['custom_id'],
                'amount': {'currency_code': 'USD', 'value': unit['amount']['value']}}]}}]})


def client(fake=None):
    fake = fake or FakePayPal()
    return PayPalClient('cid', 'secret', transport=httpx.MockTransport(fake)), fake


def test_sandbox_only():
    with pytest.raises(PayPalError, match='sandbox only'):
        PayPalClient('cid', 'secret', base_url='https://api-m.paypal.com')
    with pytest.raises(PayPalError, match='required'):
        PayPalClient('', '')
    assert PayPalClient('cid', 's').client_id == 'cid'


@pytest.mark.parametrize('bad', ['0.99', '50.01', '1.001', 'abc', 'NaN', '-5'])
def test_amount_validation(bad):
    with pytest.raises(PayPalError):
        parse_amount(bad)
    assert parse_amount('10') == Decimal('10') and parse_amount('2.50') == Decimal('2.50')


def test_create_and_capture_order():
    pp, fake = client()
    order = pp.create_order('10.00', custom_id='anchorium-budget-abc')
    unit = fake.orders[order['id']]['body']['purchase_units'][0]
    assert fake.orders[order['id']]['body']['intent'] == 'CAPTURE'
    assert unit['amount'] == {'currency_code': 'USD', 'value': '10.00'} and unit['custom_id'] == 'anchorium-budget-abc'
    assert approve_url(order).startswith('https://www.sandbox.paypal.com/')
    cap = pp.capture_order(order['id'])
    assert cap.status == 'COMPLETED' and cap.amount_usd == Decimal('10.00') and cap.custom_id == 'anchorium-budget-abc'
    capture_call = [c for c in fake.calls if c[1].endswith('/capture')][0]
    assert capture_call[2] == f'capture-{order["id"]}'  # PayPal-Request-Id makes a retried capture idempotent
    assert [c[1] for c in fake.calls].count('/v1/oauth2/token') == 1  # token cached


def test_capture_errors_and_bad_ids():
    pp, _ = client()
    with pytest.raises(PayPalError, match='INVALID_RESOURCE_ID'):
        pp.capture_order('NOPE123')
    with pytest.raises(PayPalError, match='invalid order id'):
        pp.capture_order('../v1/oauth2')


# ------------------------------------------------------------------------------------------------ ledger


def cap(order='O1', amount='10.00', status='COMPLETED', custom='anchorium-budget-1'):
    return Capture(order, f'C-{order}', status, Decimal(amount), custom, None, {})


def test_ledger_credits_each_capture_once_and_tracks_spending():
    ledger = FundingLedger(':memory:')
    assert ledger.record_capture(cap()) is True
    assert ledger.record_capture(cap()) is False  # replayed capture / double click
    assert ledger.funded_cents() == 1000
    assert ledger.apply('sig1', 'BUY', -199) is True and ledger.apply('sig1', 'BUY', -199) is False
    assert ledger.balance_cents() == 801
    ledger.record_capture(cap('O2', '5.00', status='DECLINED'))
    assert ledger.funded_cents() == 1000  # only COMPLETED captures count


def test_ledger_rejects_foreign_captures():
    with pytest.raises(PayPalError, match='not an Anchorium top-up'):
        FundingLedger(':memory:').record_capture(cap(custom='someone-else'))


# ------------------------------------------------------------------------------------------------ AI co-pilot

CTX = {'side': 'BUY', 'notional_usd': '2.00', 'close': '4.0', 'vwap': '3.9', 'atr_14': '0.01',
       'worst_case_slippage_bps': 30, 'policy_max_slippage_bps': 150, 'paypal_budget_usd': '10.00'}


def near_ai(fake: FakeNearAI, tmp_path):
    return NearAIClient('test-key', MODEL, ledger=UsageLedger(tmp_path / 'usage.json'), cache_dir=tmp_path / 'r',
                        transport=fake.transport(), sleep=lambda s: None)


def test_rules_reviewer():
    assert rules_trade_review(CTX).verdict == 'ALLOW'
    assert rules_trade_review({**CTX, 'worst_case_slippage_bps': 130}).reason_code == 'SLIPPAGE_NEAR_CAP'
    assert rules_trade_review({**CTX, 'close': '3.8'}).reason_code == 'BELOW_VWAP'
    assert rules_trade_review({**CTX, 'paypal_budget_usd': '1.00'}).reason_code == 'BUDGET_SHORT'
    top = {'amount_usd': '10.00', 'balance_usd': '0.00', 'max_trade_usd': '2.00', 'daily_loss_cap_usd': '1.00'}
    assert rules_topup_review(top).verdict == 'ALLOW'
    assert rules_topup_review({**top, 'amount_usd': '50.00', 'balance_usd': '10.00'}).reason_code == 'OVERFUNDING'
    assert rules_topup_review({**top, 'topups_last_hour': 3}).reason_code == 'TOO_MANY_TOPUPS'


def test_model_review_is_used_and_hashed(tmp_path):
    c = Copilot(near_ai(FakeNearAI(VETO), tmp_path), log_path=tmp_path / 'ai.jsonl')
    r = c.review_trade(CTX)
    assert (r.verdict, r.risk_score, r.reason_code) == ('VETO', 72, 'THINMARGIN')
    assert r.provider == f'near-ai-cloud:{MODEL} (TEE, signature verified)'
    assert r.rationale_hash == hashlib.sha256(r.canonical().encode()).hexdigest() and len(r.rationale_hash) == 64
    logged = json.loads((tmp_path / 'ai.jsonl').read_text().splitlines()[0])
    assert hashlib.sha256(logged['canonical'].encode()).hexdigest() == r.rationale_hash  # anyone can re-hash it


def test_model_cannot_loosen_a_rules_veto(tmp_path):
    r = Copilot(near_ai(FakeNearAI(ALLOW), tmp_path)).review_trade({**CTX, 'worst_case_slippage_bps': 140})
    assert r.vetoed and r.reason_code == 'SLIPPAGE_NEAR_CAP'


@pytest.mark.parametrize('reply,status,label', [('not json', 200, 'rules (ValueError)'),
                                                ('{"verdict": "MAYBE", "explanation": "x"}', 200,
                                                 "rules (bad verdict 'MAYBE')"),
                                                (ALLOW, 503, 'rules (HTTP 503)')])
def test_model_failure_falls_back_to_rules(tmp_path, reply, status, label):
    c = Copilot(near_ai(FakeNearAI(reply, chat_status=status), tmp_path))
    r = c.review_trade(CTX)
    assert r.provider == label and r.verdict == 'ALLOW' and c.last_error


def test_no_key_means_rules_and_explanations_work():
    c = Copilot(None)
    assert c.provider == 'rules (no NEAR_AI_API_KEY)'
    x = c.explain_decision({'id': 2, 'asset': 'wrap.near', 'side': 'BUY', 'mode': 'DRY_RUN', 'notional_usd_cents': 499,
                            'agent_allowed': True, 'allowed': False, 'reason': 'NOTIONAL_ABOVE_MAX'})
    assert 'overruled' in x['text'] and x['provider'] == 'rules (no NEAR_AI_API_KEY)' and not x['tee_signed']


# ------------------------------------------------------------------------------------------------ orchestrator hook


class StubCopilot:
    def __init__(self, verdict):
        self.verdict, self.contexts = verdict, []

    def review_trade(self, ctx):
        self.contexts.append(ctx)
        return Review('TRADE', self.verdict, 50, 'STUB', 'stub review', 'stub')


def test_ai_veto_turns_the_agent_vote_into_no(tmp_path):
    agent, chain, events = make(tmp_path)
    agent.copilot = StubCopilot('VETO')
    agent.copilot_context = lambda: {'paypal_budget_usd': '8.00'}
    row = agent.process(signal())
    d = chain.decisions[0]
    assert row['data']['result'] == 'REJECTED' and not d['allowed'] and d['reason'] == 'AGENT_REJECTED'
    assert d['agent_reason'] == 'AI_VETO:STUB' and len(d['rationale_hash']) == 64
    assert kinds(events) == ['signal', 'guard_check', 'quote', 'ai_review', 'decision_journaled']
    assert agent.copilot.contexts[0]['paypal_budget_usd'] == '8.00'


def test_ai_allow_cannot_override_the_contract(tmp_path):
    agent, chain, _ = make(tmp_path, chain=FakeChain(verdict='PAUSED'))
    agent.copilot = StubCopilot('ALLOW')
    agent.process(signal())
    assert not chain.decisions[0]['allowed'] and agent.copilot.contexts == []  # guard said no: AI not even asked


def test_ai_allow_keeps_the_trade_and_journals_the_hash(tmp_path):
    agent, chain, _ = make(tmp_path)
    agent.copilot = StubCopilot('ALLOW')
    row = agent.process(signal())
    assert row['data']['result'] == 'DRY_RUN' and chain.decisions[0]['allowed']
    assert chain.decisions[0]['rationale_hash'] == Review('TRADE', 'ALLOW', 50, 'STUB', 'stub review',
                                                          'stub').rationale_hash


def test_compromised_agent_skips_the_ai_and_the_contract_still_rejects(tmp_path):
    agent, chain, _ = make(tmp_path)
    agent.copilot = StubCopilot('VETO')
    agent.process(signal(), compromised=True)
    d = chain.decisions[0]
    assert agent.copilot.contexts == [] and d['agent_allowed'] and not d['allowed'] and d['reason'] == 'NOTIONAL_ABOVE_MAX'


def test_without_copilot_rationale_hash_stays_null(tmp_path):
    agent, chain, _ = make(tmp_path)
    agent.process(signal())
    assert chain.decisions[0]['rationale_hash'] is None


# ------------------------------------------------------------------------------------------------ dashboard routes


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from dashboard import app as dash
    from dashboard import paypal_routes as pr
    pp, fake = client()
    monkeypatch.setattr(pr.State, 'paypal', pp)
    monkeypatch.setattr(pr.State, 'ledger', FundingLedger(':memory:'))
    monkeypatch.setattr(pr.State, 'copilot', Copilot(None))
    monkeypatch.setattr(pr.State, 'ready', True)
    monkeypatch.setattr(pr.State, 'explained', {})

    class Reader:
        contract_id = 'guard.test'
        def policy(self): return {'max_notional_per_trade_usd_cents': 200, 'max_daily_loss_usd_cents': 100,
                                  'max_trades_per_day': 10, 'paused': False}
        def decisions(self, i, n): return [{'id': i, 'asset': 'wrap.near', 'side': 'BUY', 'mode': 'DRY_RUN',
                                            'notional_usd_cents': 499, 'agent_allowed': True, 'allowed': False,
                                            'reason': 'NOTIONAL_ABOVE_MAX'}]
    monkeypatch.setattr(pr.State, 'reader', Reader())
    return TestClient(dash.app), fake


def test_dashboard_checkout_flow(api):
    http, fake = api
    assert http.get('/api/paypal/config').json()['mode'] == 'sandbox'
    o = http.post('/api/paypal/orders', json={'amount': '10.00'}).json()
    assert o['review']['verdict'] == 'ALLOW' and o['status'] == 'CREATED'
    c = http.post(f'/api/paypal/orders/{o["id"]}/capture').json()
    assert c['credited'] and c['ledger']['funded_usd'] == '10.00'
    again = http.post(f'/api/paypal/orders/{o["id"]}/capture').json()
    assert again['credited'] is False and again['ledger']['funded_usd'] == '10.00'


def test_dashboard_ai_vetoes_overfunding_before_any_order(api):
    http, fake = api
    for _ in range(2):
        o = http.post('/api/paypal/orders', json={'amount': '25.00'}).json()
        http.post(f'/api/paypal/orders/{o["id"]}/capture')
    r = http.post('/api/paypal/orders', json={'amount': '10.00'})  # $60 > 25 trades x $2
    assert r.status_code == 409 and r.json()['detail']['review']['reason_code'] == 'OVERFUNDING'
    assert len(fake.orders) == 2  # no third order was created


def test_dashboard_refuses_unknown_orders_and_bad_amounts(api):
    http, _ = api
    assert http.post('/api/paypal/orders/ABC123/capture').status_code == 404
    assert http.post('/api/paypal/orders', json={'amount': '0.10'}).status_code == 400
    assert http.post('/api/paypal/simulate', json={'amount': '5.00'}).status_code == 400  # keys set: no simulation


def test_dashboard_explains_decisions(api):
    http, _ = api
    x = http.get('/api/ai/explain/2').json()
    assert x['decision_id'] == 2 and 'overruled' in x['text']
    assert http.get('/paypal').status_code == 200


def test_derived_facts_are_exact():
    from anchorium.ai.copilot import derived_facts
    f = derived_facts({'close': '74328.0', 'vwap': '74295.5382', 'atr_14': '93.5714', 'worst_case_slippage_bps': 61,
                       'policy_max_slippage_bps': 150, 'notional_usd': '1.99', 'policy_max_trade_usd': '2.00',
                       'paypal_budget_usd': '1.50'})
    assert f['price_vs_vwap'] == 'ABOVE' and f['slippage_pct_of_cap'] == '41%' and f['notional_within_cap'] is True
    assert f['budget_covers_trade'] is False and f['atr_pct_of_price'] == '0.126%'
