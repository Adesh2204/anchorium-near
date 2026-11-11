# Regime backtest: A (long-only) vs B (regime long-only) vs C (regime long + bear)

Run 2026-11-12 01:33 IST on branch `regime`. Every number below comes from
`docs/data/regime_backtest_results.json`; the tables are printed by `scripts/render_regime_backtest.py` from that file.
Reproduce with `.venv/bin/python scripts/backtest_regime.py --seeds 200` (about 10 min after the one-off signal replay).

**Summary.** Out of sample (days 31-60), **neither B nor C beat A. All three made the same single trade and
ended with identical results**: one bull round opened on 12 Oct 09:31 IST at $5.871 (the regime was still BULL
from the September rally), NEAR then fell 39%, and the profit-only rule kept that round open for the rest of the
window (unrealized -INR 75.04 after tax, -INR 74.22 vs just holding). In days 1-30 (NEAR +149%), A did best.
Every variant spent 90-99% of the time inside one open round. That, not the regime filter, decides the results.

## 1. What was tested

| | |
| --- | --- |
| Market data | Binance USD-M NEARUSDT 1m klines, 2026-09-08 to 2026-11-10 (UTC), from data.binance.vision daily archives. Every file's SHA256 matched Binance's published checksum (`docs/data/binance_near_1m_sha256.txt`). No gaps, nothing filled. |
| Why not Hyperliquid 1m | Hyperliquid's public API serves only about 3.5 days of 1m candles (checked: nothing older than 4 days). Its 15m/1h history was used as a cross-check (section 5). |
| Windows | Days 1-30 = 12 Sep to 11 Oct (sanity check only). Days 31-60 = 12 Oct to 10 Nov (strictly out of sample). Each window starts with a fresh INR 500 wallet. 4 extra warm-up days before 12 Sep feed the indicators only. |
| Signals | The frozen strategy, unchanged, through `signals.evaluate_latest` (the live agent's exact code path and 26 h window), on every one of the 86,400 minutes: 5,635 signals (BREAKOUT_LONG 2,214, PULLBACK_LONG 433, BREAKDOWN_SHORT 2,596, PULLBACK_SHORT 392). Freeze check passes. |
| Regime | `anchorium/agent/regime.py`, the six parameters fixed before any run and never changed: EMA20/EMA50 on 1h closes, EMA20 rising = above its value one completed 1h bar earlier, price vs EMA20 (15m close), CHOP when 1h ATR14% < 2 x the round-trip cost, 2-bar 15m hysteresis. Only 1h bars that had closed are used (no look-ahead). |
| Leg costs | 60 real 1Click DRY quotes per direction for $2, taken 2026-11-12 00:57-01:04 IST (`docs/data/quote_costs_2026-11-12.json`), measured on the GUARANTEED output (`minAmountOut`) against the Hyperliquid mid. USDC->wNEAR: median 35.4 bps (min 28.2, p90 38.0, max 45.3). wNEAR->USDC: median 58.3 bps (min 49.2, p90 61.7, max 66.2). Both include the 20 bps 1Click fee. Each simulated quote draws one sample (bootstrap, seeded). |
| Gas | 0.001467 NEAR per leg = a measured mainnet `ft_transfer` (0.000217) + storage registration of the new 1Click deposit address (0.00125, `storage_balance_bounds`), as in the live exit floor. Valued at the NEAR price of the moment, paid from the separate gas NEAR (not capital), and subtracted from every result. |
| FX | ECB USD/INR reference rate of each day (Frankfurter; `docs/data/fx_usd_inr_ecb.json`), 93.69-96.78 over the period. |
| Wallet | INR 500 split 50/50 into USDC and wNEAR at the window's first price. FIFO lots in integer base units (USDC 6, wNEAR 24 decimals), INR in Decimal. |
| Rules (all variants) | Entry at a signal bar's close. $2 per round (at most 98% of the side's balance, at least $1), 1 open round, at most 3 rounds per IST day. SKIPPED_COST when the entry quote + the median exit cost > 1.3% (never triggered: costs were 0.9-1.1%). Every minute the round's close is quoted; a close happens only when `net_after_tax_inr >= 1.00` on that quote AND on a second, fresh quote. The fill is the fresh quote's guaranteed amount. Never a close at a loss, no stop, no time exit. |
| Tax | Every swap disposes of the asset sold (USDC included). proceeds = INR market value of what was received; the asset received takes that value as its cost. Reserve = 31.2% x max(gain, 0) per disposal; losses never offset. A reserve for the ITR, not tax advice. |
| Variants | A: every LONG opens a bull round (no regime). B: LONG only in a confirmed BULL. C: B, plus a SHORT in a confirmed BEAR opens a bear round (sell $2 of wNEAR, buy back later with all the USDC received; profit = extra wNEAR). |

