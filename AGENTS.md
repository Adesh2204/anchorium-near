# AGENTS.md: Anchorium (NEAR HACKATHON entry)

Instructions for any coding agent (Claude Code, Codex, others) working in, or judging, this repository.

## What this repo is

Anchorium is an autonomous trading agent whose risk policy and decision journal live in a NEAR smart contract.
Trades settle through the NEAR Intents 1Click API. The judged work is `git diff baseline...hackathon-submission`.
`vendor/anchorium_strategy/` is prior non-NEAR work by our team and is not submitted for judging.

## Safety invariants (never weaken these)

1. **DRY_RUN is the default.** Without the LIVE gates, nothing signs or sends a mainnet transaction. Dry runs
   fetch real `dry: true` quotes and never deposit.
2. **No mainnet transaction without the human's explicit "yes" in the current session.** LIVE mode
   additionally requires all of the following:
   - `LIVE=1`
   - `MAX_LIVE_NOTIONAL_USD` (at most 2 USD per trade, 5 USD in total)
   - a confirmation typed in the terminal
   - an ALLOWED verdict from the guard contract
3. **Contracts are deployed to NEAR testnet only.** Mainnet deployment is out of scope.
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
- **Settlement:** only through the 1Click API (`https://1click.chaindefuser.com/v0`), spot, long-only. SHORT
  signals are journaled as rejected because a spot account cannot short.

## Layout

| Path | What |
| --- | --- |
| `contracts/anchorium-guard/` | Rust guard contract (near-sdk), unit tests + sandbox tests |
| `anchorium/near/` | NEAR RPC views, guard client, 1Click client |
| `anchorium/agent/` | Signal adapter, orchestrator, crash-safe checkpoint store |
| `dashboard/` | Read-only dashboard (contract state + intent timeline) |
| `scripts/` | Freeze check, testnet deploy and account scripts |

## Checks to run before committing

```sh
make test            # freeze check + Python tests + contract tests
```
