"""Vault client: event parsing, the execute-only key rule and the call it signs. No network."""
import json
from types import SimpleNamespace

import pytest

from anchorium.near.guard_client import GuardKeyError
from anchorium.near.vault_client import (EXECUTE_GAS, VaultClient, VaultExecutor, load_vault_signer,
                                         release_from_logs, vault_events)


def ev(event, **data):
    return 'EVENT_JSON:' + json.dumps({'standard': 'anchorium_vault', 'version': '1.0.0', 'event': event,
                                       'data': [data]})


def test_events_are_read_from_the_vault_standard_only():
    logs = [ev('vault_release', decision_id=1), 'plain log',
            'EVENT_JSON:' + json.dumps({'standard': 'nep141', 'event': 'ft_transfer', 'data': [{}]})]
    assert vault_events(logs) == [{'event': 'vault_release', 'decision_id': 1}]


@pytest.mark.parametrize('logs, status, reason', [
    ([ev('vault_release', decision_id=1)], 'RELEASED', None),
    ([ev('vault_release_rejected', decision_id=1, reason='NOT_ALLOWED')], 'REJECTED', 'NOT_ALLOWED'),
    ([ev('vault_release', decision_id=1), ev('release_failed', decision_id=1)], 'FAILED', 'TOKEN_TRANSFER_FAILED'),
    ([], 'FAILED', 'NO_VAULT_EVENT'),
])
def test_release_status_comes_from_the_events(logs, status, reason):
    r = release_from_logs(logs, 'Tx')
    assert (r.status, r.reason, r.tx_hash) == (status, reason, 'Tx')


class FakeNear:
    def __init__(self, keys=(), view=None):
        self.keys, self.calls, self._view = list(keys), [], view or {}

    def access_keys(self, account):
        return self.keys

    def with_signer(self, signer):
        self.signer = signer
        return self

    def view(self, contract, method, args):
        return self._view[method]

    def call(self, contract, method, args, gas):
        self.calls.append((contract, method, args, gas))
        return SimpleNamespace(logs=[ev('vault_release', decision_id=args['decision_id'])], transaction_hash='Tx1')


def key(public_key, receiver='vault.t.testnet', methods=('execute',)):
    return SimpleNamespace(public_key=public_key, access_key=SimpleNamespace(
        permission={'FunctionCall': {'receiver_id': receiver, 'method_names': list(methods)}}))


def test_signer_refuses_full_access_and_keys_for_other_methods(tmp_path):
    full = SimpleNamespace(public_key='ed25519:x', access_key=SimpleNamespace(permission='FullAccess'))
    near = FakeNear([full, key('ed25519:y', methods=('execute', 'withdraw')), key('ed25519:z', receiver='other')])
    with pytest.raises(GuardKeyError, match='full-access keys are refused'):
        load_vault_signer('agent.t.testnet', 'vault.t.testnet', credentials_dir=tmp_path, near=near)


def test_signer_loads_the_execute_only_key(tmp_path):
    from near import Ed25519KeyPair
    kp = Ed25519KeyPair.generate()  # throwaway test key, generated at run time
    folder = tmp_path / 'testnet' / 'agent.t.testnet'
    folder.mkdir(parents=True)
    (folder / (str(kp.public_key).replace(':', '_') + '.json')).write_text(json.dumps({'private_key': str(kp.secret_key)}))
    signer = load_vault_signer('agent.t.testnet', 'vault.t.testnet', credentials_dir=tmp_path,
                               near=FakeNear([key(str(kp.public_key))]))
    assert signer.account_id == 'agent.t.testnet'


def test_execute_sends_exactly_the_decision_and_enough_gas():
    near = FakeNear(view={'ft_balance_of': '42', 'is_executed': True})
    client = VaultClient('vault.t.testnet', near=near)
    executor = VaultExecutor(client, SimpleNamespace(account_id='agent.t.testnet'))
    r = executor.execute(7, 'usdc.t.testnet', 2_000_000, 'ab' * 32, 'c1:' + 'ab' * 32, register=True)
    assert r.status == 'RELEASED' and r.tx_hash == 'Tx1'
    assert near.calls == [('vault.t.testnet', 'execute', {
        'decision_id': 7, 'token': 'usdc.t.testnet', 'amount': '2000000', 'deposit_address': 'ab' * 32,
        'quote_id': 'c1:' + 'ab' * 32, 'register': True}, EXECUTE_GAS)]
    assert client.ft_balance('usdc.t.testnet') == 42 and client.is_executed(7) is True
    with pytest.raises(ValueError):
        executor.execute(7, 'usdc.t.testnet', 0, 'ab' * 32, 'q')
