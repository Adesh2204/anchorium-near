# Five-minute verification

Each step says what to run and what you should see. Nothing here needs keys or funds. Live values (quotes,
prices, decision counts) change over time. The shape of the output and the verdicts should not.

## 0. Setup (1 min)

```sh
git clone https://github.com/Adesh2204/anchorium-near-Paypal && cd anchorium-near-Paypal
make setup            # -> setup OK: Python 3.x.y   (needs Python >= 3.12; `make setup PY=python3.12` if your default python3 is older)
```

pip prints a setuptools `BuildBackendWarning: Editable installation` notice. It is harmless.

## 1. The strategy is prior work, copied unchanged (10 s)

```sh
make freeze
```

Expected (abridged):

```json
  "vendored_files_verified": 13,
  "source_parameter_count": 32,
  "source_method_count": 8,
  ...
  "vendored_verify_strategy_freeze": { "baseline": "nse-working-baseline", "parameter_count": 28, "passed": true },
  "failures": [],
  "passed": true
```

28 of the 32 frozen parameters live in the vendored files. The other 4, and all 8 frozen methods, belong to legacy
modules that were not copied. The checker shows they are recorded unchanged in the manifest, not silently
dropped. See `vendor/anchorium_strategy/SOURCE.md`.

## 2. Path A demo against the live contract (15 s)

```sh
make demo
```

Expected, from a real run on 2026-11-11 (quote ids, amounts and slippage will differ):

```text
=== Anchorium Path A: zero keys, zero funds | DRY RUN ===
guard contract : demo.anchorium-team.testnet (testnet)  https://testnet.nearblocks.io/address/demo.anchorium-team.testnet
owner / agent  : anchorium-team.testnet / agent.anchorium-team.testnet
code_hash      : 9G9w6hEmWJ9VmJ2R5BavB4gfGmZwUCCGYvhLPDyqQGfT (compare with contracts/anchorium-guard/res/SHA256SUMS)
policy         : max $2.00/trade, 10 entries/day, daily loss cap $1.00, max slippage 100 bps, cooldown 60 s, paused=False
1Click         : no JWT (observed fee 20 bps; docs say 0.25%)

--- REPLAY near-entry: recorded bar closing 2026-11-10 15:08 UTC ---
[signal]  NEAR BREAKOUT_LONG close 3.7188 (bar 2026-11-10 15:08 UTC, VWAP 3.6416, ATR 0.0147) hash 32aec4e1f8...
[guard]   check_trade BUY nep141:wrap.near $1.99 -> OK
[quote]   1Click dry quote 2 USDC -> 0.550649 wNEAR (min 0.550099); worst case 11 bps <= policy 100 bps; fee 20 bps; quote be5db9a3-...
[journal] (Path A, no key) would call demo.anchorium-team.testnet.record_decision(side=BUY, notional_usd_cents=199, slippage_bps=11, agent_allowed=True, agent_reason=OK)
... (same for btc-entry and eth-entry) ...

--- Simulated compromised agent: asks for 2.5x the cap and claims it is allowed ---
[guard]   check_trade BUY nep141:wrap.near $4.99 -> NOTIONAL_ABOVE_MAX
[journal] (Path A, no key) would call demo.anchorium-team.testnet.record_decision(side=BUY, notional_usd_cents=499, slippage_bps=0, agent_allowed=True, agent_reason=COMPROMISED_AGENT_SIMULATION)

Checks above ran against demo.anchorium-team.testnet (same code_hash, never journaled: total_decisions=0).
Real on-chain journal written by the agent (guard.anchorium-team.testnet): https://testnet.nearblocks.io/address/guard.anchorium-team.testnet
  #0 DRY_RUN BUY nep141:wrap.near -> ALLOWED
  #1 DRY_RUN SELL nep141:wrap.near -> REJECTED SLIPPAGE_ABOVE_MAX
  #2 DRY_RUN BUY nep141:wrap.near -> REJECTED NOTIONAL_ABOVE_MAX
  #3 DRY_RUN SELL nep141:wrap.near -> REJECTED SLIPPAGE_ABOVE_MAX
  #4 DRY_RUN SELL nep141:wrap.near -> ALLOWED
Stats read from the contract: decisions=5, paper closed trades=1 (wins 1, losses 0, PnL +0.03 USD paper), live trades=0
```

What this shows:

