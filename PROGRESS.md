# PROGRESS

Internal deadline: 2026-11-14 20:00 IST. Hard deadline: 2026-11-14 23:59 UTC (2026-11-15 05:29 IST).

## Checklist

- [x] Phase 0: recon, rules read, audit of the old repo (Checkpoint 1 passed: "GO", 2026-11-10)
- [x] Baseline commit: vendored frozen strategy, tag `baseline`
- [x] Phase 1: hygiene (LICENSE, AGENTS.md, .gitignore, Makefile, README skeleton, NEAR skills)
- [x] Phase 2: guard contract (unit + sandbox tests, testnet deploy, agent sub-account with function-call key)
- [x] Phase 3: 1Click executor (dry-run default, every status, mocked tests, one real dry quote)
- [x] Phase 4: orchestrator + `make demo` (Path A) + `make demo-onchain` (Path B) + REPLAY. Checkpoint 2 is waiting for the user
- [x] Phase 5: dashboard (screenshot in docs/screenshots/)
- [x] Phase 6: README + docs/JUDGES.md (live-trade evidence and the paper-trade report still to add)
- [x] Paper trade (INR 500, user request 2026-11-11): done, docs/paper_trade_report.md
- [x] Phase 7: fresh-clone self-judging + review pass (Path A and Path B clean from a fresh clone)
- [x] Regime backtest (2026-11-12): docs/regime_backtest.md. Owner's decision: version A only; regime + bear rounds stay research (`research/regime/`), no guard change
- [x] Version A autonomous build (branch `autonomous-a`): money maths, runner, status, live-preflight, tests, ruff + mypy clean
- [ ] Version A: independent review fixes, paper INR 500 run (docs/paper_trade_A_report.md), merge to main
- [ ] Version A LIVE: only after the owner types GO LIVE
- [ ] Phase 8: submission pack drafted in submission/; tag `hackathon-submission` waits for the live trade and the human TODOs (Checkpoint 3)

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
| 2026-11-10 22:27 | Measured gap between 1Click `/v0/tokens` reference prices and executable quotes: 0.5 to 1%, so a 100 bps cap rejects about 1 in 3 $2 trades (exits 102 and 162 bps rejected) | Raising `max_slippage_bps` is the owner's decision: asked the user |
| 2026-11-11 11:59 | Owner raised `max_slippage_bps` 100 -> 150 on guard.anchorium-team.testnet (tx DExrWkb3p8zQauc31dsqhCA6M9jfbWvd4YLWh4XX75df); the demo guard keeps 100 | The user's decision (a). The demo guard was not part of it |
| 2026-11-11 12:05 | Paper-trade costs are measured against `/v0/tokens` reference prices recorded with each decision, not the quote's `amountInUsd`/`amountOutUsd` | For USDC -> wNEAR, 1Click returns `amountOutUsd == amountInUsd`, so those fields cannot measure cost |
| 2026-11-11 12:05 | The orchestrator refreshes `/v0/tokens` for every signal | The client cached reference prices for the life of the process, so a long-running loop would gate slippage on stale prices |
| 2026-11-11 12:10 | Default RPC is FastNEAR (`rpc.testnet.fastnear.com`, `rpc.mainnet.fastnear.com`) | `rpc.testnet.near.org` now answers with a deprecation error and rate limit |
| 2026-11-11 12:00 | Mainnet deposit gas estimate: 0.000217 NEAR per `ft_transfer`, measured from 2 real mainnet `wrap.near` transfers | A paper trade sends no deposit; an estimate from real transactions beats a guess |
| 2026-11-11 16:25 | Second ₹500 paper trade: NEAR only, exit `PROFIT_TARGET` (sell only when the quote's `minAmountOut` covers the USDC paid + both deposits' gas + $0.01), no stop, report cut-off 2026-11-14 18:00 IST | The user's choices (asked). A live $2 round trip costs ~104 bps on NEAR vs 139 on BTC; NEAR reached +1.54% within 24 h in 77% of the last 52 days (ETH 37%, BTC 20%). An execution rule, not a strategy change: entries still come from the frozen strategy |
| 2026-11-11 20:24 | Live exit uses the paper trade 2 profit floor; the per-trade cap on an exit applies to the position's entry cost, the $5 total still counts the exit's value | User instruction 4. The local gate capped exit *proceeds* at $2, so every profitable exit would have been refused (the contract already exempts exits) |
| 2026-11-11 20:24 | A leg whose confirmation was not typed (or whose quote expired) neither blocks nor uses the micro-trade; a REFUNDED/FAILED swap still stops everything | Nothing is sent before the typed confirmation, and the guard's position stays open after a failed exit; the human reruns the command, nothing retries by itself |
| 2026-11-11 20:24 | Each LIVE deposit records the trader's NEAR balance before and after; the exit floor uses the measured entry cost | near-kit batches a `storage_deposit` for an unregistered 1Click deposit address (about 0.00125 NEAR) into the transfer, which the paper gas estimate did not include |
| 2026-11-12 01:35 IST | Regime backtest data: Binance USD-M NEARUSDT 1m archives (SHA256 checked), not Hyperliquid | Hyperliquid's public API keeps only ~3.5 days of 1m candles. Cross-checks: regime agrees 97.4% with Hyperliquid 15m/1h; minute-level signals differ (71 of 102 within 3 bars) |
| 2026-11-12 01:35 IST | "Round-trip cost" (1.3% filter, CHOP rule) = both legs' worst-case quote cost (fee + slippage vs the Hyperliquid mid); gas is charged separately in the profit rule | Matches the earlier "~104 bps NEAR round trip" figure. Measured now: median 35.4 bps buy + 58.3 bps sell |
| 2026-11-12 01:35 IST | Backtest gas per leg 0.001467 NEAR (ft_transfer + deposit-address storage registration); 3-round cap per IST day | The live exit floor's assumption; the user's "session" mapped to a day for 60 days |
| 2026-11-12 01:35 IST | Tax proceeds = INR value of what a swap delivered; the received asset takes that value as cost; FIFO across the whole wallet | The user's FIFO rule. Consequence: a bull close sells the starting wNEAR first and pays tax on its whole rise |
| 2026-11-12 10:35 IST | Owner: version A only, no guard contract change, B/C dropped from the runtime; the guard v2 bear-round work was reverted (never deployed) | Owner's message after the backtest; the in-place guard upgrade had been blocked by the permission check |
| 2026-11-12 10:50 IST | Every 1Click deposit address is new and NOT storage-registered (checked with two non-dry quotes: `storage_balance_of` = null on USDC and wrap.near), so each deposit leg costs ~0.001467 NEAR (0.000217 gas + 0.00125 registration) | That is ~0.5% of a $2 round trip at NEAR $3.54; with the quote cost (~0.92%) the total was 1.44% > the 1.3% filter |
| 2026-11-12 10:55 IST | Cost filter = (USDC in - worst-case USDC back + both legs' NEAR cost) / USDC in, threshold **1.6%** (`COST_FILTER_PCT`) | Owner's answer: "Count it, raise to 1.6%" |
| 2026-11-12 11:00 IST | Session loss cap counts realized after-tax INR (net minus the tax reserve) | The stricter reading of "realized loss" |
| 2026-11-12 11:00 IST | Version A paper fills = the quote's `minAmountOut` (journaled as the DRY_RUN outcome too); the guard position therefore holds exactly what the sell sells | Owner: use only the worst-case guaranteed amount |
| 2026-11-12 11:00 IST | Autonomous LIVE: the session total caps BUYS only ($6 = 3 x $2); exits are capped by the entry they close; the micro-trade command keeps its $5 total | Owner's limits are per trade and per round; counting exits would block the third round's sell |
| 2026-11-12 11:00 IST | A paused guard is a hard stop when flat; with an open round the exit is still managed (the contract never blocks exits) | Sections 5 and 8 together: pausing blocks buys, never traps a position |
| 2026-11-12 11:00 IST | On-chain SKIPPED_COST decisions are rate-limited to 1 per 15 min (the rest are journaled locally) | The guard's 100 decisions/day cap is shared with real trades |
| 2026-11-10 | Report the observed 1Click unauthenticated fee: 20 bps (`appFees` in a real dry quote); the docs say 0.25% | The user's GO, point 5 |
| 2026-11-10 | Python to NEAR: try `near-kit` (Python) first; switch to a `near-api-js` sidecar if blocked for more than 2 h | The user's GO, point 4 |

## Log

Times are commit times (`git log`) or `date` output.


- 2026-11-10 20:10 IST: Phase 0 recon. Old repo: 3,671 tests pass, strategy freeze passes, no real secrets in history.
- 2026-11-10 21:18 IST: cloned the empty `anchorium-near`. Installed rustc 1.99.1 (rustup), cargo-near 0.23.0, near-cli-rs 0.31.1.
- 2026-11-10 21:19 IST: `baseline` commit `56b78dc`. `scripts/check_strategy_freeze.py` passes.
- 2026-11-10 21:25 IST: Phase 1 files added. Freeze tamper tests: 5 passed.
- 2026-11-10 21:38 IST: contract: 40 unit tests + 2 near-sandbox tests pass. Clippy clean. Wasm 214 KB.
- 2026-11-10 21:42 IST: testnet deploy of `guard.anchorium-team.testnet`. Keyless views verified (`check_trade` OK at $2.00, NOTIONAL_ABOVE_MAX at $5.00). `deploy_testnet.sh` rerun is idempotent.
- 2026-11-10 21:47 IST: Phase 3: `anchorium/near/intents.py` + `live.py`; 46 mocked tests + 1 opt-in network test pass. Real dry quote correlationId `6d6eba41-e5bd-4b19-a5dd-dd97a03f8eec` ($2 USDC -> 0.5564 wNEAR, fee 20 bps).
- 2026-11-10 21:56 IST: REPLAY fixture: 6 real signal bars (BTC/ETH/NEAR entry + exit). Golden from the source tree at 68af4eb; the vendored copy matches bit for bit.
- 2026-11-10 21:58 IST: first on-chain journal entries (Path B on the team guard): #0 BUY allowed + DRY_RUN outcome, #1 SELL rejected SLIPPAGE_ABOVE_MAX (162 bps), #2 compromised-agent BUY rejected NOTIONAL_ABOVE_MAX with verdict_override.
- 2026-11-10 22:03 IST: demo guard deployed. `make demo` (Path A) is deterministic. `make test`: freeze OK, 81 Python, 40 unit + 2 sandbox contract tests pass.
- 2026-11-10 22:19 IST: Checkpoint 2 answered (live micro-trade YES with conditions; delete the agent full-access key YES). Mainnet trading account `b4443ef93760d13acb528210f796a3f0143abbbb93a90047d932865f91b966ca` created (implicit; key only in ~/.near-credentials/mainnet, never printed). Waiting for funding.
- 2026-11-10 22:19 IST: agent full-access key `ed25519:CMjy...` deleted (tx 86WYN7RoR1PNERjpSzspQXJ6uDX2fqnGP4m3LLrGyPq8) after confirming the owner's full-access key file matches its on-chain key. agent.anchorium-team.testnet now has 1 key: function-call only.
- 2026-11-10 22:24 IST: dashboard (FastAPI + static page) reads the policy, kill switch, stats and journal from the contract. 4 tests.
- 2026-11-10 22:26 IST: manual dry exit of the paper NEAR position rejected again on-chain (#3, worst case 102 bps > 100). The paper position stays open and blocks a LIVE entry (max_open_positions = 1).
- 2026-11-10 22:27 IST: live micro-trade tooling committed (70d304b). 87 Python tests pass. Waiting for the mainnet account to be funded.
- 2026-11-11 11:59 IST: user chose (a). Owner tx raised max_slippage_bps to 150. Old paper NEAR position closed: #4 SELL ALLOWED (93 bps), contract paper P&L +$0.03.
- 2026-11-11 12:11 IST: paper-trade session started (INR 500 = $5.331491 at 93.7824 INR/USD, Coinbase; check_trade BUY $2.00 ALLOWED on BTC, ETH, NEAR). Restarted at 12:17 IST on the fixed report code before any entry.
- 2026-11-11 12:21 IST: README rewritten (10 sections), docs/JUDGES.md, dashboard screenshot. Tests: 91 Python passed (1 network deselected), contract 40 unit + 2 sandbox passed.
- 2026-11-11 14:46 IST: paper trade done. Entry #5 BTC PULLBACK_LONG (bar 07:19 UTC, 79 bps), exit #6 BTC BREAKDOWN_SHORT (bar 09:15 UTC, 58 bps), held 116 min. Net -$0.020841 before gas, -$0.022395 after estimated gas (-INR 2.10, -0.42% of INR 500). Contract paper P&L -$0.03. Report: docs/paper_trade_report.md.
- 2026-11-11 14:57 IST: fresh-clone self-judge. Found and fixed: `make setup` failed on the default python3 3.11 (numpy 2.5.2 needs 3.12; commit 21ea81b). Then setup, freeze, 91 tests, `make demo`, `make status`, dashboard (200 on /, /api/decisions, /api/signals) all clean. Path B (`make demo-onchain`, new faucet account anchorium-selfjudge-30739.testnet) ran in 2:00 (finished about 15:00 IST): 2 allowed decisions, paper P&L -$0.02, compromised agent overruled.
- 2026-11-11 14:58 IST: review: no private keys in history; documented that paper and live share the guard's daily counters (auto-pause can be triggered by paper losses). `main` fast-forwarded to the green branch.
- 2026-11-11 15:02 IST: submission pack drafted (form.md with a 1965-character idea, x_post.md, video_script.md, checklist.md). Nothing submitted or posted.
- 2026-11-11 16:34 IST: second paper session `paper-profit` running (NEAR, PROFIT_TARGET +$0.01, cut-off 14 Nov 18:00 IST; commit 8a3a447, 94 Python tests). Waiting for a NEAR LONG. Report will be docs/paper_trade_profit_report.md.
- 2026-11-11 17:24 IST: paper trade 2 done. Entry #7 NEAR BREAKOUT_LONG (bar 11:50 UTC, 2 USDC -> 0.541669 wNEAR, 94 bps), 3 profit checks, exit #8 PROFIT_EXIT (bar 11:53 UTC; guaranteed 2.020587 USDC vs floor 2.011593; fill 2.022610). Net +$0.022610 before gas, +$0.021017 after estimated gas (+INR 1.97, +0.39% of INR 500). Contract paper P&L +$0.02. Reports gained a split against the Hyperliquid market price (1Click's 2-decimal reference lagged the move); it shows paper trade 1's gross was -$0.0036, not +$0.0022. 94 Python tests.
- 2026-11-11 20:24 IST: user answers: no other NEAR HACKATHON project (README updated); demo guard raised to 150 bps (tx 7MmqfBADhymYuccmniMxano2EWGURb6qtYoEyeVip7MN). Live micro-trade prepared: macOS notification + sound with the quote deadline before each typed confirmation, profit-floor exit (until 13 Nov 20:00 IST, then the user's --manual). 102 Python tests. Waiting for NEAR on b4443ef9....
- 2026-11-12 01:04 IST: 60 real dry-quote round trips sampled (docs/data/quote_costs_2026-11-12.json): buy leg median 35.4 bps, sell leg 58.3 bps, fee 20 bps each.
- 2026-11-12 01:09 IST: 60-day signal replay (86,400 minutes, frozen strategy via evaluate_latest): 5,635 signals.
- 2026-11-12 01:33 IST: regime backtest (docs/regime_backtest.md). Out of sample, A = B = C: one bull round opened 12 Oct 09:31 IST at $5.871 and stayed open through a 39% fall (unrealized -INR 75.04). Days 1-30: A best. Recommended A for LIVE, C as DRY_RUN experimental; waiting for the user's choice. Tests: 143 Python passed, freeze OK, ruff + mypy clean on the new modules.
- 2026-11-12 10:35 IST: owner chose version A only. Guard v2 (bear rounds) reverted before any deploy; deployed guards untouched (wasm sha256 7abf4ecc...).
- 2026-11-12 11:11 IST: version A built on `autonomous-a` (24e18d0): 378 Python tests, ruff + mypy clean, freeze OK. Smoke test on real APIs (no journaling): FX 93.7062 (Coinbase), cost check 1.458% <= 1.6%.
- 2026-11-12 11:23 IST: independent review (a separate agent that had not seen the work) of all money-handling code: 11 real findings, all fixed, each with a regression test (388 Python tests):
  1. CRITICAL: a network error during a LIVE deposit made the runner re-run the leg (BUY: round mis-booked as aborted, entries silently blocked, or a second deposit; SELL: crash loop). Fixed: the orchestrator never re-runs a non-NEW leg (DEPOSIT_PENDING -> NEEDS_REVIEW), the checkpoint store refuses illegal transitions, and the runner retries a leg only when no deposit can be in flight (otherwise hard stop).
  2. HIGH: the 30-min retry window for a guard-rejected stop/time sell never reset, so a later stop could be abandoned after one rejection. Fixed: the window is one continuous exit episode.
  3. MEDIUM: resuming a buy that crashed before its leg started skipped the entry gates (could buy after the 16:00 cut-off). Fixed: gates (cut-off, cooldown, caps, loss cap, signal <= 3 min old) are re-checked.
  4. MEDIUM: the session loss cap could be lost if the process died between two saves. Fixed: saved atomically with the closed round and re-derived on start.
  5. MEDIUM: unexpected errors (and LIVE wallet reads outside the retry wrapper) could end the run silently with a round open. Fixed: wallet reads retried; any unexpected error is a notified hard stop.
  6. MEDIUM-LOW: on-chain SKIPPED_COST journaling could use up the guard's 100 decisions/day needed for exits. Fixed: it stops when fewer than 50 remain.
  7. LOW: the receipt's net line was rounded half-even (could show above exact). Fixed: net rounded down, tax up, so net and after-tax are never shown above exact.
  8. LOW: a second Ctrl+C could interrupt a deposit mid-send. Fixed: ignored while a deposit is pending.
  9. LOW: a missing token contract was detected after DEPOSIT_PENDING. Fixed: checked before.
  10. LOW: a LIVE stop/time sell refused before any deposit (expired quote) hard-stopped. Fixed: treated as a rejection and retried.
  11. LOW: CLI state edits ran before taking the session lock. Fixed: lock first.
- 2026-11-12 11:29 IST: fresh clone of `autonomous-a` (9a2ea28) following the README: make setup (Python 3.12.5), make freeze (13 files, 28 parameters, passed), make test-py 388 passed, make demo (3 signals OK + compromised agent NOTIONAL_ABOVE_MAX), make status, make contract-test (40 unit + 2 sandbox). All pass.
- 2026-11-12 11:24 IST: paper INR 500 session `paper-a` started (DRY_RUN, until 1 round closes or 20:00 IST): 5.334437 USDC = INR 500.00 at 93.7306 INR/USD (Coinbase).
- 2026-11-12 14:11 IST: main fast-forwarded to autonomous-a (5fe9f1f): version A + docs/regime_backtest.md, no B/C runtime code. 388 tests, freeze OK, mypy clean, ruff clean (rule set pinned: ruff 0.17 widened its defaults). Paper session paper-a still running (flat: only SHORT signals since 11:24 IST). Live trader account not yet funded.
