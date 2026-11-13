# Anchorium Vault: the contract, not the bot, releases the money

The guard contract (`guard.anchorium-team.testnet`) decides whether a trade is allowed and journals every decision.
Before the Vault, an allowed LIVE decision was still paid by the agent's own trader key: the guard could say no,
but a compromised bot holding that key could send the money anyway.

The Vault (`contracts/anchorium-vault`, a new contract; the guard is not changed or redeployed) closes that gap. It
holds the trading funds. The agent can move them only by calling `execute(decision_id, …)` with a function-call key
limited to that one method. The vault then reads that decision from the guard **on-chain** and pays a 1Click deposit
address only if every rule below holds.

```
agent ── record_decision ──▶ guard (journal, policy, ALLOWED/REJECTED)
  │                            ▲
  └── execute(decision_id) ──▶ vault ── get_decision + get_positions ──┘
                                 │
                                 ├─ rule failed ──▶ event vault_release_rejected, nothing moves
                                 └─ all rules ok ─▶ mark executed ─▶ ft_transfer to the deposit address
                                                                      └─ failed? un-mark, event release_failed
```

## Release rules (`check_release`, a pure function, unit-tested rule by rule)

| # | Rule | Rejection reason |
|---|------|------------------|
| 1 | the guard has the decision, with the same id | `NO_SUCH_DECISION`, `DECISION_ID_MISMATCH` |
| 2 | the guard ALLOWED it | `NOT_ALLOWED` |
| 3 | it is a LIVE decision (DRY_RUN only on a vault initialised with `accept_dry_run = true`, the demo) | `DRY_RUN_DECISION`, `UNKNOWN_MODE` |
| 4 | it has no outcome yet, and this vault never executed it | `ALREADY_SETTLED`, `ALREADY_EXECUTED` |
| 5 | the vault is not paused | `VAULT_PAUSED` |
| 6 | `quote_id` equals the journaled one; when it carries a deposit address (`<correlationId>:<depositAddress>`, how the agent journals LIVE quotes) the payout goes to exactly that address | `QUOTE_MISMATCH`, `DEPOSIT_ADDRESS_MISMATCH` |
| 7 | the token is on the vault's allow-list | `TOKEN_NOT_ALLOWED` |
| 8 | BUY: the token is the quote token and the amount is at most the journaled notional (1 quote unit = $1) | `WRONG_TOKEN`, `AMOUNT_ABOVE_NOTIONAL` |
| 9 | SELL: the token is the decision's asset and the amount is at most the guard's OPEN position for that asset and mode | `WRONG_TOKEN`, `NO_OPEN_POSITION`, `AMOUNT_ABOVE_POSITION` |
| 10 | daily caps: releases per day, quote-token amount per day, NEAR spent on storage registrations per day | `DAILY_RELEASE_CAP`, `DAILY_QUOTE_CAP`, `DAILY_STORAGE_CAP` |
| – | the guard view itself failed | `GUARD_UNREACHABLE` |

Checked before the guard is even asked (the call panics, nothing is read or moved): caller is the agent
(`ONLY_AGENT`), at least 150 Tgas attached (`NOT_ENOUGH_GAS_ATTACH_150_TGAS`), not paused, not already executed,
amount positive, token allowed.

## Invariants

* **No decision, no money.** The only method that moves tokens to a third party is `execute`, and it transfers only
  inside the guard-answer callback after `check_release` returned Ok.
* **At most once per decision.** The decision is marked executed *before* the transfer, so a second `execute` is
  refused even while the first transfer is in flight. If the transfer fails, `on_transferred` un-marks it and rolls
  back the day's counters, so a retry is possible and a failed transfer never counts as spent.
* **Agent ≠ owner.** `new` and `set_agent` refuse the same account. The agent's key is a function-call key limited to
  `execute` (the Python client refuses any other key: `load_vault_signer`).
* **The owner keeps control.** `pause` stops every release. `withdraw` works while paused. `withdraw`, `resume` and
  `set_agent` need 1 yoctoNEAR attached, i.e. a full-access key: a function-call key can never call them.
* **Only intended deposits.** `ft_on_transfer` accepts tokens only from the owner and only in allowed tokens; anything
  else is returned.

## Events (NEP-297, standard `anchorium_vault`, version `1.0.0`)

`vault_release`, `vault_release_rejected` (with `reason`), `release_failed`, `deposit`, `withdraw`, `paused`,
`resumed`, `agent_changed`. The Python client reads every outcome from these events (`release_from_logs`), never
from assumptions.

