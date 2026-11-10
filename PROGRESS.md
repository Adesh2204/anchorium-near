# PROGRESS

Internal deadline: 2026-11-14 20:00 IST. Hard deadline: 2026-11-14 23:59 UTC (2026-11-15 05:29 IST).

## Checklist

- [x] Phase 0: recon, rules read, audit of the old repo (Checkpoint 1 passed: "GO", 2026-11-10)
- [x] Baseline commit: vendored frozen strategy, tag `baseline`
- [x] Phase 1: hygiene (LICENSE, AGENTS.md, .gitignore, Makefile, README skeleton, NEAR skills)
- [x] Phase 2: guard contract (unit + sandbox tests, testnet deploy, agent sub-account with function-call key)
- [x] Phase 3: 1Click executor (dry-run default, every status, mocked tests, one real dry quote)
- [x] Phase 4: orchestrator + `make demo` (Path A) + `make demo-onchain` (Path B) + REPLAY. Checkpoint 2 is waiting for the user
- [ ] Phase 5: dashboard
- [ ] Phase 6: README + docs/JUDGES.md
- [ ] Phase 7: fresh-clone self-judging + review pass
- [ ] Phase 8: submission pack (Checkpoint 3)

## Decisions

| When (IST) | Decision | Why |
| --- | --- | --- |
| 2026-11-10 21:18 | New clean repo `Adesh2204/anchorium-near`. Judged diff is `baseline...hackathon-submission` | The user's GO. The old repo's 103 non-NEAR commits since 2026-10-19 and 2.32 GB of LFS would bury the NEAR work |
| 2026-11-10 21:19 | Vendor only FrozenStrategy's 12-file import closure, byte-identical | The user's GO: no NSE/Angel One code, no exchange write paths |
| 2026-11-10 21:19 | Freeze check scoped to the entry-signal path: 1 file hash + 28 of 32 parameters verified; 8 methods + 4 parameters recorded as excluded with their original values | Those 12 entries live in legacy NSE/Angel One modules that FrozenStrategy never imports. The checker proves nothing was dropped silently (see `vendor/anchorium_strategy/SOURCE.md`) |
| 2026-11-10 21:19 | No original tests vendored | Every source test touching `signal_brain` imports modules outside the closure. A parity test against the source commit replaces them (Phase 4) |
| 2026-11-10 21:38 | Sandbox tests use `near-sandbox` 0.4 + `near-api` 0.8 instead of `near-workspaces` | The current NEAR docs, the `near-smart-contracts` skill and the `cargo near new` template all use them |
| 2026-11-10 21:38 | `cargo near build non-reproducible-wasm` | Docker is not installed, so no reproducible build. Toolchain pinned by `rust-toolchain.toml` (Rust 1.95.0, from the template) |
| 2026-11-10 21:38 | `record_outcome` takes settlement amounts; the contract computes realized PnL (no free-form `pnl_usd` argument) | A self-reported PnL argument would let a compromised agent fake its record |
| 2026-11-10 21:38 | Final verdict = contract check AND the agent's verdict; a `verdict_override` event when they disagree | The agent can veto, never upgrade |
| 2026-11-10 21:38 | Pause blocks entries only; exits need a contract-tracked open position | A kill switch must never trap funds in a position |
| 2026-11-10 21:42 | Testnet: owner `anchorium-team.testnet` (faucet), contract `guard.anchorium-team.testnet`, agent `agent.anchorium-team.testnet` with a function-call key (record_decision, record_outcome; 0.9 NEAR allowance) | The prompt's layout. Keys are in `~/.near-credentials` only |
| 2026-11-10 21:42 | No deploy tx hash in the README; link the explorer account pages | The nearblocks API v1 was retired 2026-11-05 and the deploy output was truncated |
| 2026-11-10 21:47 | Slippage gate = worst case: the value of `minAmountOut` vs the input at `/v0/tokens` reference prices (covers price impact + fees + tolerance). Passed to the contract as `slippage_bps` | The quote's own USD fields are not a cost measure (identical in/out USD despite a 20 bps fee) |
| 2026-11-10 21:47 | Default requested tolerance 10 bps; policy `max_slippage_bps` stays at 100 | Measured $2 entries at 21:46 IST, worst case: wNEAR 98 bps, ETH 76, BTC 65 at tol 10 (138/114/102 at tol 50); exit wNEAR->USDC 23 bps. Borderline NEAR entries are rejected by design |
| 2026-11-10 21:47 | A LIVE deposit is signed by a separate mainnet trading account with a full-access key | NEP-141 `ft_transfer` needs a 1 yoctoNEAR deposit and function-call keys cannot attach deposits. Documented as a limitation |
| 2026-11-10 21:58 | Python to NEAR: `near-kit` 1.3.0 works for views and function-call-key signing on testnet; no sidecar needed | Decisions #0 to #2 on guard.anchorium-team.testnet were signed by the function-call key `ed25519:6Uv5...` |
| 2026-11-10 22:03 | Path A checks run against `demo.anchorium-team.testnet` (same wasm and code_hash, no journaling key); the real journal is read from `guard.anchorium-team.testnet` | The agent's open paper position on the main guard made Path A output depend on live state (POSITION_ALREADY_OPEN skips) |
| 2026-11-10 22:05 | An aborted LIVE trade (cap or confirmation) journals a FAILED outcome | Otherwise the PENDING_ENTRY position would block all later entries |
| 2026-11-10 22:05 | Exit rule: sell on the next SHORT signal from the unchanged brain; no stop/target from the original trader | That management code is in the legacy modules that are not vendored |
| 2026-11-10 | Report the observed 1Click unauthenticated fee: 20 bps (`appFees` in a real dry quote); the docs say 0.25% | The user's GO, point 5 |
| 2026-11-10 | Python to NEAR: try `near-kit` (Python) first; switch to a `near-api-js` sidecar if blocked for more than 2 h | The user's GO, point 4 |

