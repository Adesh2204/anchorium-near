# 90-second demo video: shot-by-shot script

A screen recording only; no slides. Use a terminal font size of at least 18 pt and a browser zoom of 125% so it
reads on a phone. Every number on screen is real: do not edit or speed up the terminal output except where noted.

Before recording:

```sh
make setup          # once
make dashboard      # in a second terminal; open http://127.0.0.1:8000
```

| Time | Screen | Voice-over (about 150 words in total) |
|---|---|---|
| 0:00–0:08 | README title and "The problem" bullets | "Trading bots break their own risk rules, and nobody sees it. Anchorium moves the rules into a NEAR contract." |
| 0:08–0:18 | Dashboard top: DRY RUN banner, kill switch ACTIVE, the on-chain policy panel | "The owner sets the policy on-chain: two dollars per trade, a one-dollar daily loss cap, max slippage, cooldown. The contract pauses itself at the loss cap." |
| 0:18–0:45 | Terminal: `make demo` (cut the wait while quotes load). Pause on a `[guard]` line, a `[quote]` line and a `[journal]` line | "The frozen strategy fires a signal. The agent asks the contract check_trade, gets a real NEAR Intents 1Click quote, checks worst-case slippage, and prepares the on-chain decision." |
| 0:45–0:58 | Same terminal: the compromised-agent block, `NOTIONAL_ABOVE_MAX` | "Now a compromised agent asks for $4.99 and claims it's allowed. The contract says no." |
| 0:58–1:10 | Dashboard journal: decision #2 with "contract overrode the agent"; click "decision tx" to show it on testnet.nearblocks.io | "That rejection is on-chain, with a verdict_override event. Anyone can check it." |
| 1:10–1:20 | Terminal: the access-key `curl` from README section "How NEAR is used" | "The agent's key can only write to the journal. It cannot change the rules, pause, or move funds." |
| 1:20–1:30 | README "Limitations and roadmap" | "Today the guard is on testnet and swaps settle on mainnet into your own account. Next: a mainnet vault, so the contract itself releases funds. Built at NEAR HACKATHON." |

Optional, if the live micro-trade has run by recording time: replace 1:10–1:20 with the dashboard's LIVE
decision and its mainnet transaction link.
