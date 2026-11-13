# Shot list: every frame and its real source

Brief: the owner's media brief of 12 Nov (8 shots, 11 gallery images; the brief itself is not kept in the repo). Recon 2026-11-12 IST.
**No live mainnet round has happened** (`docs/live_trade_report.md` does not exist, and version A's paper session
`paper-a` has not closed a round), so every money scene carries the amber label "PAPER: real quotes, simulated fill".

## Owner decisions (Checkpoint 1, 2026-11-12)

1. Shot 4: film a **fresh Path B deployment** (`OWNER=anchorium-media-$RANDOM.testnet make demo-onchain`) for brand-new
   `record_decision` / `record_outcome` txs. Decisions #7/#8 (11 Nov) are backup footage, with their dates on screen.
2. Shot 5b and image 08: `pause`, then `resume`, **the same fresh Path B guard** (the media account owns it). Never
   write to `guard.anchorium-team.testnet` or `demo.anchorium-team.testnet`. Shot 5c uses the real sandbox test,
   labelled "local sandbox test".
3. No music, voiceover only.
4. Toolchain approved: VHS, pngquant, oxipng, and Playwright in `submission/media/.venv`.

## Media deployment (Path B, write scenes only)

| Item | Value |
|---|---|
| Owner account | _created at capture (step 3); logged here with its creation tx_ |
| Guard | `guard.<owner>`; agent `agent.<owner>` (function-call key only) |
| Writes on it | `deploy`, `record_decision`, `record_outcome` (from `make demo-onchain`), owner `pause`, owner `resume` |

## Facts used on screen (re-verified 2026-11-12)

| Item | Today's value | Source |
|---|---|---|
| Guard (team) / demo guard | `guard.anchorium-team.testnet` / `demo.anchorium-team.testnet` | `make status`, `make demo` header |
| Owner / agent | `anchorium-team.testnet` / `agent.anchorium-team.testnet` | `make status` → roles |
| Agent key | ONE key: `FunctionCall`, receiver `guard.anchorium-team.testnet`, methods `record_decision`, `record_outcome` | RPC `view_access_key_list` (keyless curl from the README) |
| Agent full-access key deleted | tx `86WYN7RoR1PNERjpSzspQXJ6uDX2fqnGP4m3LLrGyPq8` | README |
| Policy | $2.00/trade, 10 entries/day, $1.00 daily loss cap (auto-pause), 150 bps, 60 s cooldown, 1 open position | `make status` → policy |
| Journal totals | 9 decisions: 6 allowed, 3 rejected, 1 verdict override, all DRY_RUN; live 0 | `make status` → stats |
| Compromised agent on chain | #2 BUY $4.99, agent claims allowed → `NOTIONAL_ABOVE_MAX` + `verdict_override` | tx `4KxLnhn9mdtaQq5wVpxK525yMyBsdpfyKNfqw1aBbAWM` |
| Same check, live today | `[guard]   check_trade BUY nep141:wrap.near $4.99 -> NOTIONAL_ABOVE_MAX` (demo guard, view call) | `make demo`, run 2026-11-12 |
| Slippage rejections / cap raise | #1 162 bps, #3 102 bps; owner 100 → 150 bps | txs `DJC9Ldk6…`, `B32FQ8EE…`, `DExrWkb3…` |
| Backup cycle | #7 NEAR `BREAKOUT_LONG` BUY $2.00, worst case 94 bps; #8 `PROFIT_EXIT` SELL, contract P&L +$0.02 (11 Nov) | txs `CrSDywAW…` / `4KvDD7pt…`, `8sywP2Qr…` / `8wWznqNp…` |
| Paper receipts | Trade 1 (BTC): −INR 2.10; trade 2 (NEAR): +INR 1.97 | `docs/paper_trade_report.md`, `docs/paper_trade_profit_report.md` |
| Tests | Python **388 passed**; contract **40 unit + 2 sandbox passed** (both re-run 2026-11-12) | `make test-py`, `make contract-test` |
| Freeze | 13 files, 28 parameters, passed | `make freeze` |
| Review | 11 findings fixed, 1 critical (possible double deposit) | PROGRESS.md 2026-11-12 11:23 |

