# AGENTS.md: Anchorium (NEAR HACKATHON entry)

Instructions for any coding agent (Claude Code, Codex, others) working in, or judging, this repository.

## What this repo is

Anchorium is an autonomous trading agent whose risk policy and decision journal live in a NEAR smart contract
(the guard). A second contract, the Vault, holds the trading funds and releases them only for an ALLOWED guard
decision (`SETTLEMENT=vault`; proven on testnet, no mainnet vault yet). The AI risk co-pilot runs only on NEAR AI
Cloud (TEE-signed, veto-only); its receipt hash is journaled as `rationale_hash` and `verify-ai` checks it offline.
Trades settle through the NEAR Intents 1Click API. PayPal appears only as an optional sandbox fiat on-ramp for the
paper budget. The judged work is `git diff baseline...hackathon-submission`. `vendor/anchorium_strategy/` is prior
non-NEAR work by our team and is not submitted for judging.

## Safety invariants (never weaken these)

1. **DRY_RUN is the default.** Without the LIVE gates, nothing signs or sends a mainnet transaction. Dry runs
   fetch real `dry: true` quotes and never deposit.
2. **No mainnet transaction without the human's explicit "yes" in the current session.** LIVE mode
   additionally requires all of the following:
   - `LIVE=1`
   - `MAX_LIVE_NOTIONAL_USD` (at most 2 USD per trade, 5 USD in total)
   - a confirmation typed in the terminal
   - an ALLOWED verdict from the guard contract
   - **Exception, version A autonomous mode** (owner's written authorization, FINAL TASK 2026-11-12): after the
     owner types `GO LIVE` at session start, `python -m anchorium autonomous` trades without a per-trade typed
     confirmation, ONLY inside: $2 per trade, at most 3 buys ($6 of entries) per session, 1 open round, INR 20
     session loss cap, no buys after Fri 13 Nov 16:00 IST, every leg ALLOWED by the guard and journaled. Its
     STOP_LOSS (-5%) and TIME_EXIT may sell at a loss (approved). Never widen these limits without the owner.
3. **Contracts are deployed to NEAR testnet only.** Never upgrade or redeploy the deployed guards (owner's
   instruction 2026-11-12); version A runs on the deployed v1 guard as is. Mainnet deployment is out of scope.
4. **No secrets in git.** Keys live in `~/.near-credentials` or a gitignored `.env`. `.env.example` holds
   placeholders only. The agent account uses a function-call access key restricted to the guard contract.
5. **Never submit the hackathon form, call the submission API, or post on X.** Humans do that.
6. **Honesty.** Never fabricate results, PnL, quotes, test counts or screenshots. Paper (dry-run) figures are
   always labelled as paper. Every README claim must be reproducible from this repo.

## Frozen strategy

- `vendor/anchorium_strategy/` must stay byte-identical to its source (see its `SOURCE.md`).
  Never edit, reformat or "improve" it: not VWAP, EMA, ATR, thresholds or periods.
- Verify with `python scripts/check_strategy_freeze.py` (expect `"passed": true`).
- New code may only consume it through the read-only adapter in `anchorium/agent/`.

## Market data and execution scope

- **Allowed:** read-only public candles for native BTC, ETH and NEAR (Hyperliquid mainnet public `/info`, no
  signer), and recorded replay fixtures.
- **Not allowed:** exchange order or signing paths of any kind (Hyperliquid or otherwise), Angel One / NSE code,
  or any import from the old repository beyond the vendored closure.
- **Settlement:** only through the 1Click API (`https://1click.chaindefuser.com/v0`), spot, long-only. A SHORT
  signal exits an open position; without one it is skipped locally (a spot account cannot short). Version A
  ignores SHORT signals and exits only by its profit / stop / time rules (`anchorium/money.py`).
- **Research only:** `research/regime/` (regime filter + bear rounds, docs/regime_backtest.md) is never imported
  by the runtime.

## Layout

| Path | What |
| --- | --- |
| `contracts/anchorium-guard/` | Rust guard contract (near-sdk), unit tests + sandbox tests; deployed wasm, never rebuilt |
| `contracts/anchorium-vault/` | Rust vault contract: releases funds only for ALLOWED guard decisions; unit + sandbox tests |
| `contracts/mock-ft/` | TEST/DEMO-ONLY NEP-141 token for the vault sandbox test and the testnet vault demo |
| `anchorium/near/` | NEAR RPC views, guard client, vault client, 1Click client |
| `anchorium/agent/` | Signal adapter, orchestrator, crash-safe checkpoint store |
| `dashboard/` | Dashboard (contract state + intent timeline); `/paypal` adds PayPal sandbox top-ups (never signs NEAR txs) |
| `anchorium/paypal/` | Optional fiat on-ramp: PayPal Orders v2 **sandbox-only** client + idempotent funding ledger |
| `anchorium/ai/` | NEAR AI Cloud client + receipt verifier (`near_ai.py`), veto-only co-pilot, `verify-ai` commands |
| `fixtures/near_ai/` | Recorded NEAR AI Cloud attestation(s) (and signed receipts once credits exist) for `make verify-ai` |
| `docs/` | `VAULT.md`, `NEAR_AI_CLOUD.md`, `FIAT_ONRAMP.md`, `JUDGES.md` (expected outputs), reports |
| `scripts/` | Freeze check, testnet deploy and account scripts |

## Checks to run before committing

```sh
make test            # freeze check + Python tests + guard contract tests
make vault-test      # vault contract unit + sandbox tests
bash scripts/secret_scan.sh   # no keys in the staged diff
```

NEAR AI Cloud: tests never reach the network (`tests/conftest.py`); never add credit or top up the account; the
API key lives only in the gitignored `.env`.
