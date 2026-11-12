# Anchorium: PayPal AI edition

**Don't trust the bot. Verify it. Now funded with PayPal and double-checked by AI.**

Anchorium is an autonomous trading agent whose risk limits live in a NEAR smart contract instead of inside the
bot. This edition adds two things:

1. **PayPal (Orders API v2, sandbox) funds the agent.** A user tops up the agent's trading budget with PayPal
   Checkout. The server creates and captures the order, and an idempotent ledger credits it once. The agent can
   only trade money that was funded this way: every paper buy is debited from the PayPal budget, and a buy the
   budget can't cover is skipped.
2. **An AI risk co-pilot reviews money before it moves.** A language model reviews **every top-up before PayPal
   captures it** (protecting the user from over-funding a bot) and **every trade before it is journaled** on
   NEAR. It also explains on-chain guard decisions in plain English. The co-pilot can only say **no**. An AI
   "yes" never overrides the contract.

## Architecture

```
          PayPal JS SDK buttons (dashboard /paypal)   or   make paypal-demo (approve link)
                               |
                               v
  AI co-pilot: review_topup ---- VETO -> no order is created, nothing is charged
                               | ALLOW
                               v
  PayPal Orders v2 (sandbox): create -> buyer approves -> server capture (PayPal-Request-Id)
                               |
                               v
  Funding ledger (SQLite, integer cents, keyed by order id / signal hash): credited once
                               |
                               v  budget >= $2 ?
  Signal -> NEAR guard check_trade -> NEAR Intents 1Click dry quote -> AI co-pilot review_trade
                               |                                         | VETO -> agent_allowed=false
                               v                                         v
  record_decision(..., agent_allowed, rationale_hash = sha256(AI review))  -> the contract re-checks every rule
                               |
                               v
  paper fill -> debit the PayPal budget (once per signal)
```

| Piece | File |
|---|---|
| PayPal Orders v2 client (OAuth2 client credentials, create / get / capture, sandbox-only, amount validation) | `anchorium/paypal/client.py` |
| Idempotent funding ledger (top-ups in, paper trades out) | `anchorium/paypal/ledger.py` |
| AI co-pilot: trade review, top-up review, decision explainer, rules fallback | `anchorium/ai/copilot.py` |
| Veto-only hook in the orchestrator, `rationale_hash` on-chain | `anchorium/agent/orchestrator.py` |
| CLI demo | `anchorium/paypal_demo.py` (`make paypal-demo`) |
| Dashboard checkout page + API | `dashboard/paypal_routes.py`, `dashboard/static/paypal.html` |
| Tests (PayPal and Anthropic faked with `httpx.MockTransport`) | `tests/test_paypal_ai.py` |

## PayPal technology used

- **Orders API v2**: `POST /v2/checkout/orders` (intent `CAPTURE`, USD, `custom_id` per top-up),
  `GET /v2/checkout/orders/{id}`, `POST /v2/checkout/orders/{id}/capture` with a `PayPal-Request-Id` so a retried
  capture is idempotent.
- **OAuth 2.0** client-credentials token (`/v1/oauth2/token`), cached until expiry.
- **PayPal JavaScript SDK** Smart Payment Buttons on the dashboard (`createOrder` and `onApprove` call this
  server; the capture always happens server-side).
- **Sandbox only.** The client refuses any base URL other than `https://api-m.sandbox.paypal.com`. Amounts are
  validated server-side ($1.00 to $50.00, two decimals). The ledger credits the amount PayPal reports as captured,
  never the amount the browser sent, and only for orders this server created (`custom_id` prefix check).

## AI used

- **Model:** Anthropic Claude through the Messages API (`ANTHROPIC_API_KEY`; the model is set by `AI_MODEL`,
  default `claude-haiku-5-5`), temperature 0, JSON-only replies. Any OpenAI-compatible chat API also works
  (`AI_BASE_URL` + `AI_API_KEY` + `AI_MODEL`, for example Google Gemini or Groq); the provider used is printed
  on every review.
- **What it does:**
  - `review_trade`: sees the signal (price, VWAP, ATR, EMAs), the 1Click quote (expected vs guaranteed output,
    worst-case slippage, fee), the on-chain policy and the PayPal budget. It answers ALLOW or VETO with a risk
    score and a plain-English reason.
  - `review_topup`: sees the amount, the current budget, the per-trade cap, the daily loss cap and recent
    top-ups. It explains what the money covers and vetoes over-funding.
  - `explain_decision`: turns an on-chain journal entry into 2 or 3 sentences (dashboard "Explain with AI").
- **Guardrails around the model:**
  - It is **veto-only**: a VETO sets `agent_allowed=false`, and an ALLOW changes nothing, because the contract
    still decides.
  - **Hard rules run first and the model can't loosen them**: slippage ≥ 80% of the cap, a buy below VWAP, ATR
    above 2% of price, budget too small; for top-ups, a balance above 25 max-size trades or more than 3 top-ups
    an hour.
  - If the model call or its JSON fails, the deterministic rules reviewer answers, labelled `provider: rules`.
    It is never shown as AI output.
  - **Verifiable:** each review is hashed (sha256 of its canonical JSON) into the guard contract's
    `rationale_hash` field. The full text goes to `var/ai_rationales.jsonl`, so anyone can re-hash it.
- **Development:** the code was written with Claude Code (AI-assisted, disclosed).

## Run it

```sh
git clone https://github.com/Parth-2004/anchorium-near && cd anchorium-near
make setup

# 1) Zero keys (about 1 min): OFFLINE SIMULATION of the PayPal top-up (labelled) + rules co-pilot + live NEAR
#    testnet guard + real 1Click dry quotes
make paypal-demo

# 2) Real PayPal sandbox + real AI: put your keys in .env (see .env.example)
#    PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET  from developer.paypal.com -> Apps & Credentials -> Sandbox
#    ANTHROPIC_API_KEY                        from console.anthropic.com
make paypal-demo                       # prints a sandbox approve link, waits for approval, captures
make dashboard                         # http://127.0.0.1:8000/paypal  -> PayPal buttons, AI review, ledger

make test-py                           # includes tests/test_paypal_ai.py (no network)
```

Approve sandbox payments with a **sandbox personal account** (developer.paypal.com → Testing Tools → Sandbox
Accounts), never a real PayPal login.

## What is real and what isn't

- **PayPal:** sandbox only, no real money. Without keys, the top-up is an OFFLINE SIMULATION with its own ledger
  file (`var/paypal_ledger_offline.sqlite`), and every screen and log line says so.
- **Trades:** paper / DRY_RUN. They use real NEAR Intents 1Click `dry` quotes and real checks against the
  testnet guard, but nothing is swapped.
- **The PayPal budget is a paper budget.** Sandbox dollars aren't converted into the USDC the agent would trade
  with. Bridging real PayPal funds (for example PayPal USD) to the trading account is on the roadmap, not built.
- **The tests fake PayPal and Anthropic** with the APIs' response shapes (`httpx.MockTransport`), so
  `make test-py` needs no network. The end-to-end sandbox run needs your own sandbox app and model keys.