## Log

Times are commit times (`git log`) or `date` output.


- 2026-11-10 20:10 IST: Phase 0 recon. Old repo: 3,671 tests pass, strategy freeze passes, no real secrets in history.
- 2026-11-10 21:18 IST: cloned the empty `anchorium-near`. Installed rustc 1.99.1 (rustup), cargo-near 0.23.0, near-cli-rs 0.31.1.
- 2026-11-10 21:19 IST: `baseline` commit `05d5b8b`. `scripts/check_strategy_freeze.py` passes.
- 2026-11-10 21:25 IST: Phase 1 files added. Freeze tamper tests: 5 passed.
- 2026-11-10 21:38 IST: contract: 40 unit tests + 2 near-sandbox tests pass. Clippy clean. Wasm 214 KB.
- 2026-11-10 21:42 IST: testnet deploy of `guard.anchorium-team.testnet`. Keyless views verified (`check_trade` OK at $2.00, NOTIONAL_ABOVE_MAX at $5.00). `deploy_testnet.sh` rerun is idempotent.
- 2026-11-10 21:47 IST: Phase 3: `anchorium/near/intents.py` + `live.py`; 46 mocked tests + 1 opt-in network test pass. Real dry quote correlationId `6d6eba41-e5bd-4b19-a5dd-dd97a03f8eec` ($2 USDC -> 0.5564 wNEAR, fee 20 bps).
- 2026-11-10 21:56 IST: REPLAY fixture: 6 real signal bars (BTC/ETH/NEAR entry + exit). Golden from the source tree at 68af4eb; the vendored copy matches bit for bit.
- 2026-11-10 21:58 IST: first on-chain journal entries (Path B on the team guard): #0 BUY allowed + DRY_RUN outcome, #1 SELL rejected SLIPPAGE_ABOVE_MAX (162 bps), #2 compromised-agent BUY rejected NOTIONAL_ABOVE_MAX with verdict_override.
- 2026-11-10 22:03 IST: demo guard deployed. `make demo` (Path A) is deterministic. `make test`: freeze OK, 81 Python, 40 unit + 2 sandbox contract tests pass.
- 2026-11-10 22:19 IST: Checkpoint 2 answered (live micro-trade YES with conditions; delete the agent full-access key YES). Mainnet trading account `b4443ef93760d13acb528210f796a3f0143abbbb93a90047d932865f91b966ca` created (implicit; key only in ~/.near-credentials/mainnet, never printed). Waiting for funding.
- 2026-11-10 22:19 IST: agent full-access key `ed25519:CMjy...` deleted (tx 86WYN7RoR1PNERjpSzspQXJ6uDX2fqnGP4m3LLrGyPq8) after confirming the owner's full-access key file matches its on-chain key. agent.anchorium-team.testnet now has 1 key: function-call only.
