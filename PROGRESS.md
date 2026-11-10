# PROGRESS

Internal deadline: 2026-11-14 20:00 IST. Hard deadline: 2026-11-14 23:59 UTC (2026-11-15 05:29 IST).

## Checklist

- [x] Phase 0: recon, rules read, audit of the old repo (Checkpoint 1 passed: "GO", 2026-11-10)
- [x] Baseline commit: vendored frozen strategy, tag `baseline`
- [x] Phase 1: hygiene (LICENSE, AGENTS.md, .gitignore, Makefile, README skeleton, NEAR skills)
- [x] Phase 2: guard contract (unit + sandbox tests, testnet deploy, agent sub-account with function-call key)
- [ ] Phase 3: 1Click executor (dry-run default, every status, mocked tests, one real dry quote)
- [ ] Phase 4: orchestrator + `make demo` (Path A) + `make demo-onchain` (Path B) + REPLAY (Checkpoint 2)
- [ ] Phase 5: dashboard
- [ ] Phase 6: README + docs/JUDGES.md
- [ ] Phase 7: fresh-clone self-judging + review pass
- [ ] Phase 8: submission pack (Checkpoint 3)

## Decisions

| When (IST) | Decision | Why |
| --- | --- | --- |
| 2026-11-10 21:00 | New clean repo `Adesh2204/anchorium-near`. Judged diff is `baseline...hackathon-submission` | The user's GO. The old repo's 103 non-NEAR commits since 2026-10-19 and 2.32 GB of LFS would bury the NEAR work |
| 2026-11-10 21:15 | Vendor only FrozenStrategy's 12-file import closure, byte-identical | The user's GO: no NSE/Angel One code, no exchange write paths |
| 2026-11-10 21:15 | Freeze check scoped to the entry-signal path: 1 file hash + 28 of 32 parameters verified; 8 methods + 4 parameters recorded as excluded with their original values | Those 12 entries live in legacy NSE/Angel One modules that FrozenStrategy never imports. The checker proves nothing was dropped silently (see `vendor/anchorium_strategy/SOURCE.md`) |
| 2026-11-10 21:15 | No original tests vendored | Every source test touching `signal_brain` imports modules outside the closure. A parity test against the source commit replaces them (Phase 4) |
| 2026-11-10 21:20 | Sandbox tests use `near-sandbox` 0.4 + `near-api` 0.8 instead of `near-workspaces` | The current NEAR docs, the `near-smart-contracts` skill and the `cargo near new` template all use them |
| 2026-11-10 21:20 | `cargo near build non-reproducible-wasm` | Docker is not installed, so no reproducible build. Toolchain pinned by `rust-toolchain.toml` (Rust 1.95.0, from the template) |
| 2026-11-10 21:40 | `record_outcome` takes settlement amounts; the contract computes realized PnL (no free-form `pnl_usd` argument) | A self-reported PnL argument would let a compromised agent fake its record |
| 2026-11-10 21:40 | Final verdict = contract check AND the agent's verdict; a `verdict_override` event when they disagree | The agent can veto, never upgrade |
| 2026-11-10 21:40 | Pause blocks entries only; exits need a contract-tracked open position | A kill switch must never trap funds in a position |
| 2026-11-10 22:00 | Testnet: owner `anchorium-team.testnet` (faucet), contract `guard.anchorium-team.testnet`, agent `agent.anchorium-team.testnet` with a function-call key (record_decision, record_outcome; 0.9 NEAR allowance) | The prompt's layout. Keys are in `~/.near-credentials` only |
| 2026-11-10 22:05 | No deploy tx hash in the README; link the explorer account pages | The nearblocks API v1 was retired 2026-11-05 and the deploy output was truncated |
| 2026-11-10 | Report the observed 1Click unauthenticated fee: 20 bps (`appFees` in a real dry quote); the docs say 0.25% | The user's GO, point 5 |
| 2026-11-10 | Python to NEAR: try `near-kit` (Python) first; switch to a `near-api-js` sidecar if blocked for more than 2 h | The user's GO, point 4 |

## Log

- 2026-11-10 20:10 IST: Phase 0 recon. Old repo: 3,671 tests pass, strategy freeze passes, no real secrets in history.
- 2026-11-10 21:18 IST: cloned the empty `anchorium-near`. Installed rustc 1.99.1 (rustup), cargo-near 0.23.0, near-cli-rs 0.31.1.
- 2026-11-10 21:21 IST: `baseline` commit `05d5b8b`. `scripts/check_strategy_freeze.py` passes.
- 2026-11-10 21:30 IST: Phase 1 files added. Freeze tamper tests: 5 passed.
- 2026-11-10 21:45 IST: contract: 40 unit tests + 2 near-sandbox tests pass. Clippy clean. Wasm 214 KB.
- 2026-11-10 22:00 IST: testnet deploy of `guard.anchorium-team.testnet`. Keyless views verified (`check_trade` OK at $2.00, NOTIONAL_ABOVE_MAX at $5.00). `deploy_testnet.sh` rerun is idempotent.
