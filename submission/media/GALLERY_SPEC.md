# GALLERY SPEC v2: 11 images that win (replaces Part B of MEDIA_PROMPT.md)

> Save as `submission/media/GALLERY_SPEC.md`. Where this file and MEDIA_PROMPT.md differ about the gallery,
> this file wins. All the honesty rules in MEDIA_PROMPT.md section 2 still apply in full.

---

## 0. WHY v1 OF THE COVER ISN'T ENOUGH (fix all of these)

The current `01_cover.png` is honest and on-palette, but:

1. **It fails the thumbnail test.** On Devpost, the cover shows at about 400 px wide. At that size, the 3-line
   terminal block and the paragraph turn to grey noise. Only the headline survives.
2. **It's a document, not a poster.** It has about 90 words of text. A gallery image gets 2 seconds of
   attention.
3. **The hero is buried.** `$4.99 → NOTIONAL_ABOVE_MAX` is the whole story, but it sits inside a log line
   next to a VWAP/ATR line that judges don't need.
4. **The bottom third is a paragraph** and the rail is barely visible.
5. **It mixes two sources without saying so.** The terminal shows `make demo` against the *demo* guard
   ("would call"), while the card shows the real journal on the *team* guard. Pick one per image, or label both
   clearly.

---

## 1. THE RULES EVERY IMAGE MUST PASS

| Rule | Spec |
|---|---|
| **One idea per image** | One headline, one hero element, one proof |
| **The thumbnail test** | Render at 25% (450×300). The headline and the hero must still read. If they don't, enlarge or cut. |
| **The 2-second test** | At most 30 words of body text on the whole image, excluding mono proof strings |
| **Minimum sizes at 1800×1200** | Headline 84–104 px bold · sub-line 30 px · hero mono 48–72 px · body 28 px · **nothing below 22 px** |
| **The proof chip** | Every image has a small chip, bottom-left: `SOURCE · real tx 4KxLnhn9… · NEAR testnet · 10 Nov 2026` (or the command + date). This is what makes it believable. |
| **Annotated, not raw** | Real screenshots get numbered callouts (①②③) with 3–6 word labels, in the verdict colour. A screenshot without callouts isn't finished. |
| **Real data only** | Every number, hash and account comes from the facts table (section 5) or a fresh command run, logged in SHOTLIST.md |
| **Crisp** | Render at `device_scale_factor=2` (3600×2400), downscale to 1800×1200 with Lanczos, then pngquant/oxipng. Must stay under 5 MB. |

---

## 2. THE DESIGN SYSTEM (build once as `templates/gallery.css`, use everywhere)

**Grid.** 1800×1200, 72 px outer margin, 12 columns with 24 px gutters. Header band 0–140 px, content
160–1040 px, footer band 1060–1200 px.

**Colour.** Sample these from `dashboard/static/index.html`:
- background `#0D1117`, with a faint 40 px grid pattern at 3% opacity
- panels `#161B22`, borders `#30363D`
- text `#E6EDF3`, muted `#8B949E`
- **green `#3FB950` = allowed/safe · red `#F85149` = blocked · amber `#D29922` = paper/dry run · blue
  `#58A6FF` = links/NEAR**
- Allowed accent: one soft radial glow per image (8% opacity) in the image's verdict colour, behind the hero.
  Nothing else decorative.

**Components** (build each as a reusable CSS class):

| Component | Look |
|---|---|
| `header` | Left: `ANCHORIUM` (22 px, letter-spacing 0.4em). Right: `04 / 11` in mono, muted. |
| `headline` | Inter 800, 84–104 px, tracking −0.02em, max 2 lines, max 7 words |
| `kicker` | 22 px uppercase muted label above the headline, e.g. `PROOF 01 · THE CONTRACT OUTRANKS THE BOT` |
| `verdict-pill` | Mono, 1.0× hero size, 2 px border + 12% fill in the verdict colour, 10 px radius |
| `panel` | `#161B22`, 1 px border, 16 px radius, 0 12px 40px rgba(0,0,0,.45) |
| `callout` | A numbered circle (36 px) plus a 3–6 word label, joined to its target by a 2 px line |
| `stat` | A huge number (120–160 px, mono, the verdict colour) over a 24 px muted label |
| `proof-chip` | Bottom-left, mono, 20 px, muted, with a ● dot in the verdict colour |
| `rail` | 10 px bar along the bottom edge in the verdict colour (01 uses a red → green gradient) |
| `footer-tags` | Bottom-right, muted: `NEAR testnet · NEAR Intents 1Click · Rust guard` |