## 2. Out of sample: days 31-60 (12 Oct to 10 Nov; NEAR 5.894 -> 3.585, -39.18%)

| Variant | Rounds opened / closed | Win % | Realized net after tax (INR) | Open round at end, unrealized after tax (INR) | Equity vs start (INR) | vs just holding (INR) | vs holding, liquidation basis (INR) | Max drawdown INR (%) | Avg hold (h) | Time in an open round | Tax reserve (INR) | Gas (INR) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A long-only (current) | 1 / 0 | 0.00% | 0.00 | -75.04 | -180.95 | -74.22 | -74.22 | 190.59 (37.95%) | n/a | 99.44% | 0.00 | 0.82 |
| B regime long-only | 1 / 0 | 0.00% | 0.00 | -75.04 | -180.95 | -74.22 | -74.22 | 190.59 (37.95%) | n/a | 99.44% | 0.00 | 0.82 |
| C regime long + bear | 1 / 0 | 0.00% | 0.00 | -75.04 | -180.95 | -74.22 | -74.22 | 190.59 (37.95%) | n/a | 99.44% | 0.00 | 0.82 |

Just holding the starting mix: -INR 106.74 vs start. Regime time share: BULL 8.8%, BEAR 18.3%, CHOP 73.0%
(raw readings: ATR_BELOW_COST on 1,714 of 2,880 bars, so the low-volatility rule caused most of the CHOP).

Robustness (200 different cost draws) and stress (every quote at the p90 leg cost, 60.3 bps):

| Variant | Realized INR p10 / p50 / p90 | vs holding INR p10 / p50 / p90 | Rounds closed p10 / p50 / p90 | Stress (every quote at the p90 cost): closed, realized, vs holding |
|---|---|---|---|---|
| A | 0.00 / 0.00 / 0.00 | -74.22 / -74.19 / -74.15 | 0 / 0 / 0 | 0, 0.00, -74.48 |
| B | 0.00 / 0.00 / 0.00 | -74.22 / -74.19 / -74.15 | 0 / 0 / 0 | 0, 0.00, -74.48 |
| C | 0.00 / 0.00 / 0.00 | -74.22 / -74.19 / -74.15 | 0 / 0 / 0 | 0, 0.00, -74.48 |

**Path dependence: the same window started 0-9 days later** (fresh wallet each time, seed 0). Not part of the
pre-registered test; shown because one entry decided the main result.

| Start | A: closed, realized, vs hold | B: closed, realized, vs hold | C: closed, realized, vs hold |
|---|---|---|---|
| +0d | 0, 0.00, -74.22 | 0, 0.00, -74.22 | 0, 0.00, -74.22 |
| +1d | 1, +1.10, -60.12 | 0, 0.00, -62.71 | 10, +16.20, -18.79 |
| +2d | 1, +1.53, -59.40 | 0, 0.00, -62.71 | 11, +16.74, -18.96 |
| +3d | 3, +3.74, -59.82 | 0, 0.00, -62.71 | 3, +4.10, -60.66 |
| +4d | 0, 0.00, -61.68 | 0, 0.00, -61.68 | 0, 0.00, -61.68 |
| +5d | 2, +2.31, -59.96 | 2, +2.89, -59.60 | 8, +13.32, -20.22 |
| +6d | 2, +2.75, -57.50 | 1, +1.44, -58.80 | 7, +10.19, -22.31 |
| +7d | 2, +2.24, -60.22 | 1, +1.20, -58.24 | 1, +1.20, -58.24 |
| +8d | 2, +2.81, -61.07 | 1, +1.28, -59.28 | 1, +1.28, -59.28 |
| +9d | 2, +2.42, -60.05 | 1, +1.35, -59.21 | 1, +1.35, -59.21 |

