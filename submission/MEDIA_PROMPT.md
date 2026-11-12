# MASTER PROMPT: Anchorium demo video + image gallery (NEAR HACKATHON)

> How to use: save this file as `submission/MEDIA_PROMPT.md` in your local `anchorium-near` clone, then tell
> Claude Code: "Read submission/MEDIA_PROMPT.md and execute it, starting with step 1." (Long pastes into the
> terminal get cut off. Hand it over as a file.)
> Do NOT commit raw recordings. Commit only the sources (tapes, scripts, templates) and the final gallery PNGs.

---

## 0. ROLE AND GOAL

You are the **creative director and media engineer** for Team Anchorium (Adesh + Parth). We're competing for the
NEAR HACKATHON Grand Prize with https://github.com/Adesh2204/anchorium-near. Produce:

- **Part A:** a demo video, **2:40–2:50, hard limit 3:00**, for YouTube (public or unlisted), plus a **60 s cut**
  for the required X post.
- **Part B:** an **11-image gallery**, **1800×1200 px (3:2), PNG, under 5 MB each**.

Everything is built **as code**, so `make media` rebuilds it all.

The judges (NEAR, a16z crypto, Helius, Electric Capital, Synthetix, Dragonfly, cyber•Fund) see dozens of
submissions. In the first 10 seconds, ours must make them feel this: **"the bot tried to cheat, the contract
said no, and I can check that myself."**

`submission/video_script.md` (90 s) is an earlier draft. This prompt replaces it for YouTube; keep its spirit.

---

## 1. FACTS YOU MAY USE (all from the repo; re-verify each one before it goes on screen)

| Fact | Source |
|---|---|
| Guard: `guard.anchorium-team.testnet`; keyless demo guard: `demo.anchorium-team.testnet` | README "How NEAR is used" |
| Owner `anchorium-team.testnet`; agent `agent.anchorium-team.testnet`, **function-call key only** (`record_decision`, `record_outcome`) | README; the `view_access_key_list` curl |
| Agent full-access key deleted: tx `86WYN7RoR1PNERjpSzspQXJ6uDX2fqnGP4m3LLrGyPq8` | README |
| Compromised agent ($4.99, claims allowed) → `NOTIONAL_ABOVE_MAX` + `verdict_override`: tx `4KxLnhn9mdtaQq5wVpxK525yMyBsdpfyKNfqw1aBbAWM` | README journal #2 |
| Slippage rejections at 162 and 102 bps; owner raises the cap 100 → 150 bps (`policy_updated`, tx `DExrWkb3…`) | README journal #1, #3 |
| Policy: $2/trade, 10 entries/day, $1 daily loss cap with auto-pause, 150 bps, 60 s cooldown, 1 open position | `make status` |
| Paper trade 1 (BTC): **−INR 2.10**; paper trade 2 (NEAR): **+INR 1.97** | `docs/paper_trade_report.md`, `docs/paper_trade_profit_report.md` |
| Tests: **388 Python**, **40 contract unit + 2 sandbox**; freeze: 13 files, 28 parameters | README "Tests" (re-run and use today's numbers) |
| Independent review: 11 findings fixed, 1 critical (a possible double deposit) | PROGRESS.md 2026-11-12 11:23 |
| **No live mainnet trade yet** (unless it runs before recording; check `docs/live_trade_report.md`) | README "Limitations" |

Anything not in this table needs a source file or a real command output before it goes on screen.

---

## 2. NON-NEGOTIABLE RULES

1. **Real only.** Every number, hash, account, tx and UI frame comes from a real command or a real page.
   Nothing is typed into a frame by hand, and nothing is mocked or retouched.
2. **Paper is labelled as paper.** If no live round exists, money scenes carry a "PAPER: real quotes, simulated
   fill" label. If a live round exists by recording time, use its real receipt and mainnet tx, **win or lose**.
3. **Don't disturb the production contracts.**
   - Never call `pause`, `update_policy` or anything that writes to `guard.anchorium-team.testnet` or
     `demo.anchorium-team.testnet`. A paper/live session uses the team guard, and judges use the demo guard.
   - Every scene that **writes** (kill switch, fresh decisions, `demo-onchain`) runs on a **fresh Path B
     deployment**: `OWNER=anchorium-media-$RANDOM.testnet make demo-onchain`. Log the account in SHOTLIST.md.
   - Read-only scenes (dashboard, `make status`, `make demo`, NearBlocks) may use the team or demo guard.
4. **No secrets on screen.** Before any render, grep every capture source for `ed25519:` private keys,
   `secret_key`, `JWT`, `~/.near-credentials`, `/Users/` and email addresses. The agent's **public** key
   `ed25519:6Uv5zL…` is fine. Never show the mainnet trader account's credentials.
5. **No brand art.** Don't use the NEAR logo or other marks as decoration (the plain text "Built on NEAR" is
   fine). No copyrighted music: leave the video silent, or use a royalty-free track the team puts in
   `submission/media/music/`.
