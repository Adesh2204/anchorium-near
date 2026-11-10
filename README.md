# Anchorium

> **Status: work in progress for NEAR HACKATHON (deadline 2026-11-14).** This README is a skeleton. Sections
> marked *not built yet* describe planned components that do not exist in the repository yet.
> See [PROGRESS.md](PROGRESS.md) for what is done.

Anchorium is an autonomous trading agent whose risk limits live in a NEAR smart contract. The agent asks the
contract before every trade, journals every decision on-chain, and settles spot swaps through the NEAR Intents
1Click API.

## The problem

- Trading bots must be trusted. They can break their own risk rules silently.
- Their track records are screenshots, not verifiable history.
- Kill switches live inside the same software they are meant to stop.

## How NEAR is used *(not built yet)*

- **Guard contract (NEAR testnet):** the owner sets the policy (allowed assets, per-trade notional, trades per
  day, daily loss, slippage, cooldown, pause). The contract re-checks every decision the agent journals, and it
  auto-pauses when the daily loss cap is hit.
- **Agent sub-account with a function-call key:** the agent's key can only call the guard's journaling methods.
- **NEAR Intents 1Click:** quote, slippage gate, deposit and status tracking. Dry-run quotes by default.

## Quickstart *(not built yet)*

Requirements so far: Python >= 3.11, GNU make. Rust and cargo-near are needed only for contract work.

```sh
make setup     # create .venv and install
make freeze    # verify the vendored strategy is byte-identical to its source
make test-py   # Python tests (no network)
```

## Hackathon disclosure

- **Judged work:** `git diff baseline...hackathon-submission`. Everything after the `baseline` tag was built
  during NEAR HACKATHON.
- **Prior work (not judged):** `vendor/anchorium_strategy/` is our team's earlier, non-NEAR VWAP/EMA/ATR
  strategy. It was built for Perpetual Alpha, a separate non-NEAR trading competition, and is copied unchanged
  (see [SOURCE.md](vendor/anchorium_strategy/SOURCE.md)).
- **AI-assisted code:** written with Claude Code.
- **Team:** Adesh, Parth.

## License

[MIT](LICENSE)