## 3. Sanity check: days 1-30 (12 Sep to 11 Oct; NEAR 2.364 -> 5.895, +149.37%)

| Variant | Rounds opened / closed | Win % | Realized net after tax (INR) | Open round at end, unrealized after tax (INR) | Equity vs start (INR) | vs just holding (INR) | vs holding, liquidation basis (INR) | Max drawdown INR (%) | Avg hold (h) | Time in an open round | Tax reserve (INR) | Gas (INR) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A long-only (current) | 12 / 11 | 91.67% | +15.27 | -0.23 | +413.98 | +38.56 | +96.27 | 166.32 (18.05%) | 32.91 | 99.49% | 110.05 | 11.05 |
| B regime long-only | 10 / 9 | 90.00% | +11.06 | -0.25 | +409.71 | +34.28 | +91.99 | 166.32 (18.14%) | 32.7 | 90.09% | 106.96 | 9.69 |
| C regime long + bear | 10 / 9 | 90.00% | +12.69 | -6.27 | +408.02 | +32.60 | +92.48 | 166.01 (18.14%) | 35.72 | 93.79% | 110.59 | 9.48 |

Just holding: +INR 375.42 vs start. Regime share: BULL 37.6%, BEAR 12.9%, CHOP 49.5%.

| Variant | Realized INR p10 / p50 / p90 | vs holding INR p10 / p50 / p90 | Rounds closed p10 / p50 / p90 | Stress (every quote at the p90 cost): closed, realized, vs holding |
|---|---|---|---|---|
| A | +14.45 / +15.03 / +15.45 | +31.12 / +38.14 / +38.55 | 11 / 11 / 13 | 10, +13.91, +30.59 |
| B | +10.97 / +11.42 / +12.92 | +26.28 / +34.13 / +34.57 | 9 / 9 / 9 | 8, +10.40, +27.04 |
| C | +12.07 / +12.36 / +12.60 | +31.90 / +32.22 / +32.63 | 9 / 9 / 9 | 9, +12.30, +17.26 |

| Start | A: closed, realized, vs hold | B: closed, realized, vs hold | C: closed, realized, vs hold |
|---|---|---|---|
| +0d | 11, +15.27, +38.56 | 9, +11.06, +34.28 | 9, +12.69, +32.60 |
| +1d | 11, +13.98, +37.17 | 10, +14.00, +16.49 | 10, +14.00, +16.49 |
| +2d | 12, +14.66, +32.86 | 8, +9.27, +32.44 | 8, +9.27, +32.44 |
| +3d | 10, +14.66, +29.31 | 10, +14.66, +29.31 | 10, +14.66, +29.31 |
| +4d | 11, +13.79, +37.00 | 10, +12.56, +35.69 | 10, +12.56, +35.69 |
| +5d | 9, +13.65, +28.24 | 9, +13.65, +28.24 | 9, +13.65, +28.24 |
| +6d | 6, +11.57, +26.15 | 6, +11.57, +26.15 | 6, +11.57, +26.15 |
| +7d | 6, +8.43, +23.01 | 6, +8.43, +23.01 | 6, +8.43, +23.01 |
| +8d | 7, +9.32, +23.93 | 7, +10.00, +24.64 | 7, +10.00, +24.64 |
| +9d | 6, +8.53, +7.86 | 6, +8.53, +7.86 | 6, +8.53, +7.86 |

## 4. One wallet over all 60 days (for context)

| Variant | Rounds opened / closed (bear) | Realized (INR) | Open at end, unrealized (INR) | vs start | vs holding | vs holding, liquidation basis | Max drawdown (INR) | Time in an open round |
|---|---|---|---|---|---|---|---|---|
| A long-only (current) | 13 / 12 (0) | +16.29 | -75.82 | +55.01 | -61.80 | -23.83 | 379.75 | 99.74% |
| B regime long-only | 11 / 10 (0) | +12.06 | -75.79 | +50.96 | -65.85 | -27.88 | 379.47 | 95.03% |
| C regime long + bear | 10 / 9 (1) | +12.69 | -64.65 | +64.17 | -52.65 | -14.67 | 365.05 | 96.89% |

Just holding: +INR 116.81 vs start.

## 5. Data-source checks

