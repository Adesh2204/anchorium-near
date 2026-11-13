"""`python -m anchorium vault-demo --owner X.testnet`: the vault releases money only for an ALLOWED on-chain decision.

Runs on the deployment made by scripts/near/deploy_vault_testnet.sh (`make vault-demo` does both): guard.X, vault.X
(accept_dry_run=true, the DEMO deployment), usdc.X (a TEST-ONLY mock token), agent.X with two function-call keys
(guard journaling; vault `execute`). Every step is a real NEAR testnet transaction and prints its explorer link.

  1. the agent journals a $4.99 BUY (over the $2 cap) -> the guard REJECTS it
  2. the agent asks the vault to pay for it                    -> vault_release_rejected NOT_ALLOWED, nothing moves
  3. the agent journals a $2.00 BUY -> ALLOWED; asks the vault  -> vault_release, the deposit address gets 2 mUSDC
  4. the same decision again                                    -> refused ALREADY_EXECUTED
  5. owner pause; a new allowed decision                        -> refused VAULT_PAUSED; owner withdraw still works
"""
from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

from near import ContractPanicError, Near

from anchorium.near.guard_client import DEFAULT_RPC, GuardReader, GuardWriter, load_function_call_signer, tx_url
from anchorium.near.vault_client import VaultClient, VaultExecutor, load_vault_signer, vault_events

NETWORK = 'testnet'
ASSET = 'nep141:wrap.near'


def _hash(label: str) -> str:
    return hashlib.sha256(f'anchorium-vault-demo:{label}:{time.time_ns()}'.encode()).hexdigest()


def _link(tx: str) -> str:
    return tx_url(NETWORK, tx) if tx else '(no tx hash)'


def _balance(near: Near, token: str, who: str) -> str:
    units = int(near.view(token, 'ft_balance_of', {'account_id': who}))
    return f'{units / 1_000_000:.6f} mUSDC'


def cmd_vault_demo(args: Any) -> int:
    owner = args.owner
    guard_id, vault_id, usdc, agent = f'guard.{owner}', f'vault.{owner}', f'usdc.{owner}', f'agent.{owner}'
    rpc = DEFAULT_RPC[NETWORK]
    reader = GuardReader(guard_id, NETWORK)
    writer = GuardWriter(reader, load_function_call_signer(agent, guard_id, NETWORK))
    vault = VaultClient(vault_id, NETWORK)
    executor = VaultExecutor(vault, load_vault_signer(agent, vault_id, NETWORK))
    owner_near = Near.from_file(owner, NETWORK, rpc_url=rpc)
    cfg = vault.config()
    print(f'=== Anchorium Vault demo on NEAR testnet (accept_dry_run={cfg["accept_dry_run"]}: DEMO deployment) ===')
    print(f'guard {guard_id} | vault {vault_id} | token {usdc} (TEST-ONLY mock) | agent {agent}')
    print(f'vault holds {_balance(vault.near, usdc, vault_id)}\n')
    deposit = secrets.token_hex(32)   # a fresh implicit account, like a 1Click deposit address
    print(f'demo deposit address: {deposit}\n')

    def decide(label: str, cents: int, asset: str = ASSET) -> dict[str, Any]:
        quote_id = f'demo-{label}:{deposit}'
        w = writer.record_decision(signal_hash=_hash(label), asset=asset, side='BUY', mode='DRY_RUN',
                                   notional_usd_cents=cents, slippage_bps=20, quote_id=quote_id, agent_allowed=True,
                                   agent_reason='VAULT_DEMO', rationale_hash=None)
        d = w.result
        verdict = 'ALLOWED' if d['allowed'] else f'REJECTED {d["reason"]}'
        print(f'[guard]  decision #{d["id"]} BUY {asset} ${cents / 100:.2f} -> {verdict}\n         {_link(w.tx_hash)}')
        return {**d, 'quote_id': quote_id}

    def execute(d: dict[str, Any], units: int, register: bool) -> None:
        try:
            r = executor.execute(d['id'], usdc, units, deposit, d['quote_id'], register=register)
            detail = f' {r.reason}' if r.reason else ''
            print(f'[vault]  execute #{d["id"]} {units / 1_000_000:.2f} mUSDC -> {r.status}{detail}\n         {_link(r.tx_hash)}')
        except ContractPanicError as exc:
            print(f'[vault]  execute #{d["id"]} -> refused by the contract: {exc.panic} (receipt {exc.receipt_id})')

    print('--- 1-2. a decision the guard rejected: the vault pays nothing ---')
    rejected = decide('over-cap', 499)
    execute(rejected, 2_000_000, register=True)
    print(f'         deposit address holds {_balance(vault.near, usdc, deposit) if _registered(vault.near, usdc, deposit) else "0 (not even registered)"}\n')

    print('--- 3. an ALLOWED decision: the vault releases exactly its notional ---')
    allowed = decide('allowed', 200)
    execute(allowed, 2_000_000, register=True)
    print(f'         deposit address now holds {_balance(vault.near, usdc, deposit)}; vault holds '
          f'{_balance(vault.near, usdc, vault_id)}\n')

    print('--- 4. the same decision again ---')
    execute(allowed, 2_000_000, register=False)
    print()

    print('--- 5. owner pause: no release; owner withdraw still works ---')
    res = owner_near.call(vault_id, 'pause', {}, gas='30 Tgas')
    print(f'[owner]  pause -> {[e["event"] for e in vault_events(list(res.logs))]}\n         {_link(res.transaction_hash or "")}')
    second = decide('while-paused', 150, asset='nep141:eth.bridge.near')  # another asset: the guard ALLOWS it
    execute(second, 1_500_000, register=False)
    res = owner_near.call(usdc, 'storage_deposit', {'account_id': owner, 'registration_only': True},
                          gas='30 Tgas', deposit='0.00125 NEAR')
    res = owner_near.call(vault_id, 'withdraw', {'token': usdc, 'amount': '1000000'}, gas='60 Tgas', deposit='1 yocto')
    print(f'[owner]  withdraw 1 mUSDC while paused -> {[e["event"] for e in vault_events(list(res.logs))]}\n'
          f'         {_link(res.transaction_hash or "")}')
    res = owner_near.call(vault_id, 'resume', {}, gas='30 Tgas', deposit='1 yocto')
    print(f'[owner]  resume (full-access key, 1 yocto) -> {[e["event"] for e in vault_events(list(res.logs))]}\n'
          f'         {_link(res.transaction_hash or "")}')
    daily = vault.daily()
    print(f'\nvault today: {daily["releases"]} release(s), {int(daily["quote_released"]) / 1_000_000:.2f} mUSDC released; '
          f'vault holds {_balance(vault.near, usdc, vault_id)}')
    print(f'explorer: https://testnet.nearblocks.io/address/{vault_id}')
    return 0


def _registered(near: Near, token: str, who: str) -> bool:
    return near.view(token, 'storage_balance_of', {'account_id': who}) is not None