## Video shots

| # | Time | Visual | Proves | Exact source | Status |
|---|---|---|---|---|---|
| 1 | 0:00–0:12 | VHS: `make demo` compromised-agent block → red `NOTIONAL_ABOVE_MAX`; hard cut to NearBlocks tx `4KxLnhn9…`, `verdict_override` highlighted | The contract outranks the bot | `tapes/01_cold_open.tape`; `web/nearblocks.py` → `https://testnet.nearblocks.io/txns/4KxLnhn9mdtaQq5wVpxK525yMyBsdpfyKNfqw1aBbAWM` | OK. Lower-third gives the tx date (10 Nov) |
| 2 | 0:12–0:27 | Title card "Anchorium" + 3 problem cards | The problem | `templates/title.html` | Graphic, no metrics |
| 3 | 0:27–0:47 | Architecture (README mermaid): strategy → agent → guard (testnet) → 1Click (mainnet) → user's account → outcome to the guard; owner → pause in red | How it works | `templates/architecture.html` | Graphic, no metrics |
| 4 | 0:47–1:40 | Left: VHS `make demo-onchain` on the media account (⏩ badge during waits). Right: `make dashboard` (team guard) + NearBlocks of the new decision tx. Beats: signal → `[guard] OK` → `[quote]` ≤ 150 bps → `record_decision` link → NearBlocks → `record_outcome` with contract P&L → dashboard card | End to end, on chain | `tapes/04_cycle.tape`; `web/dashboard.py`; `web/nearblocks.py` (new tx URL logged at capture) | Step 3. Backup: #7/#8 with "11 Nov" on screen |
| 5a | 1:40–1:48 | `view_access_key_list` curl → one FunctionCall key; deletion tx `86WYN7Ro…` | Agent key can only journal | `tapes/05a_keys.tape` | OK (read-only) |
| 5b | 1:48–1:57 | Media owner `pause` → next `check_trade` paused → `resume` | Kill switch | `tapes/05b_pause.tape` on `guard.<media owner>` only | Step 3 |
| 5c | 1:57–2:05 | `cargo test --test sandbox guard_rules_kill_switch_and_auto_pause_on_chain` + the `lib.rs` auto-pause lines + the dashboard text "pauses itself at the daily loss cap" | Auto-pause at the loss cap | `tapes/05c_autopause.tape` | Label "local sandbox test" |
| 6 | 2:05–2:22 | Paper receipts −INR 2.10 (red rail) and +INR 1.97 (green rail), amber PAPER label, exit formula, "31.2% tax reserved" | We don't hide losses | `templates/money.html` from the two report files | No mainnet tx (none exists) |
| 7 | 2:22–2:38 | Fresh clone: `make setup && make demo`; `make test-py` → 388 passed; `make contract-test` → 40 + 2 | Reproducible | `tapes/07_verify.tape` (temp dir) | Today's counts |
| 8 | 2:38–2:48 | End card | Close | `templates/end.html` | Graphic |

## Gallery (11 images)