## Residual risk (stated, not hidden)

The contract cannot check off-chain that a deposit address really belongs to the 1Click quote. A compromised agent
could journal a decision with its own address inside the `quote_id` and get it ALLOWED. It can therefore misdirect at
most **one allowed decision's notional**, which the guard already bounds (per-trade cap, entries per day, daily loss
cap, slippage) and which the vault bounds again with its own daily caps. The owner can pause the vault at any time.
Compared with a trader key that can send its whole balance, the worst case shrinks from "everything" to "one capped
trade per allowed decision".

## How the bot uses it (`SETTLEMENT=vault`)

`SETTLEMENT=direct` (the default) keeps the original flow: the trader account sends each 1Click deposit itself.
With `SETTLEMENT=vault` and `VAULT_ACCOUNT_ID=…` (`anchorium/agent/orchestrator.py`, `anchorium/cli.py`):

* the 1Click quote's `recipient` and `refundTo` are the vault, so outputs and refunds return to it;
* a BUY is journaled at the quote token's face value (rounded up), exactly the amount the vault will compare with
  the notional, so the guard caps what the vault may release;
* a SELL never asks for more than the vault holds;
* after the same LIVE gates and typed confirmation, the agent calls `vault.execute` instead of `ft_transfer`;
* `RELEASED` → tracked like a direct deposit; `REJECTED` / `TOKEN_TRANSFER_FAILED` / a refused call while the
  decision is not marked executed → journaled `FAILED` with the vault's evidence (nothing moved); any other answer,
  or a crash mid-call → `NEEDS_REVIEW` for a human, never re-sent.

