# Version A paper trade report: session `paper-a`

PAPER (DRY_RUN): real 1Click dry quotes and a real testnet journal; fills are simulated at each quote's guaranteed minimum (`minAmountOut`). No funds moved.

| | |
| --- | --- |
| Started | 2026-11-12 11:24:22 IST |
| Finished | 2026-11-12 20:00:07 IST: RUN_UNTIL 2026-11-12 20:00:00 IST |
| Capital | 5.334437 USDC = INR 500.00 at 93.7306 INR/USD (Coinbase public exchange-rates API, 2026-11-12T05:54:22+00:00) |
| Strategy | Version A: long-only NEAR on the unchanged frozen strategy's LONG signal (Hyperliquid 1m) |
| Limits | $2.00 per trade, 1 open round, 3 rounds, session loss cap INR 20, cost filter 1.600% (fee + slippage + all deposit NEAR) |
| Exits | PROFIT_EXIT at INR 1.00 after fees, gas and a 31.2% tax reserve; STOP_LOSS at net <= -5% of entry; TIME_EXIT at the cut-off; no buys after the buy cut-off; 60 min cooldown after a stop |
| Guard / journal | [guard.anchorium-team.testnet](https://testnet.nearblocks.io/address/guard.anchorium-team.testnet) (testnet): check_trade + record_decision/record_outcome for every leg |
| Result | 0 round(s) closed, session after tax INR +0.00 |

## Receipts

No round closed in this session.

## Skips and refusals (last 200 events)

none

## One-time setup costs (not part of any round)

None in a paper session. A LIVE session adds the exchange withdrawal fee and the trading account's one-time USDC/wNEAR storage registration (0.00125 NEAR each).

## Cost notes

* Each deposit leg costs NEAR: the transfer's gas plus the storage registration of the new 1Click deposit address (0.00125 NEAR; every deposit address is new and unregistered, checked 2026-11-12). Paper receipts use the measured estimate 0.0014670906627479 NEAR per leg.
* "Slippage" is everything between the market price (Hyperliquid NEAR mid at the quote) and the guaranteed output, apart from the 1Click fee (20 bps of the input): price impact, the quote's spread and the 10 bps slippage tolerance.
* Display rounding: components are rounded so that they add up exactly to the rounded net; the tax line is rounded up. Exact values are kept in the session state.

## Tax note

Each $2 trade is far below the TDS threshold, so no TDS is deducted. The 31.2% reserve (30% + 4% cess on gains; losses neither taxed nor offset) is set aside to pay at ITR filing. This is a conservative reserve, not tax advice.

## Events (last 40)

* [2026-11-12 11:24:22 IST] START: DRY_RUN session paper-a: 5.334437 USDC = INR 500.00 at 93.7306 INR/USD (Coinbase public exchange-rates API)
* [2026-11-12 13:24:23 IST] HEARTBEAT: alive; 0 closed, session after tax INR +0.00; flat
* [2026-11-12 15:24:24 IST] HEARTBEAT: alive; 0 closed, session after tax INR +0.00; flat
* [2026-11-12 17:25:06 IST] HEARTBEAT: alive; 0 closed, session after tax INR +0.00; flat
* [2026-11-12 19:25:07 IST] HEARTBEAT: alive; 0 closed, session after tax INR +0.00; flat
* [2026-11-12 20:00:07 IST] STOP: RUN_UNTIL 2026-11-12 20:00:00 IST

## Note (added after the session, from a replay)

Replaying the unchanged frozen strategy on the same Hyperliquid 1m candles for 11:24-20:00 IST gives 45 signals,
all `BREAKDOWN_SHORT`, and no `LONG`. NEAR fell from $3.5282 to $3.3629 (low $3.3406, about -4.7%) over the
session. Version A buys only on a LONG signal, so it correctly stayed flat: no buy, no fee, no loss. This is a
"no trade" result, not a profit.