**The series story.** Browsed in order, the 11 images tell the pitch:
**hook → problem → solution → 5 proofs → honest money → engineering → call to action.**

---

## 3. THE 11 IMAGES, ONE BY ONE

> For each one: build it, run the thumbnail test, log its sources in SHOTLIST.md.

### 01 · `01_cover.png`: THE HOOK (this is the Devpost thumbnail)
- **Layout:** left 7 columns: text. Right 5 columns: one big "verdict card".
- **Left:** kicker `NEAR HACKATHON · DEFI · AGENTS`. Headline across 2 lines: **"A trading bot you don't
  have to trust."** Then three chips in a row: `Rules in a NEAR contract` · `Every decision on-chain` ·
  `Never holds your money`.
- **Right, the verdict card** (a panel with a red glow behind it), as 3 stacked rows of large mono text:
  - `Agent asks   BUY $4.99`
  - `Policy max   $2.00`
  - `Contract     [ REJECTED · NOTIONAL_ABOVE_MAX ]`, with the red verdict pill at about 48 px
  - Under it, small: `+ verdict_override event, permanent`
- **Proof chip:** `real tx 4KxLnhn9… · guard.anchorium-team.testnet · 10 Nov 2026`
- **Rail:** red → green gradient. Remove the paragraph and the VWAP/ATR signal line.

### 02 · `02_problem.png`: WHY THIS MATTERS
- **Headline:** **"Today, you just trust the bot."**
- **Layout:** two columns of 5 rows. Left column header: `EVERY BOT TODAY` (red ✕ on each row). Right column
  header: `ANCHORIUM` (green ✓ on each row). The rows pair up:
  1. Holds your keys → Settles into your own account, no custody
  2. Rules live in its own code → Rules live in a NEAR contract
  3. Breaks rules silently → Every decision journaled, overrides flagged
  4. Track record = screenshots → The contract computes P&L itself
  5. Kill switch inside the bot → Owner pause in 1 tx + auto-pause at the loss cap
- **Proof chip:** `README · Safety model`. **Rail:** muted → green.

### 03 · `03_architecture.png`: HOW IT WORKS
- **Headline:** **"The rules live on-chain, not in the bot."**
- **Hero:** a left-to-right flow of 5 numbered nodes with arrows:
  `① Signal (frozen strategy)` → `② check_trade (guard)` → `③ 1Click quote` → `④ record_decision (on-chain)` →
  `⑤ Settle → your account → record_outcome`.
- Draw two shaded **zones** under the nodes, labelled honestly: `NEAR TESTNET: guard + journal` and
  `NEAR MAINNET: Intents settlement`.
- Above the guard: an `OWNER` box with a red arrow labelled `pause / update_policy`.
- **Proof chip:** `README · Architecture`. **Rail:** green.

