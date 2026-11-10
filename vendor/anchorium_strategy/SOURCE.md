# Vendored strategy: source and provenance

This directory is **prior, non-NEAR work by our team**. It is copied unchanged from our earlier
repository so the NEAR agent can reuse its entry signals. It is **not** part of the NEAR HACKATHON
submission and is not submitted for judging. The judged work is `baseline...hackathon-submission`.

| Field | Value |
| --- | --- |
| Source repository | https://github.com/Adesh2204/Autonomous-Trading-Bot-Crypto |
| Source commit | `68af4eb4334043aea16e2650658d7df20fe68aaf` (`main`, "Merge pull request #19 from Adesh2204/codex/hype-5x") |
| Strategy baseline inside the source | `nse-working-baseline` (`ed0dba5b0577da38a500d9dc208fe9938d3f3a14`) |
| Copied with | `git archive 68af4eb` followed by a plain file copy, keeping the source's relative paths |
| Origin | Built for and run in Perpetual Alpha, a separate non-NEAR trading competition |

## What was copied

These 12 Python files are the complete local import closure of `FrozenStrategy`
(`algo-trading-bot/frozen_strategy.py`), computed by walking every `import` and `from ... import`
statement, including imports inside functions. They are byte-identical to the source commit.

| File | Role |
| --- | --- |
| `algo-trading-bot/frozen_strategy.py` | Adapter: completed candles in, `generate_vwap_signal` out |
| `algo-trading-bot/signal_brain.py` | The frozen VWAP/EMA/ATR signal brain (file hash pinned by the manifest) |
| `algo-trading-bot/config.py` | Holds the frozen parameters. It also contains inert legacy NSE watchlist and fee constants. No vendored code uses them, and they are kept so the file stays byte-identical. |
| `algo-trading-bot/strategy_freeze.py` | The original freeze verifier |
| `algo-trading-bot/candle_normalizer.py` | Candle list to DataFrame |
| `algo-trading-bot/session_manager.py` | UTC daily VWAP anchor |
| `algo-trading-bot/safety_errors.py` | Error type |
| `algo-trading-bot/venue_config.py` | Mode flags imported by `config.py` (defaults to PAPER / `ALLOW_LIVE_ORDERS=false`) |
| `algo-trading-bot/exchange/__init__.py`, `base_exchange.py`, `market_health.py`, `venue_profiles.py` | Data types and validation only. No network calls, signing or order transport. |

`docs/strategy_freeze.source.json` is a verbatim copy of `docs/strategy_freeze.json` at the source commit.

`SHA256SUMS` lists the SHA-256 of every copied file. `scripts/check_strategy_freeze.py` verifies them.

## What was deliberately not copied

- **Legacy NSE / Angel One modules.** The source manifest also freezes 8 management methods (exits, candle-close
  handling, daily bias, trade execution) and 4 parameters. They live in `execution_engine.py`, `safe_trader.py`,
  `live_stream.py` (`AngelOneLiveStream`) and `runtime_observation.py`. Those modules depend on the retired
  Angel One broker stack, and `FrozenStrategy` does not use them, so they are not vendored.
- **Exchange write paths, signers, SDKs, LFS evidence and runtime state.** None of these were copied.
- **Original tests.** Every test file in the source that exercises `signal_brain` or `FrozenStrategy` also imports
  modules outside this closure (NSE paper trader, Hyperliquid runtimes, research harnesses). None could be copied
  unchanged, so none were. Parity with the source is instead checked by the tests added after the `baseline` tag.

## The scoped freeze manifest (the one derived file)

`docs/strategy_freeze.json` is the only derived file here, and it is the file the vendored `strategy_freeze.py`
reads at runtime. It was generated from the source manifest:

- `files` and the `config.py` entry of `parameters` are copied unchanged. That is the `signal_brain.py` SHA-256
  and 28 parameters.
- `methods` is empty. The 8 source methods are kept verbatim under `excluded_methods`.
- The 4 `execution_engine.py` parameters are kept verbatim under `excluded_parameters`.
- `source_manifest_sha256` pins the verbatim source manifest.

`scripts/check_strategy_freeze.py` fails if any vendored byte changes. It also fails if any scoped entry differs
from the source, if anything from the source is missing from the scoped and excluded entries combined, or if the
vendored `verify_strategy_freeze()` does not pass.

```sh
python scripts/check_strategy_freeze.py   # expect "passed": true
```