| # | File | Headline | Real source | Rail |
|---|---|---|---|---|
| 01 | `01_cover.png` | Don't trust the bot. Verify it. | The `NOTIONAL_ABOVE_MAX` line from `make demo` (2026-11-12) | red → green |
| 02 | `02_problem.png` | Today you just trust the bot | Graphic | muted |
| 03 | `03_architecture.png` | The rules live on-chain, not in the bot | Graphic (still of shot 3) | green |
| 04 | `04_overruled.png` | The bot tried to cheat. The contract said no. | Dashboard card #2 + NearBlocks tx `4KxLnhn9…` | red |
| 05 | `05_policy.png` | The owner writes the rules on-chain | Dashboard crop: kill switch, policy, today | green |
| 06 | `06_funds_timeline.png` | Always know where your money is | Dashboard crop: one ALLOWED decision row | green |
| 07 | `07_agent_key.png` | A key that can only write the journal | `view_access_key_list` output + tx `86WYN7Ro…` | green |
| 08 | `08_kill_switch.png` | One transaction stops every new trade | Media guard: `pause` → `check_trade` paused + tx link | red |
| 09 | `09_honest_money.png` | Wins and losses, every cost shown | The two paper receipts + exit formula (KaTeX) + PAPER label | amber |
| 10 | `10_engineering.png` | 388 + 42 tests. 11 review findings fixed. | `make test-py`, `make contract-test`, `make freeze` terminal | green |
| 11 | `11_verify.png` | Verify it in one minute | Fresh-clone `make setup && make demo` terminal + repo URL + guard ID | green |

## Palette (sampled from `dashboard/static/index.html`, dark theme)

The dashboard's own dark tokens are used, so the media looks like the product. They differ slightly from the
brief's target values:

| Role | Dashboard token | Brief target |
|---|---|---|
| Background | `--bg #0f1216` | `#0D1117` |
| Panel | `--panel #171b21` | `#161B22` |
| Border | `--line #283039` | `#30363D` |
| Text / muted | `--ink #e8ebef` / `--muted #98a1ad` | `#E6EDF3` / `#8B949E` |
| ALLOWED | `--ok #4cc38a` (badge bg `#12291f`) | `#3FB950` |
| REJECTED | `--bad #f97066` (badge bg `#2d1614`) | `#F85149` |
| DRY RUN / PAPER | `--warn #f5b546` (badge bg `#2b2210`) | `#D29922` |
| Links | `--accent #7c9cff` | `#58A6FF` |

## Shots that cannot be captured as written

- **Shot 6 mainnet tx:** no live round exists, so it is dropped and the receipts are labelled PAPER.
- **Fonts:** Inter and JetBrains Mono are not installed locally. Templates load them from Google Fonts at render
  time, falling back to the dashboard's system sans and `ui-monospace`.

## Toolchain (installed 2026-11-12)

VHS 0.12.1 (ttyd 1.7.7), ffmpeg 9.0.2, pngquant 3.0.3, oxipng 10.2.1, Playwright 1.65.1 + Chromium headless shell
158 in `submission/media/.venv`.

## Capture log, 2026-11-12 (gallery v2)

### Media deployment (write scenes only)

| Item | Value |
|---|---|
| Owner | `anchorium-media-6018.testnet`, created ~15:55 IST by `OWNER=anchorium-media-$RANDOM.testnet make demo-onchain` |
| Guard / agent | `guard.anchorium-media-6018.testnet` (code_hash `9G9w6hEmWJ9VmJ2R5BavB4gfGmZwUCCGYvhLPDyqQGfT`, policy file `scripts/near/policy.testnet.json`: 100 bps) / `agent.anchorium-media-6018.testnet` (function-call key: `record_decision`, `record_outcome`) |
| Journal written by the demo | #0 BUY ALLOWED (tx `3juxrMHELwSq6gMzeF8EJ4Ho8fcjnFuF8o164e6yq1XD`, outcome `2EzsaH21XoAzc4YZqiXfmh4pTKvwAsDPdsVbFF1tnsgh`), #1 SELL ALLOWED, #2 compromised BUY REJECTED `NOTIONAL_ABOVE_MAX` |
| Kill switch (`web/kill_switch_capture.sh`) | `check_trade` OK → owner `pause` tx `6aAieGNY52VqTm6piM1K7gV6kUhivBUwcXK6wtRcZEJ6` (event `paused`) → `check_trade` `PAUSED` → owner `resume` tx `8pVtm45NGDHBVnvSqabnGq5tmDz6pjautghJ8vBmX5rL` (event `resumed`) → `check_trade` OK |
| Recording | Not usable yet: near-cli prints the key-file path (a home path) when it creates accounts, and the VHS `Wait` failed on scrolling output. For the video: pre-create a new media owner off camera, then film `make demo-onchain` with a fixed `Sleep` (it reuses the accounts and journals a fresh cycle). |