### 04 · `04_overruled.png`: PROOF 1: THE CONTRACT OUTRANKS THE BOT
- **Kicker:** `PROOF 01`. **Headline:** **"The bot tried to cheat. The contract said no."**
- **Layout:** left: a real crop of dashboard card #2 (`REJECTED NOTIONAL_ABOVE_MAX` · `contract overrode the
  agent`). Right: a real NearBlocks crop of the **logs / receipt with `EVENT_JSON` → `verdict_override`**
  (not the green "Success" header, which confuses judges).
- **Callouts:** ① `Agent claimed: allowed` ② `Contract: $4.99 > $2.00 cap` ③ `verdict_override, on-chain
  forever`
- **Proof chip:** `real tx 4KxLnhn9… · 10 Nov 2026 16:27 UTC`. **Rail:** red.

### 05 · `05_policy.png`: PROOF 2: THE OWNER'S RULES ARE ON-CHAIN
- **Kicker:** `PROOF 02`. **Headline:** **"The rules are code anyone can read."**
- **Hero:** a real crop of the dashboard's KILL SWITCH + POLICY (ON-CHAIN) panels, scaled large.
- **Side callouts:** ① `$2 max per trade` ② `$1 daily loss → auto-pause` ③ `Only the owner can change
  these`. Plus a small note: `Even rule changes are public: 100 → 150 bps, policy_updated tx DExrWkb3…`
- **Proof chip:** `make status · read from guard.anchorium-team.testnet · <date>`. **Rail:** green.

### 06 · `06_funds_timeline.png`: PROOF 3: ALWAYS KNOW WHERE YOUR MONEY IS
- **Kicker:** `PROOF 03`. **Headline:** **"Every decision, fully traceable."**
- **Hero:** a real crop of one **ALLOWED** decision card (#7 or #8, or a fresh Path B one), enlarged to full
  width. Above it, a 5-step stepper matching the card's columns: `Signal → Guard verdict → 1Click quote →
  Where the funds are → Outcome`.
- **Callouts:** ① `contract said OK` ② `worst-case slippage checked` ③ `funds never left your account (dry
  run)` ④ `P&L computed by the contract`
- **Proof chip:** the real tx hashes of that decision + outcome. Add the amber `DRY RUN` label if it was
  paper. **Rail:** green.

### 07 · `07_agent_key.png`: PROOF 4: THE BOT'S KEY CAN ONLY WRITE THE JOURNAL
- **Kicker:** `PROOF 04`. **Headline:** **"A key that can only do one thing."**
- **Layout:** left: the real `view_access_key_list` output (crop it to the permission block and highlight
  `receiver_id: guard.anchorium-team.testnet` and `method_names: [record_decision, record_outcome]`). Right:
  two lists:
  - `CAN` (green): record_decision · record_outcome
  - `CANNOT` (red): move NEAR · pause · change rules · call any other contract
- A small line: `We deleted its full-access key ourselves · tx 86WYN7Ro…` and `sandbox test:
  function_call_key_can_journal_but_nothing_else`
- **Proof chip:** `keyless RPC query · <date>`. **Rail:** green.

### 08 · `08_kill_switch.png`: PROOF 5: ONE TRANSACTION STOPS EVERYTHING
- **Kicker:** `PROOF 05`. **Headline:** **"One transaction stops every new trade."**
- **Layout:** a before/after split with an arrow between. Left (green): `check_trade → OK`. Middle: the
  owner's `pause` tx (red). Right (red): `check_trade → PAUSED`. A bottom strip: `Exits stay possible ·
  the contract also pauses itself at the $1 daily loss cap (sandbox test)`.
- **Source:** the **fresh Path B media guard only**, labelled with its account. Never the team or demo guard.
- **Proof chip:** the real pause/resume tx hashes on `<media account>`. **Rail:** red.

### 09 · `09_honest_money.png`: HONEST MONEY
- **Headline:** **"Every fee shown. Wins and losses."**
- **Hero:** a **waterfall chart** of paper trade 2 (real numbers, README): gross move **+$0.0317** → 1Click
  fee **−$0.0080** → spread **−$0.0011** → gas **−$0.0016** → **net +$0.0210 (+INR 1.97)**. Green bars up,
  red bars down, the net bar outlined.
- Beside it, a smaller red card: `Paper trade 1 · BTC · −INR 2.10 · costs beat the move`
- Under both, small: the exit rule formula (rendered with KaTeX) and `31.2% tax reserved before any profit is
  called a profit`
- An amber `PAPER · real 1Click quotes, simulated fill` label (or the live receipt, if one exists by then).
- **Proof chip:** `docs/paper_trade_profit_report.md · docs/paper_trade_report.md`. **Rail:** amber.

### 10 · `10_engineering.png`: BUILT LIKE IT HANDLES REAL MONEY
- **Headline:** **"Built like it handles real money."**
- **Hero:** a 2×3 grid of `stat` blocks (re-run the commands and use today's numbers):
  - **388** Python tests
  - **40 + 2** contract unit + sandbox tests
  - **28/28** frozen strategy parameters verified
  - **11** independent-review findings fixed (1 critical)
  - **60** real 1Click round trips measured
  - **0** floats in money maths
- A thin strip of the real terminal underneath: `388 passed`, `40 passed`, `"passed": true`
- **Proof chip:** `make test-py · make contract-test · make freeze · <date>`. **Rail:** green.

### 11 · `11_verify.png`: THE CALL TO ACTION
- **Headline:** **"Don't trust us either. Verify in one minute."**
- **Hero:** 3 huge mono steps:
  - `git clone github.com/Adesh2204/anchorium-near`
  - `make setup`
  - `make demo`
- Then a `you'll see` list: `real check_trade calls` · `real 1Click quotes` · `the compromised agent
  rejected` · `the live on-chain journal`
- On the right: the repo URL and `guard.anchorium-team.testnet` in large mono, plus an optional QR code to the
  repo (`qrcode` library, styled in the palette).
- Footer: `Built at NEAR HACKATHON · Adesh & Parth · MIT`
- **Proof chip:** `docs/JUDGES.md`. **Rail:** green.

---

## 4. PRODUCTION ORDER AND THE ONE CHECKPOINT

1. Build `gallery.css` + `gallery_frame.html` with every component from section 2.
2. Capture all the real crops at 2× (dashboard via Playwright; NearBlocks logs; terminal output via VHS
   stills or Playwright-rendered HTML of the real command output).
3. Render 01–11 and run QA (section 6) on each one.
4. Render `gallery/_contact_sheet.png` (a 4×3 grid) **and** `gallery/_thumbnail_test.png` (all 11 at 25%).
5. 🛑 **CHECKPOINT:** show me both sheets, plus the full-size 01, 04 and 09. Don't stop before all 11 are
   done.
6. After I approve, carry on with Part A (the video) in MEDIA_PROMPT.md, using the same `gallery.css` for
   every title card so the video and the images look like one product.

---

## 5. FACTS TABLE (the only numbers allowed; re-verify before rendering)

| Fact | Value |
|---|---|
| Guard / demo guard / owner / agent | `guard.anchorium-team.testnet` / `demo.anchorium-team.testnet` / `anchorium-team.testnet` / `agent.anchorium-team.testnet` |
| Compromised agent | $4.99 asked, $2.00 cap, `NOTIONAL_ABOVE_MAX`, `verdict_override`, tx `4KxLnhn9mdtaQq5wVpxK525yMyBsdpfyKNfqw1aBbAWM`, 10 Nov 2026 16:27:52 UTC |
| Agent key deletion | tx `86WYN7RoR1PNERjpSzspQXJ6uDX2fqnGP4m3LLrGyPq8` |
| Slippage cap change | 100 → 150 bps, `policy_updated`, tx `DExrWkb3p8zQauc31dsqhCA6M9jfbWvd4YLWh4XX75df` |
| Policy | $2/trade · 10 entries/day · $1 daily loss cap (auto-pause) · 150 bps · 60 s cooldown · 1 open position |
| Paper trade 1 | BTC, −$0.0224, −INR 2.10 |
| Paper trade 2 | NEAR, gross +$0.0317, fee −$0.0080, spread −$0.0011, gas −$0.0016, net +$0.0210, +INR 1.97 |
| Tests | 388 Python · 40 unit + 2 sandbox · freeze 13 files / 28 parameters |
| Review | 11 findings, 1 critical, all fixed |
| Quote sampling | 60 real dry-quote round trips |
| Live mainnet trades | **none yet**, so every money image is labelled PAPER |

---

## 6. QA CHECKLIST (every image)

- [ ] Exactly 1800×1200, under 5 MB
- [ ] Passes the thumbnail test at 25%
- [ ] At most 30 words of body text, and nothing below 22 px
- [ ] Every number is in the facts table or logged in SHOTLIST.md from a fresh run
- [ ] Paper/dry run labelled in amber wherever money appears
- [ ] No private keys, `.env` contents, `/Users/` paths, emails or browser chrome/tabs
- [ ] Has a proof chip, a rail and a header number
- [ ] Shares the same system as the other 10 (same CSS, no one-off styles)