6. If you can't capture a shot for real, **drop it and tell me**. Never fake it.

---

## 3. TOOLCHAIN

| Job | Tool |
|---|---|
| Terminal scenes | **VHS** (`brew install vhs`), one `.tape` per scene |
| Browser scenes (dashboard at http://127.0.0.1:8000, NearBlocks testnet) | **Playwright for Python**, 1920×1080, `record_video_dir`; stills at `device_scale_factor=2` |
| Title cards, diagrams, gallery frames | HTML/CSS templates rendered to PNG/MP4 by Playwright |
| Assembly | **ffmpeg**: H.264, `-preset veryfast`, CRF 20, 30 fps, AAC 192k, `-movflags +faststart` |
| Voiceover | **Parth's or Adesh's own voice**, recorded in QuickTime from `vo_script.txt`. A real student voice is more believable than TTS. |
| Captions | burned in from `captions.srt`, because many judges watch muted |
| PNG size | `pngquant` / `oxipng` to stay under 5 MB |

Layout (add `submission/media/raw/` to `.gitignore`):

```
submission/media/
  tapes/  web/  templates/  raw/  gallery/  video/
  SHOTLIST.md     # every shot: what it proves, the exact command/URL, the date captured
  vo_script.txt   # timecoded voiceover
  youtube.md      # title, description, chapters
```

Add Makefile targets: `media`, `media-video`, `media-gallery`.

---

## 4. VISUAL IDENTITY (extend the dashboard; don't invent a new look)

The dashboard (`docs/screenshots/dashboard.png`) already has the look. Every graphic should feel like it came
from the same product.

- **Mood:** audit-grade calm, like a security report crossed with a trading terminal. No rockets, coins,
  lambos, laser eyes or "to the moon".
- **Palette** (sample it from `dashboard/static/index.html`; the values below are targets):
  - background `#0D1117`, panels `#161B22`, borders `#30363D`
  - text `#E6EDF3`, muted `#8B949E`
  - **ALLOWED** green `#3FB950`, **REJECTED** red `#F85149`, **DRY RUN/PAPER** amber `#D29922`, links blue `#58A6FF`
  - **Colour is meaning:** green means the contract allowed it, red means the contract blocked it, amber means
    paper/dry run. Never use these colours decoratively.
- **Type:** Inter (or the system sans) for headlines, at most 7 words. JetBrains Mono for every hash, account,
  amount and command.
- **Signature motif, the "guard rail":** a 6 px line along the bottom of every title card and gallery image,
  coloured by the scene's verdict. In the video, the rail draws left to right as each scene begins.
- **VHS theme:** a custom theme JSON from the palette, FontSize 22, Padding 40, 1920×1080, TypingSpeed 45ms,
  WindowBar Colorful.

---

## PART A: THE DEMO VIDEO

### A1. Story arc

Cold open (the bot gets caught) → the problem → how it works → one full cycle → three safety proofs → honest
money → verify it yourself → close.

### A2. Shot list (targets ±3 s; total 2:40–2:50)

| # | Time | Visual (all real) | Proves | Voiceover draft (first person, conversational; rewrite it in your own words) |
|---|---|---|---|---|
| 1 | 0:00–0:12 | **Cold open.** VHS: the `make demo` block "Simulated compromised agent: asks for 2.5x the cap and claims it is allowed" → red `NOTIONAL_ABOVE_MAX`. Hard cut to NearBlocks tx `4KxLnhn9…` with the `verdict_override` event highlighted. | The contract outranks the bot | "This is our trading bot pretending it's been hacked. It asks for two and a half times its limit and says the trade is allowed. And this is a NEAR smart contract saying no, permanently, on-chain." |
| 2 | 0:12–0:27 | Title card **"Anchorium"** + rail. Three problem cards: *Bots hold your keys* · *Broken rules stay invisible* · *Track records are screenshots* | The problem | "Every trading bot asks for blind trust. It can quietly break its own rules, and its track record is a screenshot." |
| 3 | 0:27–0:47 | **Animated architecture** built from the README mermaid: frozen strategy → agent → guard (testnet) → 1Click (mainnet) → user's own account → outcome back to the guard. Boxes light up in order. Owner → pause arrow in red. | How it works | "So we moved the rules out of the bot and into a contract. Before every trade, the agent has to ask the guard, journal its decision on-chain, and settle through NEAR Intents into your own account. The bot never holds your money." |
| 4 | 0:47–1:40 | **Full cycle on a fresh Path B guard.** Split screen: left is VHS `OWNER=anchorium-media-$RANDOM.testnet make demo-onchain` (sped up 3–4× with a "⏩ 4×" badge during waits); right is the dashboard (`make dashboard`, pointed at the team guard) and NearBlocks. Beats: `[signal]` → green `[guard] OK` → `[quote]` with worst-case bps ≤ the cap → the `record_decision` explorer link → open it on NearBlocks → the `record_outcome` line with P&L computed by the contract. Then the dashboard decision card: Signal → Guard verdict → Quote → **Where the funds are** → Outcome. | It actually works, end to end, on-chain | "A real signal comes in. The contract checks it. We get a live quote from NEAR Intents, and our worst-case slippage has to fit the policy. The decision goes on-chain. Here's the transaction. And the dashboard always tells you where your money is right now." |
| 5 | 1:40–2:05 | **Three safety proofs, ~8 s each:** (a) the README `view_access_key_list` curl returns ONE key with `FunctionCall … [record_decision, record_outcome]`, plus the key-deletion tx `86WYN7Ro…`; (b) **on the fresh Path B guard only**, the owner calls `pause` → the next `check_trade` returns paused, then `resume`; (c) the daily loss cap auto-pause: the contract test or the `lib.rs` lines, plus the dashboard text "pauses itself at the daily loss cap". | Safety lives in the contract | "The bot's key can only write to the journal. It can't change the rules or move funds. We deleted its master key ourselves. The owner has a one-transaction kill switch. And if the daily loss cap is hit, the contract pauses itself." |
| 6 | 2:05–2:22 | **Honest money.** Side by side: the paper trade 1 receipt **−INR 2.10** (red rail) and the paper trade 2 receipt **+INR 1.97** (green rail), with an amber "PAPER" label. A quick flash of the exit formula and the line "31.2% tax reserved". *If a live round exists, show it instead, with its mainnet tx.* | We don't hide losses | "Every fee, every bit of gas, and India's 31.2% tax reserved before we call anything a profit. Wins and losses both. Nothing rounded in our favour." |
| 7 | 2:22–2:38 | **Verify it yourself.** VHS on a fresh clone: `make setup && make demo`, then a fast scroll of `make test-py` ending on **388 passed** and `make contract-test` with **40 + 2 passed**. Caption: "No keys. No funds. ~1 minute." Optional 2 s overlay: "Independent review: 11 findings, all fixed". | Reproducible and engineered | "Don't trust us either. Clone it, run two commands, no keys and no money, and you'll see exactly this." |
| 8 | 2:38–2:48 | **End card:** Anchorium · *A trading bot you don't have to trust.* · `github.com/Adesh2204/anchorium-near` · `guard.anchorium-team.testnet` · "Built at NEAR HACKATHON · Adesh & Parth" | Close | "Anchorium. Built at NEAR Hackathon, by Adesh and Parth. Thanks for watching." |

### A3. Editing rules

- **No dead air.** Never more than 6 s without a visual change. Speed up waits 2–4× with a visible badge, and
  never speed up so much that results look faked.
- **Highlight what matters.** Every verdict, hash and amount the VO mentions gets a rounded box in its verdict
  colour plus an eased 1.15× zoom (300 ms in, hold, 300 ms out).
- **Lower-thirds** name the source: "Guard contract · NEAR testnet", "NEAR Intents 1Click · mainnet quote",
  "NearBlocks explorer", "Fresh deployment by `make demo-onchain`".
- **Captions:** burned in, bottom centre, at most 2 lines, white on 70% black.
- **Audio:** VO at −14 LUFS. Music (if any) ducks to −28 LUFS under the VO. At most two UI sounds: a soft tick
  on ALLOWED and a low thud on REJECTED.
- **Frame 1 is the thumbnail.** Open on the red `NOTIONAL_ABOVE_MAX` frame, never on black.
- **The 60 s X cut:** shots 1 → 3 (trimmed to 12 s) → 4 (signal → OK → tx → NearBlocks) → 8.

### A4. Video deliverables

- `video/anchorium_demo_full.mp4` (1080p30, under 3:00; prove it with `ffprobe`)
- `video/anchorium_demo_60s.mp4`
- `video/captions.srt`, `vo_script.txt` (with timecodes matched to the real clip lengths)
- `youtube.md`:
  - **Title:** `Anchorium: the trading bot that can't break its own rules | NEAR HACKATHON`
  - **Description:** the elevator pitch · repo link · guard explorer link · "Built at NEAR HACKATHON" ·
    chapters: 0:00 The bot gets caught · 0:12 The problem · 0:27 How it works · 0:47 A full cycle on-chain ·
    1:40 Safety · 2:05 Honest money · 2:22 Verify it yourself
  - **Visibility:** public or unlisted, no password

---

## PART B: THE IMAGE GALLERY (11 images, 1800×1200)

### B1. Frame template (`templates/gallery_frame.html`)

- 64 px safe margin all round (Devpost crops thumbnails).
- Top-left: the wordmark "ANCHORIUM" (letter-spaced text). Top-right: "04 / 11".
- A headline (at most 7 words), plus one sub-line in muted grey that names the **source** (e.g. "Real tx on
  NEAR testnet · 4KxLnhn9…").
- The main visual sits in a rounded panel (`#161B22`, 16 px radius, 1 px `#30363D` border) covering about 70%
  of the area. Real captures are taken at 2× and scaled down, never stretched. Crop the tall dashboard
  screenshot into the relevant region; don't squash it.
- The guard rail runs along the bottom in the verdict colour.

### B2. The images (order matters: #1 becomes the thumbnail)

| # | File | Headline | Main visual | Rail |
|---|---|---|---|---|
| 1 | `01_cover.png` | **A trading bot you don't have to trust** | The real `NOTIONAL_ABOVE_MAX` terminal line, large, with the pitch below. *(Graphic + real text.)* | red → green gradient |
| 2 | `02_problem.png` | **Today you just trust the bot** | 3 problem cards ↔ 3 Anchorium answers (rules on-chain · journal on-chain · no custody). *(Graphic.)* | muted |
| 3 | `03_architecture.png` | **The rules live on-chain, not in the bot** | The architecture diagram from shot 3, as a still. *(Graphic.)* | green |
| 4 | `04_overruled.png` | **The bot tried to cheat. The contract said no.** | The dashboard card #2 ("contract overrode the agent") next to the NearBlocks tx `4KxLnhn9…` | red |
| 5 | `05_policy.png` | **The owner writes the rules on-chain** | Dashboard crop: kill switch ACTIVE + POLICY (ON-CHAIN) + TODAY panels | green |
| 6 | `06_funds_timeline.png` | **Always know where your money is** | Dashboard crop: one ALLOWED decision row, Signal → Guard verdict → Quote → Where the funds are → Outcome | green |
| 7 | `07_agent_key.png` | **A key that can only write the journal** | The real `view_access_key_list` output (one FunctionCall key) + the deletion tx `86WYN7Ro…` | green |
| 8 | `08_kill_switch.png` | **One transaction stops every new trade** | A **fresh Path B guard**: owner `pause` → the next `check_trade` paused (terminal), with the explorer tx link | red |
| 9 | `09_honest_money.png` | **Wins and losses, every cost shown** | Paper receipts −INR 2.10 and +INR 1.97 side by side, the exit formula rendered from LaTeX, an amber PAPER label (or the live receipt, if it exists) | amber |
| 10 | `10_engineering.png` | **388 + 42 tests. 11 review findings fixed.** | Real terminal: `make test-py` (388 passed), `make contract-test` (40 + 2), and `make freeze` passed true | green |
| 11 | `11_verify.png` | **Verify it in one minute** | Real fresh-clone terminal running `make setup && make demo`, with the repo URL and `guard.anchorium-team.testnet` in large mono | green |

### B3. Gallery QA

- Each PNG is exactly 1800×1200, under 5 MB, readable at 50% zoom, with no secrets and no `/Users/` paths.
- Every number on every image matches SHOTLIST.md.
- Render `gallery/_contact_sheet.png` (a 4×3 grid) and show it to me.

---

## 5. EXECUTION ORDER AND CHECKPOINTS

1. **Recon (≤ 20 min).** Run `make status`, `make test-py`, `make contract-test`, `make demo`, and check
   whether `docs/live_trade_report.md` or a closed version A paper round (`docs/paper_trade_A_report.md`)
   exists. Write SHOTLIST.md (shot → what it proves → the exact command/URL → today's real value).
   🛑 **CHECKPOINT 1:** show me SHOTLIST.md and list any shot you can't capture for real.
2. **Look and feel.** Build the VHS theme, the title card and gallery image #1.
   🛑 **CHECKPOINT 2:** show me both before mass production.
3. **Capture.** Create the fresh Path B deployment (log its account), record every VHS and Playwright scene,
   and render all 11 gallery images plus the contact sheet.
4. **VO script.** Write `vo_script.txt`, timed to the real clip lengths (about 2.3 words per second).
   🛑 **CHECKPOINT 3:** I record `submission/media/vo.m4a` and tell you when it's done.
5. **Assemble** the full cut and the 60 s cut, burn in captions, mix audio, and run `ffprobe` to confirm the
   length is under 3:00.
   🛑 **CHECKPOINT 4:** show me both videos and the contact sheet.
6. **Commit** the sources and the gallery PNGs, and push. **Do not upload to YouTube, post on X or submit the
   form.** Humans do that.

## 6. DEFINITION OF DONE

- [ ] The full video is 2:40–2:50, 1080p, opens on the red REJECTED frame, and has captions burned in
- [ ] The 60 s X cut is rendered
- [ ] 11 gallery PNGs (1800×1200, under 5 MB) and the contact sheet are approved
- [ ] Every on-screen fact is traceable in SHOTLIST.md, and paper is labelled as paper
- [ ] Production guards untouched: every write scene used the fresh Path B account
- [ ] No secrets or local paths in any frame
- [ ] `make media` rebuilds everything, and youtube.md is ready
