"""PayPal sandbox funding for the agent's paper budget (the optional fiat on-ramp of the NEAR entry).

* `client.PayPalClient`: Orders API v2 (create, get, capture) over REST. Sandbox only.
* `ledger.FundingLedger`: an idempotent SQLite ledger of captured top-ups and the agent's paper spending.
"""
