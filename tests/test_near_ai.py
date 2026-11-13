"""NEAR AI Cloud client: signed reviews verify offline, tampering fails, and the spend guardrails hold. No network."""
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from anchorium.ai import commands
from anchorium.ai.copilot import TRADE_SYSTEM, Copilot
from anchorium.ai.near_ai import (Guardrails, NearAIClient, NearAIUnavailable, UsageLedger, canonical,
                                  recover_eip191, sha256_hex, verify_attestation_record, verify_receipt)
from tests.near_ai_fake import ALLOW, MODEL, VETO, FakeNearAI, address, sign, fake_key

CTX = {'side': 'BUY', 'notional_usd': '2.00', 'close': '4.0', 'vwap': '3.9', 'atr_14': '0.01',
       'worst_case_slippage_bps': 30, 'policy_max_slippage_bps': 150, 'paypal_budget_usd': '10.00'}
ROOT = Path(__file__).resolve().parents[1]


def client(tmp_path, fake=None, **guard):
    fake = fake or FakeNearAI()
    c = NearAIClient('test-key', MODEL, guard=Guardrails(**guard) if guard else None,
                     ledger=UsageLedger(tmp_path / 'usage.json'), cache_dir=tmp_path / 'receipts',
                     transport=fake.transport(), sleep=lambda s: None)
    return c, fake


def failing(checks):
    return [c.step for c in checks if not c.ok]


def test_eip191_recovery_roundtrip():
    key = fake_key('x')
    assert recover_eip191('m:a:b', sign(key, 'm:a:b')) == address(key)
    assert recover_eip191('m:a:c', sign(key, 'm:a:b')) != address(key)


@pytest.mark.parametrize('kind', ['provider_tee', 'gateway'])
def test_signed_review_verifies_offline(tmp_path, kind):
    c, fake = client(tmp_path, FakeNearAI(VETO, kind=kind))
    r = c.review('TRADE', TRADE_SYSTEM, CTX)
    assert r.verified and r.verdict == 'VETO' and r.reason_code == 'THINMARGIN' and r.signature_kind == kind
    assert failing(verify_receipt(r.to_receipt())) == []
    assert r.rationale_hash == sha256_hex(canonical(r.to_receipt()))
    stored = json.loads((tmp_path / 'receipts' / f'{r.rationale_hash}.json').read_text())
    assert sha256_hex(canonical(stored)) == r.rationale_hash          # anyone can re-hash the stored receipt
    sent = json.loads(fake.requests[0])
    assert sent['temperature'] == 0 and sent['stream'] is False and sent['max_tokens'] == 300


def test_tampering_is_detected(tmp_path):
    c, _ = client(tmp_path)
    receipt = c.review('TRADE', TRADE_SYSTEM, CTX).to_receipt()
    bad_resp = json.loads(json.dumps(receipt))
    bad_resp['evidence']['response_body'] = bad_resp['evidence']['response_body'].replace('ALLOW', 'VETO')
    assert 'response hash' in failing(verify_receipt(bad_resp))
    bad_verdict = {**receipt, 'verdict': 'VETO'}
    assert failing(verify_receipt(bad_verdict)) == ['review from signed bytes']
    bad_sig = {**receipt, 'signature': sign(fake_key('attacker'), receipt['evidence']['signed_text'])}
    assert failing(verify_receipt(bad_sig)) == ['signature']


def test_wrong_signer_or_unbound_key_is_not_trusted(tmp_path):
    c, _ = client(tmp_path, FakeNearAI(wrong_signer=True))
    assert not c.review('TRADE', TRADE_SYSTEM, CTX).verified
    c2, _ = client(tmp_path / 'b', FakeNearAI(bad_binding=True))
    r = c2.review('TRADE', TRADE_SYSTEM, CTX)
    assert not r.verified and failing(verify_receipt(r.to_receipt())) == ['model TEE binding']
    copilot = Copilot(client(tmp_path / 'c', FakeNearAI(VETO, wrong_signer=True))[0])
    review = copilot.review_trade(CTX)
    assert review.provider == 'rules (TEE signature not verified)' and review.verdict == 'ALLOW'