* Regime from Binance-built 15m/1h bars vs Hyperliquid's own 15m/1h candles, 23 Sep to 11 Nov (4,637 bars): the
  confirmed regime agrees on **97.39%** of bars; median close difference 4.75 bps.
* Frozen-strategy signals on 10 Nov (UTC) from Hyperliquid 1m vs Binance 1m: 102 vs 92 signals; 31 on the same
  bar and direction, 71 within 3 bars. **The minute-level signal timing depends on the venue.** The live agent
  uses Hyperliquid, so the exact rounds would differ; the regime itself transfers well.

## 6. What the numbers say

1. **Out of sample, B and C did not beat A.** They were identical to it: same round, same p10/p50/p90 over 200
   cost draws, same stress result. The regime was still BULL when the top came (EMAs lag), so B and C made the same
   entry as A.
2. **The profit-only rule plus one open round dominates everything.** All variants spent 90-99.7% of the time
   inside an open round. In a rising market that works (days 1-30: 9-11 closed rounds, every close at or above the
   target). In a falling market one entry near the top blocks the bot for weeks: 0 closes in 30 days and a
   -INR 75 unrealized loss on a $2 round. The "win %" counts only closed rounds as wins; nothing closed at a loss
   because nothing is allowed to.
3. **C helps only when it is not already stuck in a bull round.** Of the 9 shifted OOS starts, C was far ahead
   of A in 4: it closed 7-11 bear rounds (+INR 10.19 to +16.74 realized) and lost INR 35-41 less than A against
   holding, though each of those runs still ended stuck in a later bull round. It was INR 0.8-2.0 ahead in 3,
   equal in 1, and INR 0.84 behind in 1. In the rising window C was 0 to INR 20.68 behind A against holding
   (INR 0.71 ahead once), because the regime gate skipped profitable LONGs. This is suggestive, not a
   pre-registered result.
4. **FIFO tax makes bull closes harder as NEAR rises.** A bull close sells the oldest wNEAR first, which is the
   starting inventory, so each close realizes the inventory's gain. In days 1-30 the close-leg tax grew from
   INR 0.81 (round 1) to INR 18.60 (round 11), and the price move each round needed to clear INR 1 after tax grew
   from 2.3% (round 1) to 12.2% (round 11).
   The INR 110 of tax reserved by A in days 1-30 is mostly tax on the starting wNEAR's rise, which a holder owes
   only when selling. The liquidation-basis column charges both sides as if they sold everything at the end.
5. **Against just holding, the bot only won in the rising window** (+INR 33-39; +INR 92-96 on a liquidation basis).
   Over the falling window and over all 60 days, every variant was behind holding.
6. Costs: gas is 0.001467 NEAR per leg, INR 0.82 per leg at NEAR $5.87 (about 0.4% of a $2 leg) and
   INR 0.50 at $3.59. The quote round trip costs about 94 bps (median). The 1.3% cost filter never triggered.

## 7. Recommendation

On the pre-registered test, B and C do not beat A, so the evidence does not justify switching. **For the LIVE
INR 500 run I recommend A (long-only, profit-only)**. That is also the fallback section 9 requires if regime + bear
is not fully reviewed by Thu 12 Nov 23:00 IST. Run **C as DRY_RUN "experimental"** alongside it: in the shifted
falling-market starts it was never worse than A by more than INR 0.84 and was far better in 4 of 9, but
those are runs I looked at after the fact, and in the rising window it trailed A. Run B nowhere: it adds nothing over A or C.

Whichever variant runs, the live risk to expect is a round that stays open, possibly for weeks (no stop and no
time exit, by your rule). The paper run's "2 rounds closed" target may not be met before Fri 13 Nov 12:00 IST.

## 8. Limitations

* 60 days, one coin, and two very different halves (+149% then -39%). Too little for a statistical claim about
  either variant.
* Leg costs were sampled within one 7-minute window, at night IST. Costs at other times or in fast markets differ.
  The stress row uses the p90 cost for every quote.
* Fills are the guaranteed (`minAmountOut`) amounts; real 1Click fills are usually at or above that.
* Signals come from Binance candles; Hyperliquid's signal timing differs at the minute level (section 5).
* Storage registration (0.00125 NEAR) is charged on every leg, as the live code assumes. If 1Click's deposit
  addresses were already registered, gas would be about 7x lower.
* "Session" = one IST day for the 3-rounds cap.
