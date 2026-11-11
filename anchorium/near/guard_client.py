"""Guard contract client (near-kit for Python).

* `GuardReader`: keyless view calls (policy, check_trade, stats, journal). Path A uses only this.
* `load_function_call_signer`: loads the agent's key ONLY if, on chain, it is a function-call key whose receiver
  is the guard and whose methods are a subset of record_decision/record_outcome. A full-access key is refused,
  even if it is the only key on disk.
* `GuardWriter`: record_decision / record_outcome signed with that key.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from near import KeyPairSigner, Near, parse_key

AGENT_METHODS = frozenset({'record_decision', 'record_outcome'})
#: rpc.{testnet,mainnet}.near.org are deprecated and rate-limit clients; FastNEAR is a listed public provider.
DEFAULT_RPC = {
    'testnet': 'https://rpc.testnet.fastnear.com',
    'mainnet': 'https://rpc.mainnet.fastnear.com',
}
EXPLORER = {
    'testnet': 'https://testnet.nearblocks.io',
    'mainnet': 'https://nearblocks.io',
}


class GuardKeyError(RuntimeError):
    pass


def tx_url(network: str, tx_hash: str) -> str:
    return f'{EXPLORER[network]}/txns/{tx_hash}'


def account_url(network: str, account_id: str) -> str:
    return f'{EXPLORER[network]}/address/{account_id}'


class GuardReader:
    def __init__(self, contract_id: str, network: str = 'testnet', rpc_url: str | None = None,
                 near: Near | None = None):
        self.contract_id = contract_id
        self.network = network
        self.near = near or Near(network, rpc_url=rpc_url or DEFAULT_RPC.get(network))

    def view(self, method: str, args: dict[str, Any] | None = None) -> Any:
        return self.near.view(self.contract_id, method, args or {})

    def policy(self) -> dict[str, Any]:
        return self.view('get_policy')

    def roles(self) -> dict[str, str]:
        return self.view('get_roles')

    def stats(self) -> dict[str, Any]:
        return self.view('get_stats')

    def positions(self) -> list[dict[str, Any]]:
        return self.view('get_positions')

    def check_trade(self, asset: str, side: str, notional_usd_cents: int, slippage_bps: int | None) -> dict[str, Any]:
        return self.view('check_trade', {'asset': asset, 'side': side, 'notional_usd_cents': notional_usd_cents,
                                         'slippage_bps': slippage_bps})

    def decisions(self, from_index: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        return self.view('get_decisions', {'from_index': from_index, 'limit': limit})

    def latest_decisions(self, count: int = 10) -> list[dict[str, Any]]:
        total = self.stats()['total_decisions']
        return self.decisions(max(0, total - count), count)

    def code_hash(self) -> str:
        return self.near.account(self.contract_id).code_hash


def load_function_call_signer(agent_id: str, guard_id: str, network: str = 'testnet',
                              credentials_dir: Path | None = None, near: Near | None = None) -> KeyPairSigner:
    near = near or Near(network, rpc_url=DEFAULT_RPC.get(network))
    base = Path(credentials_dir or Path.home() / '.near-credentials') / network / agent_id
    candidates = []
    for info in near.access_keys(agent_id):
        permission = info.access_key.permission
        if not isinstance(permission, dict) or 'FunctionCall' not in permission:
            continue  # full-access keys are never used by the agent
        fc = permission['FunctionCall']
        if fc.get('receiver_id') != guard_id or not set(fc.get('method_names') or []) <= AGENT_METHODS \
                or not fc.get('method_names'):
            continue
        public_key = str(info.public_key)
        path = base / (public_key.replace(':', '_') + '.json')
        if path.exists():
            candidates.append((public_key, path))
    if not candidates:
        raise GuardKeyError(f'no function-call key for {guard_id} limited to {sorted(AGENT_METHODS)} found on chain '
                            f'for {agent_id} with a key file in {base} (full-access keys are refused)')
    public_key, path = candidates[0]
    data = json.loads(path.read_text())
    key_pair = parse_key(data.get('private_key') or data.get('secret_key'))
    if str(key_pair.public_key) != public_key:
        raise GuardKeyError(f'key file {path.name} does not match its public key')
    return KeyPairSigner(account_id=agent_id, key_pair=key_pair)


@dataclass(frozen=True)
class Written:
    result: Any
    tx_hash: str
    logs: list[str]


class GuardWriter:
    def __init__(self, reader: GuardReader, signer: KeyPairSigner):
        self.reader = reader
        self.near = reader.near.with_signer(signer)
        self.agent_id = signer.account_id

    def _call(self, method: str, args: dict[str, Any]) -> Written:
        if method not in AGENT_METHODS:
            raise GuardKeyError(f'{method} is not an agent method')
        result = self.near.call(self.reader.contract_id, method, args, gas='50 Tgas')
        return Written(result=result.json_value(), tx_hash=result.transaction_hash or '', logs=result.logs)

    def record_decision(self, **args: Any) -> Written:
        return self._call('record_decision', args)

    def record_outcome(self, **args: Any) -> Written:
        return self._call('record_outcome', args)