def test_budget_caps_refuse_before_any_billable_call(tmp_path):
    c, fake = client(tmp_path, max_calls_per_run=1)
    c.review('TRADE', TRADE_SYSTEM, CTX)
    with pytest.raises(NearAIUnavailable, match='calls per run'):
        c.review('TRADE', TRADE_SYSTEM, {**CTX, 'close': '4.1'})
    assert fake.chat_calls == 1
    ledger = UsageLedger(tmp_path / 'usage.json')
    assert ledger.total_usd() == Decimal('0.00000015') * 400 + Decimal('0.00000055') * 60
    rich, fake2 = client(tmp_path, budget_usd=Decimal('0.0001'))  # lifetime total already near the cap
    copilot = Copilot(rich)
    assert copilot.review_trade({**CTX, 'close': '4.2'}).provider == 'rules (budget cap: USD)'
    assert fake2.chat_calls == 0


@pytest.mark.parametrize('status,text,reason,calls', [
    (402, '{"error": {"message": "No spending limit configured. Please contact support to set up credits."}}',
     'out of credits', 1),
    (429, '{"error": "slow down"}', 'rate limited', 1),
    (401, '{"error": "bad key"}', 'unauthorized', 1),
    (503, '{"error": "overloaded"}', 'HTTP 503', 2),     # one retry on 5xx, never a loop
])
def test_provider_errors_fall_back_to_rules(tmp_path, status, text, reason, calls):
    c, fake = client(tmp_path, FakeNearAI(chat_status=status, chat_text=text))
    review = Copilot(c).review_trade(CTX)
    assert review.provider == f'rules ({reason})' and review.verdict == 'ALLOW' and fake.chat_calls == calls


def test_identical_requests_are_paid_once(tmp_path):
    c, fake = client(tmp_path)
    a = c.review('TRADE', TRADE_SYSTEM, CTX)
    b = c.review('TRADE', TRADE_SYSTEM, CTX)
    assert fake.chat_calls == 1 and a.response_sha256 == b.response_sha256 and b.verified
    assert len(UsageLedger(tmp_path / 'usage.json').calls()) == 1


def test_copilot_uses_the_signed_receipt_and_cannot_loosen_rules(tmp_path):
    c, _ = client(tmp_path, FakeNearAI(VETO))
    review = Copilot(c, log_path=tmp_path / 'ai.jsonl').review_trade(CTX)
    assert review.provider == f'near-ai-cloud:{MODEL} (TEE, signature verified)' and review.vetoed
    assert review.rationale_hash == sha256_hex(canonical(review.receipt)) and review.to_dict()['tee_signed']
    c2, _ = client(tmp_path / 'b', FakeNearAI(ALLOW))
    loose = Copilot(c2).review_trade({**CTX, 'worst_case_slippage_bps': 140})  # rules veto: slippage near the cap
    assert loose.vetoed and loose.reason_code == 'SLIPPAGE_NEAR_CAP' and loose.receipt is not None


def test_explanations_are_signed_too(tmp_path):
    fake = FakeNearAI('{"explanation": "The contract overruled the agent."}')
    c, _ = client(tmp_path, fake)
    out = Copilot(c).explain_decision({'id': 2, 'agent_allowed': True, 'allowed': False, 'reason': 'NOTIONAL_ABOVE_MAX'})
    assert out['tee_signed'] and out['text'] == 'The contract overruled the agent.'


def test_no_key_means_rules_and_no_calls():
    c = Copilot.from_env({})
    assert c.client is None and c.provider == 'rules (no NEAR_AI_API_KEY)'
    assert c.review_trade(CTX).provider == 'rules (no NEAR_AI_API_KEY)'


def test_committed_attestation_fixture_verifies_offline():
    files = sorted((ROOT / 'fixtures' / 'near_ai').glob('attestation-*.json'))
    assert files, 'record one with: make near-ai-attest'
    for f in files:
        rec = json.loads(f.read_text())
        assert failing(verify_attestation_record(rec)) == []
        tampered = {**rec, 'nonce': 'cd' * 32}
        assert failing(verify_attestation_record(tampered))


def test_verify_ai_command_on_fixtures(capsys):
    class Args:
        target, full = None, False
    assert commands.cmd_verify_ai(Args()) == 0
    assert 'FAIL' not in capsys.readouterr().out


def test_tests_cannot_reach_near_ai():
    with pytest.raises(Exception, match='tried to reach cloud-api.near.ai'):
        httpx.get('https://cloud-api.near.ai/v1/models', timeout=5)
