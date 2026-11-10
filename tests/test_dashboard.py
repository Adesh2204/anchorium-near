"""Dashboard API with a fake contract reader and fake 1Click (no network)."""
import pytest
from fastapi.testclient import TestClient

from anchorium.near.intents import FUNDS_LOCATION, StatusUpdate
import dashboard.app as dash

BOOK = {'decisions': 1, 'allowed': 1, 'rejected': 0, 'verdict_overrides': 0, 'settled': 1, 'refunded': 0,
        'failed': 0, 'closed_trades': 0, 'wins': 0, 'losses': 0, 'realized_pnl_usd_cents': 0,
        'peak_pnl_usd_cents': 0, 'max_drawdown_usd_cents': 0}


def decision(i, mode='DRY_RUN', allowed=True, quote_id='corr', outcome=None):
    return {'id': i, 'timestamp_ms': 1_762_790_000_000, 'signal_hash': f'{i:064x}', 'asset': 'nep141:wrap.near',
            'side': 'BUY', 'mode': mode, 'notional_usd_cents': 199, 'slippage_bps': 30, 'quote_id': quote_id,
            'agent_allowed': True, 'agent_reason': 'OK', 'rationale_hash': None, 'allowed': allowed,
            'reason': 'OK' if allowed else 'PAUSED', 'outcome': outcome}


class FakeReader:
    def __init__(self, decisions):
        self._d = decisions

    def code_hash(self): return '9G9w6hEm'
    def roles(self): return {'owner': 'o.testnet', 'agent': 'a.o.testnet'}
    def policy(self): return {'paused': True, 'max_slippage_bps': 100}
    def stats(self): return {'total_decisions': len(self._d), 'dry_run': BOOK, 'live': BOOK, 'paused': True,
                             'open_positions': 0, 'today': {'day': 1, 'decisions': 1, 'entries': 1,
                                                            'realized_pnl_usd_cents': 0}}
    def positions(self): return []
    def latest_decisions(self, n): return self._d[-n:]


class FakeOneClick:
    calls = []

    def status(self, address):
        self.calls.append(address)
        return StatusUpdate('PROCESSING', '2026-11-10T00:00:00Z', 'now', FUNDS_LOCATION['PROCESSING'], {})


@pytest.fixture
def client(monkeypatch):
    rows = [decision(0), decision(1, allowed=False), decision(2, mode='LIVE', quote_id='corr-9:abc123')]
    monkeypatch.setattr(dash, 'reader', FakeReader(rows))
    monkeypatch.setattr(dash, 'oneclick', FakeOneClick())
    monkeypatch.setattr(dash, 'local_events', lambda: {})
    dash._cache.clear()
    return TestClient(dash.app)


def test_overview_reads_policy_and_kill_switch_from_the_contract(client):
    o = client.get('/api/overview').json()
    assert o['policy']['paused'] is True and o['code_hash'] == '9G9w6hEm'
    assert o['banner'] == 'DRY RUN' and o['live'] is False


def test_funds_timeline_per_decision(client):
    rows = {r['id']: r for r in client.get('/api/decisions').json()}
    assert [r['id'] for r in client.get('/api/decisions').json()] == [2, 1, 0], 'newest first'
    assert rows[0]['funds_timeline'][0]['status'] == 'DRY_RUN'
    assert rows[1]['funds_timeline'][0]['status'] == 'NOT_SENT'
    live = rows[2]
    assert live['funds_timeline'][-1] == {'status': 'PROCESSING', 'at': '2026-11-10T00:00:00Z',
                                          'funds_location': FUNDS_LOCATION['PROCESSING']}
    assert live['intents_explorer'].endswith('search=abc123')
    assert dash.oneclick.calls[-1] == 'abc123', 'deposit address parsed from the on-chain quote_id'


def test_intent_endpoint_validates_the_address(client):
    assert client.get('/api/intent/abc123').json()['status'] == 'PROCESSING'
    assert client.get('/api/intent/bad%20addr').status_code == 400


def test_index_is_served(client):
    r = client.get('/')
    assert r.status_code == 200 and 'Anchorium Guard' in r.text