**Status:** this path is covered by mocked tests only. 1Click runs on mainnet and no mainnet vault is deployed (that
needs the human's explicit `GO MAINNET`). The vault itself is proven on testnet below and in the sandbox test.

## Testnet evidence (13 Nov 2026, free testnet NEAR, TEST-ONLY mock token)

`make vault-demo OWNER=anchorium-vault-11674.testnet` deployed a fresh guard (same wasm as the team guard, code hash
`9G9w6hEmWJ9VmJ2R5BavB4gfGmZwUCCGYvhLPDyqQGfT`), the vault (code hash
`AowRWHGB28oLfZ8h8q2qzepsGu2XYav7z9B2cBBATLGj`, `res/anchorium_vault.wasm` sha256 `91bf6f6d…`) and a mock USDC
(`contracts/mock-ft`, demo only). This is the DEMO deployment (`accept_dry_run = true`), so the decisions are
DRY_RUN; a production vault refuses them (`DRY_RUN_DECISION`, covered by the sandbox test).

| Step | Result | Transaction |
|------|--------|-------------|
| vault deploy + init | code hash above | [DVoMKsKY…](https://testnet.nearblocks.io/txns/DVoMKsKYQhJjKmtAaCLWssJibecv58MAvvkPSw9yu3QX) |
| agent key limited to `execute` | function-call key | [3pLK2Gvp…](https://testnet.nearblocks.io/txns/3pLK2GvpFehWtjcCDGZrByQPte3SvHepKm8seYs4NkWB) |
| owner funds the vault | 10 mUSDC | [43RyHQSi…](https://testnet.nearblocks.io/txns/43RyHQSiG7uhMrAUrbaBZunzuDGNpfZdtJ2u4ntvCvz7) |
| decision #0, BUY $4.99 | guard REJECTED `NOTIONAL_ABOVE_MAX` | [9rqe5bTb…](https://testnet.nearblocks.io/txns/9rqe5bTbuF8cCwh9f15nzbMq7dq9UrFFarWXRjuhvjmX) |
| execute #0 | `vault_release_rejected NOT_ALLOWED`, nothing moved | [r1SYreti…](https://testnet.nearblocks.io/txns/r1SYretiMuPAmijQgHSnbVEnLZb5YZ7WFmp72i1dk6B) |
| decision #1, BUY $2.00 | guard ALLOWED | [51LDEvkM…](https://testnet.nearblocks.io/txns/51LDEvkMvab1873TuRAGXJsSeg4QefrU3F56FQBFb9uM) |
| execute #1 | `vault_release`: deposit address 0 → 2 mUSDC, vault 10 → 8 | [AX9vpHMm…](https://testnet.nearblocks.io/txns/AX9vpHMmtJ8CpHxcwJiMjXGxSQA3gVXoLQ9xtVBDn9Yb) |
| execute #1 again | refused `ALREADY_EXECUTED` | receipt `GBFshR8YCsWtV6FYja99fTkySnFtyn24dB13wU66uh8n` |
| owner pause | `paused` | [A9JBJDCr…](https://testnet.nearblocks.io/txns/A9JBJDCrYTZpwctPP5Abh1KhBUBW6NQ2FYqCiZAmjREX) |
| owner withdraw 1 mUSDC while paused | `withdraw` | [EZAQfA7s…](https://testnet.nearblocks.io/txns/EZAQfA7soifhpu8kTry13WRNNBx1rBHnSvFK7KLLSF4v) |
| owner resume (full-access key, 1 yocto) | `resumed` | [AmByWz9L…](https://testnet.nearblocks.io/txns/AmByWz9L9oGaJePUnxfrYR9Vff53o1Vdi2Fn9AqQ5tcv) |
| re-check while paused: pause | `paused` | [3HgLYPjj…](https://testnet.nearblocks.io/txns/3HgLYPjjwxHHqot1wuhi8q71SnGEMjJvbmAcGwSNnDjA) |
| decision #3, BUY eth $1.50 | guard ALLOWED | [HvBmL8td…](https://testnet.nearblocks.io/txns/HvBmL8tdaf5zq8n8y3St2WoYLg1FSyvp39wLWGCvyN9h) |
| execute #3 | refused `VAULT_PAUSED` | receipt `8JZmz84KqyXqgPfRcAUaNXuWVnqnP6CQo6yCHbRJnYT9` |
| resume | `resumed` | [2oPVHNwk…](https://testnet.nearblocks.io/txns/2oPVHNwk9RNXyKqppQjXRRyMUGAnP6rurZVZN4yA3hcX) |

Vault account: [vault.anchorium-vault-11674.testnet](https://testnet.nearblocks.io/address/vault.anchorium-vault-11674.testnet).
In the first run, step 5's second decision was for the same asset and the guard rejected it
(`POSITION_ALREADY_OPEN`, [9RKEbV64…](https://testnet.nearblocks.io/txns/9RKEbV64oyHAhkvVyUAu2uxEZSkPgYndVUmiYYZCHHYF));
the demo now uses another asset so the paused vault is shown refusing an *allowed* decision (the re-check rows).

Reproduce (free, about 8 testnet NEAR from the faucet): `make vault-demo OWNER=<new-name>.testnet`.

## Tests

| Suite | Command | What it proves |
|-------|---------|----------------|
| 16 unit tests (`src/tests.rs`) | `make vault-test` | every release rule, marking/rollback, daily caps, agent-only, pause, replay, owner controls, 1-yocto methods, gas floor, deposit filter, init checks |
| sandbox test (`tests/sandbox.rs`) | `make vault-test` | on a local NEAR node with the committed guard wasm and the mock token: allowed LIVE release, replay, rejected decision, notional cap, wrong deposit address, DRY_RUN on a production vault, stranger and agent-key limits, failed transfer un-marks then retry with registration, pause + withdraw |
| `tests/test_vault_client.py` | `make test-py` | event parsing, execute-only key rule, the exact call the agent signs |
| vault cases in `tests/test_orchestrator.py` | `make test-py` | `SETTLEMENT=vault`: recipient/refund = vault, face-value notional, refusals journaled FAILED with evidence, uncertain answers and crashes parked, never asked for rejected or unconfirmed decisions, SELL bounded by the vault balance |

## Audit checklist (self-review with the `near-contract-audit` checklist; not a third-party audit)

| Check | Status |
|-------|--------|
| Callbacks `#[private]` | `on_guard_answer`, `on_transferred` |
| Callback results handled | both use `#[callback_result]`; a failed guard view → `GUARD_UNREACHABLE` |
| State changed before cross-contract transfer | executed mark and counters set before `ft_transfer`; rolled back on failure |
| Gas for the whole chain | `execute` refuses < 150 Tgas; static gas for every hop |
| Privileged methods need a full-access key | `withdraw`, `resume`, `set_agent` assert 1 yocto |
| Arithmetic | `checked_mul` for the notional bound, `saturating_sub` on rollback |
| Token callbacks | `ft_on_transfer` only from the owner in allowed tokens |
| Storage | registrations paid from the vault, capped per day |
| Upgrade path | none: no upgrade method; a new vault is a new deployment |
