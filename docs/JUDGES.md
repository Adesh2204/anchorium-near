# Five-minute verification

Each step says what to run and what you should see. Nothing here needs keys or funds. Live values (quotes,
prices, decision counts) change over time. The shape of the output and the verdicts should not.

## 0. Setup (1 min)

```sh
git clone https://github.com/Adesh2204/anchorium-near && cd anchorium-near
make setup            # -> setup OK: Python 3.x.y   (needs Python >= 3.11)
```

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
  same code hash but nothing journals to it, so its state stays flat and the demo is repeatable. It keeps the
  original 100 bps slippage cap; the main guard's owner raised that to 150 bps.
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

## 6. Tests (1–2 min)

```sh
make test-py          # -> 91 passed, 1 deselected
make contract-test    # -> 40 passed (unit), 2 passed (sandbox); needs Rust 1.95 + downloads a local NEAR node
```

## 7. Optional: your own contract, on-chain (3 min)

```sh
OWNER=judge-$RANDOM.testnet make demo-onchain   # needs near-cli-rs 0.31.1; free testnet faucet account
```

You get the same replay, but every decision and paper outcome is a real transaction on **your** `guard.OWNER`,
each printed with an explorer link.

## Where the claims live

| Claim | Code | Test |
|---|---|---|
| Contract re-checks every decision; the agent can veto, never override a rejection | `contracts/anchorium-guard/src/lib.rs` `record_decision` | unit tests in `src/tests.rs`, `tests/sandbox.rs` |
| P&L is computed on-chain; auto-pause at the daily loss cap | `record_outcome` | `guard_rules_kill_switch_and_auto_pause_on_chain` |
| The agent key cannot pause, change the policy or transfer | access key scope | `function_call_key_can_journal_but_nothing_else` |
| No deposit without LIVE flags, the caps, an ALLOWED decision and a typed confirmation | `anchorium/near/live.py`, `orchestrator._live_settle` | `tests/test_live_gates.py`, `tests/test_orchestrator.py` |
| Restart resumes and never repeats a side effect | `anchorium/agent/checkpoint.py` | crash tests in `tests/test_orchestrator.py` |
| Signals match the original strategy | `anchorium/agent/signals.py` | `tests/test_signal_parity.py` (golden outputs from the source commit) |
