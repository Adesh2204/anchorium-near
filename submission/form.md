# Submission form draft (humans submit; nothing here has been sent)

Submit once, at https://nearhackathon.com/#apply (or the submission API in HACKATHON.md). A mistake after
submitting goes through https://nearhackathon.com/request-changes, never a second submission. If the repo was
already submitted under its old name (`anchorium-near`), use request-changes to point it at the new URL.

| Field | Value |
|---|---|
| projectName | Anchorium |
| leadName | `TODO_HUMAN` (2-80 characters) |
| email | `TODO_HUMAN` (at most 2 submissions per email) |
| size | `2 - 4` |
| tracks | `security`, `defi`, `privacy`, `open` (AI Security & Defense, Unstoppable DeFi, Privacy & Private AI, Open Innovation) |
| nearAddress | `TODO_HUMAN` (the payout account, lowercase, e.g. `yourname.near`) |
| projectUrl | https://github.com/Adesh2204/anchorium-near-Paypal |
| demoUrl | `TODO_HUMAN` (the public X post) |
| idea | below (1979 characters by `len()`; the limit is 20-2000) |
| consent | ticked by the human after reading the contact notice |

Leave the `website` field empty (it is a spam trap).

## idea

Don't trust the bot. Verify it. Anchorium is an autonomous trading agent whose rules, money and AI reviews are checked on NEAR instead of inside the bot.

Problem: you have to trust a trading bot to follow its own risk rules. When it breaks one, nobody sees it, its track record is a screenshot, and the key that can stop it lives in the same software.

How it works: a frozen VWAP/EMA/ATR strategy turns public market data into signals. Before any trade the agent gets a NEAR Intents 1Click quote and journals the decision on a guard contract, which re-checks the owner's policy (assets, $ per trade, entries per day, cooldown, slippage, open positions, daily loss cap). The agent can veto but never override a rejection. The contract computes realized P&L itself and pauses at the loss cap.

New: a Vault contract holds the funds. The agent's only key for it can call execute(decision_id); the vault reads that decision from the guard and pays the 1Click deposit address only if it is ALLOWED, unsettled, not paid before, within the notional or open position and the vault's daily caps. On testnet a rejected decision moved nothing, an allowed $2 decision paid exactly $2 of a test token, a replay and a paused vault were refused (every tx linked in the repo).

New: the AI risk co-pilot runs only on NEAR AI Cloud in a TEE. Each review is signed inside the TEE over the exact request and response; make verify-ai checks the signature, the Intel TDX binding of the key to our nonce and NVIDIA's GPU attestation, offline, with zero keys. Its hash is journaled as rationale_hash.

Verify: make demo (keyless, real quotes), make verify-ai, make vault-demo on your own testnet accounts. 454 Python tests, guard 40+2 and vault 16+1 contract tests, CI.

Honest limits: no mainnet trade yet and no mainnet vault; signed AI reviews wait for NEAR AI credits (attestation recorded); a compromised agent could misdirect one capped allowed decision. Fiat on-ramp: PayPal sandbox, optional.