The team guard and the demo guard were never written to. Local state was isolated: the 10 Nov checkpoint store
and the event log were restored; the media and today's Path A demo lines are in
`var/decisions.2026-11-12.demo-and-media-6018.jsonl` and `var/agent-demo-onchain.media-6018.sqlite`.

### Image sources (all parsed by `web/render.py`, never retyped)

| # | Real source |
|---|---|
| 01 | tx `4KxLnhn9…` via NEAR testnet RPC (`EXPERIMENTAL_tx_status`): args `notional_usd_cents=499`, `agent_allowed=true`; log `verdict_override` → `NOTIONAL_ABOVE_MAX`; block time 2026-11-10 16:27:52 UTC. Cap $2.00 from `make -s status` |
| 02 | Graphic. Its 5 row pairs come from the spec (65 words, above the 30-word guideline) |
| 03 | Graphic. Zones are labelled per node (off-chain / testnet / mainnet), because the flow alternates networks |
| 04 | Dashboard card #2 (2x element screenshot, dashboard on :8010 reading the team guard) + the tx's real contract log from RPC. NearBlocks blocks headless browsers (Cloudflare check), so the RPC log replaces the NearBlocks crop |
| 05 | Dashboard KILL SWITCH + POLICY panels; `policy_updated` tx `DExrWkb3…` verified via RPC |
| 06 | Dashboard card #8 (SELL `PROFIT_EXIT`, contract P&L $0.02, DRY RUN); txs `8sywP2Qr…` / `8wWznqNp…` from the README table |
| 07 | `view_access_key_list agent.anchorium-team.testnet` (one FunctionCall key); `DeleteKey` tx `86WYN7Ro…` verified via RPC; sandbox test name checked in `tests/sandbox.rs` |
| 08 | `raw/kill_switch.log` on the media guard (above) |
| 09 | README paper trade 2 line (+$0.0317, −$0.0080, −$0.0011, −$0.0016 = +$0.0210, +INR 1.97; the sum is asserted) and paper trade 1 (−$0.0224, −INR 2.10). The formula is version A's PROFIT_EXIT rule (`anchorium/money.py`); paper trade 2 used the earlier profit-target rule |
| 10 | `make test-py` 388 passed; `make contract-test` 40 + 2 passed; `make freeze` 28 parameters, passed; PROGRESS 11 findings incl. 1 CRITICAL; 60 samples in `docs/data/quote_costs_2026-11-12.json`; 0 occurrences of `float` in the money modules |
| 11 | `LICENSE` (MIT); QR encodes `https://github.com/Adesh2204/anchorium-near-Paypal` |

## Video capture log, 2026-11-12 (Part A, step 3)

