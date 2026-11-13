"""Anchorium Vault client: keyless views, and the agent's `execute` signed ONLY with a function-call key limited to the
vault's `execute` method (full-access keys are refused, exactly like the guard writer).

The vault (contracts/anchorium-vault) releases funds only for an ALLOWED decision journaled on the guard; this client
just asks. Every outcome is read back from the vault's own NEP-297 events, never assumed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from near import KeyPairSigner, Near, parse_key

from anchorium.near.guard_client import DEFAULT_RPC, GuardKeyError

VAULT_METHODS = frozenset({'execute'})
EXECUTE_GAS = '200 Tgas'          # the vault refuses less than 150 Tgas (MIN_EXECUTE_GAS)


@dataclass(frozen=True)
class Release:
    """What the vault did with one execute call, from its events."""
    status: str                    # RELEASED | REJECTED | FAILED
    reason: str | None
    tx_hash: str
    events: list[dict[str, Any]] = field(default_factory=list)


def vault_events(logs: list[str]) -> list[dict[str, Any]]:
    out = []
    for line in logs:
        if line.startswith('EVENT_JSON:'):
            ev = json.loads(line[len('EVENT_JSON:'):])
            if ev.get('standard') == 'anchorium_vault':
                out.append({'event': ev['event'], **(ev.get('data') or [{}])[0]})
    return out


def release_from_logs(logs: list[str], tx_hash: str) -> Release:
    events = vault_events(logs)
    names = [e['event'] for e in events]
    if 'vault_release_rejected' in names:
        reason = next(e.get('reason') for e in events if e['event'] == 'vault_release_rejected')
        return Release('REJECTED', reason, tx_hash, events)
    if 'release_failed' in names:
        return Release('FAILED', 'TOKEN_TRANSFER_FAILED', tx_hash, events)
    if 'vault_release' in names:
        return Release('RELEASED', None, tx_hash, events)
    return Release('FAILED', 'NO_VAULT_EVENT', tx_hash, events)


def load_vault_signer(agent_id: str, vault_id: str, network: str = 'testnet', credentials_dir: Path | None = None,
                      near: Near | None = None) -> KeyPairSigner:
    near = near or Near(network, rpc_url=DEFAULT_RPC.get(network))
    base = Path(credentials_dir or Path.home() / '.near-credentials') / network / agent_id
    for info in near.access_keys(agent_id):
        permission = info.access_key.permission
        if not isinstance(permission, dict) or 'FunctionCall' not in permission:
            continue  # full-access keys are never used by the agent
        fc = permission['FunctionCall']
        if fc.get('receiver_id') != vault_id or set(fc.get('method_names') or []) != VAULT_METHODS:
            continue
        public_key = str(info.public_key)
        path = base / (public_key.replace(':', '_') + '.json')
        if path.exists():
            data = json.loads(path.read_text())
            key_pair = parse_key(data.get('private_key') or data.get('secret_key'))
            if str(key_pair.public_key) != public_key:
                raise GuardKeyError(f'key file {path.name} does not match its public key')
            return KeyPairSigner(account_id=agent_id, key_pair=key_pair)
    raise GuardKeyError(f'no function-call key for {vault_id} limited to ["execute"] found on chain for {agent_id} '
                        f'with a key file in {base} (full-access keys are refused)')


class VaultClient:
    def __init__(self, vault_id: str, network: str = 'testnet', rpc_url: str | None = None, near: Near | None = None):
        self.vault_id, self.network = vault_id, network
        self.near = near or Near(network, rpc_url=rpc_url or DEFAULT_RPC.get(network))

    def config(self) -> dict[str, Any]:
        return self.near.view(self.vault_id, 'get_config', {})

    def is_executed(self, decision_id: int) -> bool:
        return bool(self.near.view(self.vault_id, 'is_executed', {'decision_id': decision_id}))

    def daily(self) -> dict[str, Any]:
        return self.near.view(self.vault_id, 'get_daily', {})

    def ft_balance(self, token: str) -> int:
        """What the vault holds of a NEP-141 token (base units)."""
        return int(self.near.view(token, 'ft_balance_of', {'account_id': self.vault_id}))


class VaultExecutor:
    """The agent's only way to move vault funds."""

    def __init__(self, client: VaultClient, signer: KeyPairSigner):
        self.client = client
        self.near = client.near.with_signer(signer)
        self.agent_id = signer.account_id

    def execute(self, decision_id: int, token: str, amount_units: int, deposit_address: str, quote_id: str,
                register: bool = False) -> Release:
        if amount_units <= 0:
            raise ValueError('amount must be positive')
        result = self.near.call(self.client.vault_id, 'execute', {
            'decision_id': decision_id, 'token': token, 'amount': str(amount_units),
            'deposit_address': deposit_address, 'quote_id': quote_id, 'register': register}, gas=EXECUTE_GAS)
        return release_from_logs(list(result.logs), result.transaction_hash or '')
