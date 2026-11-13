# Submission form draft (humans submit; nothing here has been sent)

Submit once, at https://nearhackathon.com/#apply (or the submission API in HACKATHON.md). A mistake after
submitting goes through https://nearhackathon.com/request-changes, never a second submission.

| Field | Value |
|---|---|
| projectName | Anchorium |
| leadName | `TODO_HUMAN` (2-80 characters) |
| email | `TODO_HUMAN` (at most 2 submissions per email) |
| size | `2 - 4` |
| tracks | `defi`, `security`, `agents`, `business`, `open` |
| nearAddress | `TODO_HUMAN` (the payout account, lowercase, e.g. `yourname.near`) |
| projectUrl | https://github.com/Adesh2204/anchorium-near-Paypal |
| demoUrl | `TODO_DEMO_URL` (the public X post) |
| idea | below (1997 characters; the limit is 20-2000) |
| consent | ticked by the human after reading the contact notice |

Leave the `website` field empty (it is a spam trap).

## idea

Don't trust the bot. Verify it. Anchorium is an autonomous trading agent whose risk limits live in a NEAR smart contract instead of inside the bot.

Problem: you have to trust a trading bot to follow its own rules. When it breaks one, nobody sees it, and its track record is a screenshot.

How it works: a frozen VWAP/EMA/ATR strategy turns public market data into signals. Before any trade, the agent calls the guard contract's check_trade, gets a NEAR Intents 1Click quote, and journals the decision with record_decision. The contract re-checks the trade against the owner's policy: allowed assets, max $ per trade, entries per day, cooldown, max slippage, open positions and a daily loss cap. The final verdict is the contract's check AND the agent's verdict, so the agent can veto but never override a rejection (a verdict_override event shows it if it tries). After settlement, record_outcome stores the amounts and the contract computes realized P&L itself; at the daily loss cap it pauses itself. The owner can pause it any time; exits stay possible.

The agent's sub-account holds only a function-call key for record_decision and record_outcome. It cannot change the policy, pause, or transfer NEAR. Swaps settle through 1Click from and into the user's own account.

Built and verifiable now: the Rust guard on testnet (40 unit + 2 sandbox tests), a crash-safe Python orchestrator, dry-run 1Click quotes by default, every 1Click status handled, including refunds, a read-only dashboard, a keyless demo (make demo) and a deploy-your-own demo. The on-chain journal already shows allowed trades, slippage rejections and a simulated compromised agent being overruled.

Honest limits: the guard is on testnet and Intents settle on mainnet, so enforcement today is the agent refusing to deposit without an ALLOWED on-chain decision; every violation is visible in the journal. Next: a mainnet vault contract so the contract itself releases funds only for allowed decisions, then Chain Signatures.