- **Signals:** the frozen strategy's verdicts on recorded Hyperliquid candles (`fixtures/replay/`).
- **Checks:** `check_trade` is a real view call on the deployed wasm. `demo.anchorium-team.testnet` runs the
  same code hash but nothing journals to it, so its state stays flat and the demo is repeatable. The output above
  was recorded before the owner raised both guards' slippage cap from 100 to 150 bps (demo guard:
  [7MmqfBAD…](https://testnet.nearblocks.io/txns/7MmqfBADhymYuccmniMxano2EWGURb6qtYoEyeVip7MN)), so a run today
  prints `max slippage 150 bps`.
- **Quotes:** real 1Click dry quotes.
- **Journal:** read from the main guard, `guard.anchorium-team.testnet`. It will have more entries than shown
  here.

## 3. Read the contract yourself (30 s)

```sh
make status
```

The output is JSON with the policy (`max_slippage_bps: 150` on the main guard), the roles, `get_stats` (paper and
live books kept separately) and the latest decisions. Open the explorer link for any decision: the transaction
is signed by `agent.anchorium-team.testnet` and its logs carry `EVENT_JSON` NEP-297 events
(`decision_recorded`, `verdict_override`, `outcome_recorded`).

## 4. The agent can only journal (20 s)

```sh
curl -s https://rpc.testnet.fastnear.com -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"query",
  "params":{"request_type":"view_access_key_list","finality":"final","account_id":"agent.anchorium-team.testnet"}}'
```

Expected: one key, `ed25519:6Uv5zLri3SzPNx15BV8jATcjVjPcWndD4GoVZBKx5xfz`, with
`"FunctionCall": {"receiver_id": "guard.anchorium-team.testnet", "method_names": ["record_decision",
"record_outcome"], ...}`. There is no full-access key; its deletion tx is
[86WYN7Ro…](https://testnet.nearblocks.io/txns/86WYN7RoR1PNERjpSzspQXJ6uDX2fqnGP4m3LLrGyPq8).

## 5. The deployed code is the committed code (20 s)

```sh
shasum -a 256 contracts/anchorium-guard/res/anchorium_guard.wasm
# 7abf4eccdf522e8949f5b5b86ab7939673fb9e87169b1adaecbfe2f82677c66a
python3 -c "import hashlib;b=hashlib.sha256(open('contracts/anchorium-guard/res/anchorium_guard.wasm','rb').read()).digest();A='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';n=int.from_bytes(b,'big');s='';
while n: n,r=divmod(n,58); s=A[r]+s
print(s)"
# 9G9w6hEmWJ9VmJ2R5BavB4gfGmZwUCCGYvhLPDyqQGfT  == the code_hash printed by `make demo`
```

## 6. NEAR AI Cloud evidence, offline (5 s, zero keys, zero cost)

```sh
make verify-ai
```

Expected (real output, 13 Nov 2026):

```text
== attestation attestation-qwen-qwen3-vl-30b-a3b-instruct-2026-11-13.json (Qwen/Qwen3-VL-30B-A3B-Instruct, fetched 2026-11-13T08:41:48+00:00)
  PASS  report bytes               sha256 = 0e87ce8a6eb86561...
  PASS  model TEE nodes            1 node(s) serving Qwen/Qwen3-VL-30B-A3B-Instruct
  PASS  model node 0 TEE binding   TDX v4 report_data = 0x2665cce942fa427ef30d87f4b0bff67fb7645462 + our nonce
  PASS  gateway TEE binding        TDX v4 report_data = 0xb98a789cdc4817275f5155593ba73ef2a957e779 + our nonce
  => PASS

1/1 PASS
```

`make verify-ai ARGS="--full"` also sends the GPU evidence to NVIDIA NRAS (online, free) and checks that its overall
result is `true` and that NVIDIA saw our nonce. No signed review is committed yet: NEAR AI Cloud answered HTTP 402
(no credits) on 13 Nov; see [NEAR_AI_CLOUD.md](NEAR_AI_CLOUD.md).

## 7. Tests (1–2 min)

```sh
make test-py          # -> 454 passed, 1 deselected
make contract-test    # -> 40 passed (unit), 2 passed (sandbox); needs Rust 1.95 + downloads a local NEAR node
make vault-test       # -> 16 passed (unit), 1 passed (sandbox)
```

## 8. Optional: your own contract, on-chain (3 min)

```sh
OWNER=judge-$RANDOM.testnet make demo-onchain   # needs near-cli-rs 0.31.1; free testnet faucet account
```

You get the same replay, but every decision and paper outcome is a real transaction on **your** `guard.OWNER`,
each printed with an explorer link.

## 9. Optional: the Vault on your own testnet accounts (a few minutes)

```sh
OWNER=judge-$RANDOM.testnet make vault-demo   # needs near-cli-rs 0.31.1; free testnet faucet account
```

Expected (abridged: the account and deposit-address lines are left out), from the real run on 13 Nov 2026
(`OWNER=anchorium-vault-11674.testnet`; your accounts, deposit address
and hashes will differ):

```text
=== Anchorium Vault demo on NEAR testnet (accept_dry_run=True: DEMO deployment) ===
vault holds 10.000000 mUSDC

--- 1-2. a decision the guard rejected: the vault pays nothing ---
[guard]  decision #0 BUY $4.99 -> REJECTED NOTIONAL_ABOVE_MAX
         https://testnet.nearblocks.io/txns/9rqe5bTbuF8cCwh9f15nzbMq7dq9UrFFarWXRjuhvjmX
[vault]  execute #0 2.00 mUSDC -> REJECTED NOT_ALLOWED
         https://testnet.nearblocks.io/txns/r1SYretiMuPAmijQgHSnbVEnLZb5YZ7WFmp72i1dk6B
         deposit address holds 0 (not even registered)

--- 3. an ALLOWED decision: the vault releases exactly its notional ---
[guard]  decision #1 BUY $2.00 -> ALLOWED
         https://testnet.nearblocks.io/txns/51LDEvkMvab1873TuRAGXJsSeg4QefrU3F56FQBFb9uM
[vault]  execute #1 2.00 mUSDC -> RELEASED
         https://testnet.nearblocks.io/txns/AX9vpHMmtJ8CpHxcwJiMjXGxSQA3gVXoLQ9xtVBDn9Yb
         deposit address now holds 2.000000 mUSDC; vault holds 8.000000 mUSDC

--- 4. the same decision again ---
[vault]  execute #1 -> refused by the contract: Smart contract panicked: ALREADY_EXECUTED (receipt GBFshR8YCsWtV6FYja99fTkySnFtyn24dB13wU66uh8n)

--- 5. owner pause: no release; owner withdraw still works ---
[owner]  pause -> ['paused']
         https://testnet.nearblocks.io/txns/A9JBJDCrYTZpwctPP5Abh1KhBUBW6NQ2FYqCiZAmjREX
[guard]  decision #2 BUY $1.50 -> REJECTED POSITION_ALREADY_OPEN
         https://testnet.nearblocks.io/txns/9RKEbV64oyHAhkvVyUAu2uxEZSkPgYndVUmiYYZCHHYF
[vault]  execute #2 -> refused by the contract: Smart contract panicked: VAULT_PAUSED (receipt 6LGd7WeoHtEve2NN8oriaXEZqLtKF4q9YGtV7byjDNvJ)
[owner]  withdraw 1 mUSDC while paused -> ['withdraw']
         https://testnet.nearblocks.io/txns/EZAQfA7soifhpu8kTry13WRNNBx1rBHnSvFK7KLLSF4v
[owner]  resume (full-access key, 1 yocto) -> ['resumed']
         https://testnet.nearblocks.io/txns/AmByWz9L9oGaJePUnxfrYR9Vff53o1Vdi2Fn9AqQ5tcv

vault today: 1 release(s), 2.00 mUSDC released; vault holds 7.000000 mUSDC
```

In that run, step 5's decision was for the same asset, so the guard already rejected it. The demo now uses another
asset (and prints the asset after `BUY`), so step 5 shows the paused vault refusing an **allowed** decision; we
checked that on the same deployment: decision #3 (BUY eth, ALLOWED) → `execute` refused `VAULT_PAUSED`. Every
transaction of both is listed in [VAULT.md](VAULT.md#testnet-evidence-13-nov-2026-free-testnet-near-test-only-mock-token). The
mock token is TEST-ONLY, and this DEMO vault accepts DRY_RUN decisions; a production vault refuses them.

## Where the claims live

| Claim | Code | Test |
|---|---|---|
| Contract re-checks every decision; the agent can veto, never override a rejection | `contracts/anchorium-guard/src/lib.rs` `record_decision` | unit tests in `src/tests.rs`, `tests/sandbox.rs` |
| P&L is computed on-chain; auto-pause at the daily loss cap | `record_outcome` | `guard_rules_kill_switch_and_auto_pause_on_chain` |
| The agent key cannot pause, change the policy or transfer | access key scope | `function_call_key_can_journal_but_nothing_else` |
| No deposit without LIVE flags, the caps, an ALLOWED decision and a typed confirmation | `anchorium/near/live.py`, `orchestrator._live_settle` | `tests/test_live_gates.py`, `tests/test_orchestrator.py` |
| Restart resumes and never repeats a side effect | `anchorium/agent/checkpoint.py` | crash tests in `tests/test_orchestrator.py` |
| Signals match the original strategy | `anchorium/agent/signals.py` | `tests/test_signal_parity.py` (golden outputs from the source commit) |
| The vault pays only for an ALLOWED, unsettled LIVE decision, at most once, within its caps | `contracts/anchorium-vault/src/lib.rs` `check_release`, `on_guard_answer` | `src/tests.rs`, `tests/sandbox.rs` |
| `SETTLEMENT=vault` never asks the vault without an ALLOWED decision; refusals journal FAILED, uncertainty parks | `orchestrator._vault_release` | vault cases in `tests/test_orchestrator.py` |
| A NEAR AI review is accepted only with a valid TEE signature bound to an attested key and our nonce | `anchorium/ai/near_ai.py` `verify_receipt` | `tests/test_near_ai.py` |