| Shot | Clip (raw/, untracked) | Real source and values |
|---|---|---|
| 1 | `01_cold_open.mp4` | `tapes/01_cold_open.tape`: `make demo` against `demo.anchorium-team.testnet`; `$4.99 -> NOTIONAL_ABOVE_MAX`. NearBlocks blocks headless browsers (Cloudflare), so the "hard cut" uses gallery 04 (the same tx's real contract log from RPC) |
| 2, 3, 8 | `clips/v_title.mp4`, `clips/v_arch.mp4`, `clips/v_end.mp4` | `templates/v_*.html` on `video.css` (= gallery.css at 1920x1080), recorded by `web/record_cards.py`. Graphics, no metrics |
| 4 | `04_cycle.mp4` | `tapes/04_cycle.tape` on media owner 2 **`anchorium-media-19428.testnet`** (created off camera by `scripts/near/deploy_testnet.sh`: near-cli prints key-file paths). `make demo-onchain`: #0 BUY ALLOWED, worst case 58 bps <= 100, tx `iUMzJAyScbGUigT7y3PKcG3HDdKVwkT7Hgbg49CW6n7`, outcome `GA1PPiK3XsK8CmpfLyH3X2StdzXBgdm8LpVyTU1gERNq`; #1 SELL ALLOWED, tx `FCi4jFQYBQRYMKwAt6DSQkhUnkZPYG1QAsJkdLPV8thG`, outcome `H9XvE4CYs3viXqVDicLsk5jvADWrENfWEWexYK1m7TtT` (contract paper P&L **-$0.02**); #2 compromised BUY REJECTED `NOTIONAL_ABOVE_MAX`, tx `9Zg3o6w7Dud4pDwiX3wMzVmWtywEdEMmkMVYReUt8TCA` |
| 4 (right) | `clips/dashboard.mp4` | `make dashboard` on :8010 reading the team guard; slow real scroll. The DRY RUN banner was made sticky in `dashboard/static/index.html` (display only) so it stays visible (rule 3) |
| 5a | `05a_keys.mp4` | The README curl (`view_access_key_list agent.anchorium-team.testnet`): one FunctionCall key, guard only, `record_decision` + `record_outcome` |
| 5b | `05b_pause.mp4` | `web/kill_switch_capture.sh` on `guard.anchorium-media-19428.testnet` only: OK -> `pause` tx `DZHNSLmrfpUY3oQ4Y63wZ7tebFxbCTQhQnZrzwcSe9QW` -> PAUSED -> `resume` tx `FKLwabo3E3dcxWizmNjfD2eDer9hfGBmpDunHDwNfDCr` -> OK |
| 5c | `05c_autopause.mp4` | `sed -n 603,612p src/lib.rs` (`maybe_auto_pause`) + `cargo test --test sandbox guard_rules_kill_switch_and_auto_pause_on_chain`: 1 passed, 13.61 s. Label: local sandbox test |
| 6 | (gallery 09 still) | Paper trades 1 and 2 from the README / reports; PAPER label |
| 7 | `07_verify.mp4` | Fresh `git clone` from GitHub in an empty temp dir: `time (make setup && make demo)` = **real 0m30.064s** (pip cache warm on this machine); `make test-py` 388 passed; `make contract-test` 40 + 2 passed (build cache reused via CARGO_TARGET_DIR, same sources) |

All terminal recordings were checked for `/Users/` (0 hits). Team and demo guards were never written to. Local state
was isolated again for shot 4 (media store `var/agent-demo-onchain.media-19428.sqlite`, event lines in
`var/decisions.2026-11-12.media-19428.jsonl`); `make demo` lines with replay hashes were moved out of
`var/decisions.jsonl` so the dashboard shows the original 10-11 Nov quotes.

## Final video (silent, no voiceover; owner's choice on 2026-11-12)

`web/assemble.py` builds `video/anchorium_demo_full.mp4` (2:46, 1920x1080, 30 fps, H.264, silent AAC track) and
`video/anchorium_demo_60s.mp4` (0:55) from the clips above, with on-screen captions instead of a voiceover
(`video/captions.srt` for YouTube CC). Footage is trimmed, sped up 2x or 4x with a visible badge, or held on its real
last frame with highlight boxes (green allowed, red blocked, amber paper, blue code); gallery stills 04 and 09 get a
slow zoom. Copies at the repo root: `Anchorium_Demo_Video.mp4`, `Anchorium_Demo_Video_60s.mp4` (untracked).

### Revision 2026-11-12 evening (owner request)

Tagline changed everywhere to "Don't trust the bot. Verify it." (title and end cards, gallery 01, youtube.md, x_post.md,
form.md idea = 1997/2000 characters). New cold open: `templates/v_hook.html`, every value filled from the real tx
4KxLnhn9 and the on-chain policy (BUY $4.99, allowed: true, $2.00 cap, NOTIONAL_ABOVE_MAX, 10 Nov 2026 16:27 UTC),
then the raw `make demo` line and the RPC log as proof. Chapter cards (`templates/v_chapter.html`) and 0.3 s
cross-fades. Full cut 2:49, X cut 0:57.

## Re-cut 2026-11-13 (NEAR HACKATHON grand-prize pass): Vault + NEAR AI Cloud, ≤ 2:15

Timeline (`web/assemble.py`): hook (real tx 4KxLnhn9) → title + 3 problems → new architecture card "The rules, and the
money, live on NEAR" (signal → 1Click quote → NEAR AI review (TEE) → guard journals → Vault releases only if ALLOWED)
→ `make demo-onchain` on the 12 Nov media guard (trimmed) → **NEW Vault** → **NEW NEAR AI Cloud** → agent key + kill
switch (stills of the 12 Nov captures) → optional fiat on-ramp → fresh-clone verification → end card. The dashboard
scroll, the auto-pause sandbox clip and the paper-money scene were dropped for time; the money facts stay in the README.

| Scene | Clip (raw/, untracked) | Real source and values |
|---|---|---|
| Vault (new) | `06_vault.mp4`, `06_vault_end.png` | `tapes/06_vault.tape`: `python -m anchorium vault-demo --owner anchorium-vault-film-1430.testnet` on a fresh deployment made off camera by `scripts/near/deploy_vault_testnet.sh` (it prints key-file paths). Decision #0 $4.99 REJECTED → execute `NOT_ALLOWED`; #1 $2.00 ALLOWED → `RELEASED` 2 mUSDC (TEST-ONLY mock token); replay `ALREADY_EXECUTED`; pause → #2 eth ALLOWED → `VAULT_PAUSED`; withdraw; resume. Every tx in `docs/VAULT.md` (second run), all confirmed via RPC |
| NEAR AI Cloud (new) | `08_near_ai.mp4`, `08_near_ai_end.png` | `tapes/08_near_ai.tape`: `make -s verify-ai ARGS="--full"` on the committed 13 Nov attestation: both TDX bindings PASS, NVIDIA NRAS overall `True`, nonce matched; then `tests/test_near_ai.py`: 17 passed (fake TEE). Caption says signed reviews wait for NEAR AI credits (HTTP 402) |
| Fiat on-ramp (new) | `09_fiat.mp4`, `09_fiat_end.png` | `tapes/09_fiat.tape`: `make -s paypal-demo` keyless (NEAR AI key blanked off camera, no PayPal keys → offline simulation, labelled so on screen). The real sandbox capture of 12 Nov is in `docs/FIAT_ONRAMP.md` |
| Verify (re-filmed) | `07_verify.mp4`, stills | `tapes/07_verify.tape`: fresh `git clone` of `near-grand-prize` from GitHub, `time (make setup && make demo)`, then `make test-py`, `make contract-test`, `make vault-test` (cargo cache via `ANCHORIUM_MEDIA_CARGO_TARGET`, same sources) |
| Cards | `clips/v_title.mp4`, `v_arch.mp4`, `v_end.mp4`, `ch_vault.mp4`, `ch_ai.mp4` | `templates/v_*.html` (title 10 s, architecture 12 s, end card per the brief: repo, "Built for NEAR HACKATHON · NEAR Protocol · NEAR Intents · NEAR AI Cloud", small grey "Fiat on-ramp: PayPal sandbox", "Adesh & Parth") |

Secret check: every new terminal recording's `.txt` was grepped for `sk-`, `ed25519:` private keys and `/Users/`
before assembly.

Result (13 Nov): `video/anchorium_demo_full.mp4` **2:02** (121.9 s), `video/anchorium_demo_60s.mp4` **0:51.5** (hook,
architecture, Vault, end card), 1920x1080, 30 fps, silent AAC track; `video/captions.srt` and `video/chapters.txt`
regenerated. Gallery 10 re-rendered from fresh runs (454 · guard 40 + 2 · vault 16 + 1 · freeze passed). Both files
are untracked; the human uploads them (YouTube, X).
