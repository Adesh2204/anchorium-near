# Fiat on-ramp: PayPal sandbox funds the agent, NEAR enforces the rules

**Fiat in, NEAR-enforced out.** A PayPal sandbox checkout funds the agent's paper budget; NEAR AI Cloud reviews the
top-up inside a TEE; the NEAR guard contract still has the final word on every trade. This is an optional feature of
the NEAR entry. It is sandbox-only and no real money moves.

1. **PayPal (Orders API v2, sandbox) funds the agent.** A user tops up the agent's trading budget with PayPal
   Checkout. The server creates and captures the order, and an idempotent ledger credits it once. The agent can only
   trade money that was funded this way: every paper buy is debited from the PayPal budget, and a buy the budget
   can't cover is skipped.
2. **The NEAR AI Cloud co-pilot reviews money before it moves.** It reviews **every top-up before PayPal captures
   it** (protecting the user from over-funding a bot) and **every trade before it is journaled** on NEAR. It can only
   say **no**: an AI "yes" never overrides the contract. See [NEAR_AI_CLOUD.md](NEAR_AI_CLOUD.md) for the TEE
   attestation, the signed receipts and `make verify-ai`.

## Architecture

```
          PayPal JS SDK buttons (dashboard /paypal)   or   make paypal-demo (approve link)
                               |
                               v
  NEAR AI Cloud co-pilot (TEE): review_topup -- VETO -> no order is created, nothing is charged
                               | ALLOW
                               v
  PayPal Orders v2 (sandbox): create -> buyer approves -> server capture (PayPal-Request-Id)
                               |
                               v
  Funding ledger (SQLite, integer cents, keyed by order id / signal hash): credited once
                               |
                               v  budget >= $2 ?
  Signal -> NEAR guard check_trade -> NEAR Intents 1Click dry quote -> NEAR AI Cloud review_trade
                               |                                         | VETO -> agent_allowed=false
                               v                                         v
  record_decision(..., agent_allowed, rationale_hash = sha256(signed receipt)) -> the contract re-checks every rule
                               |
                               v
  paper fill -> debit the PayPal budget (once per signal)
```

| Piece | File |
|---|---|
| PayPal Orders v2 client (OAuth2 client credentials, create / get / capture, sandbox-only, amount validation) | `anchorium/paypal/client.py` |
| Idempotent funding ledger (top-ups in, paper trades out) | `anchorium/paypal/ledger.py` |
| Co-pilot: trade review, top-up review, decision explainer, rules fallback | `anchorium/ai/copilot.py` |
| NEAR AI Cloud client: attestation, signed chat, offline verification, spend guardrails | `anchorium/ai/near_ai.py` |
| Veto-only hook in the orchestrator, `rationale_hash` on-chain | `anchorium/agent/orchestrator.py` |
| CLI demo | `anchorium/paypal_demo.py` (`make paypal-demo`) |
| Dashboard checkout page + API | `dashboard/paypal_routes.py`, `dashboard/static/paypal.html` |
| Tests (PayPal and NEAR AI Cloud faked with `httpx.MockTransport`) | `tests/test_paypal_ai.py`, `tests/test_near_ai.py` |

## PayPal technology used

- **Orders API v2**: `POST /v2/checkout/orders` (intent `CAPTURE`, USD, `custom_id` per top-up),
  `GET /v2/checkout/orders/{id}`, `POST /v2/checkout/orders/{id}/capture` with a `PayPal-Request-Id` so a retried
  capture is idempotent.
- **OAuth 2.0** client-credentials token (`/v1/oauth2/token`), cached until expiry.
- **PayPal JavaScript SDK** Smart Payment Buttons on the dashboard (`createOrder` and `onApprove` call this server;
  the capture always happens server-side).
- **Sandbox only.** The client refuses any base URL other than `https://api-m.sandbox.paypal.com`. Amounts are
  validated server-side ($1.00 to $50.00, two decimals). The ledger credits the amount PayPal reports as captured,
  never the amount the browser sent, and only for orders this server created (`custom_id` prefix check).

## The co-pilot's guardrails

- **Veto-only:** a VETO sets `agent_allowed=false`; an ALLOW changes nothing, because the contract still decides.
- **Hard rules run first and the model can't loosen them:** slippage ≥ 80% of the cap, a buy below VWAP, ATR above
  2% of price, budget too small; for top-ups, a balance above 25 max-size trades or more than 3 top-ups an hour.
- **Labelled fallback:** without `NEAR_AI_API_KEY`, under the spend caps, or when the call or its TEE signature
  fails, the deterministic rules reviewer answers, labelled `rules (<reason>)`. It is never shown as AI output.
- **Verifiable:** with NEAR AI Cloud, the signed receipt's hash goes into the guard's `rationale_hash` field.

## Run it

```sh
git clone https://github.com/Adesh2204/anchorium-near-Paypal && cd anchorium-near-Paypal
make setup

# 1) Zero keys: OFFLINE SIMULATION of the PayPal top-up (labelled) + rules co-pilot + live NEAR testnet guard +
#    real 1Click dry quotes. No billable call anywhere.
make paypal-demo

# 2) Real PayPal sandbox + NEAR AI Cloud: put your keys in .env (see .env.example)
#    PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET  from developer.paypal.com -> Apps & Credentials -> Sandbox
#    NEAR_AI_API_KEY                          from cloud.near.ai (spend capped by NEAR_AI_BUDGET_USD)
make paypal-demo                       # prints a sandbox approve link, waits for approval, captures
make dashboard                         # http://127.0.0.1:8000/paypal  -> PayPal buttons, AI review, ledger

make test-py                           # includes the fiat on-ramp and NEAR AI tests (no network)
```

Approve sandbox payments with a **sandbox personal account** (developer.paypal.com → Testing Tools → Sandbox
Accounts), never a real PayPal login.

## Verified sandbox run (12 Nov 2026)

`make paypal-demo` against the PayPal **sandbox** with the team's sandbox app:
- Sandbox order `0J908874MF194271T`, approved by a sandbox personal account; **capture `4V7136386D273822L`
  COMPLETED, $10.00**, credited once to the ledger.
- 3 replay entries through the live testnet guard and real 1Click dry quotes; the compromised agent (asking $4.99)
  skipped its own checks, the AI included, and was rejected by the contract (`NOTIONAL_ABOVE_MAX`). Budget: $10.00
  funded, $3.98 paper spent, $6.02 left.
- The AI verdicts in that run came from the co-pilot's earlier model provider, which has since been replaced by NEAR
  AI Cloud; the PayPal capture, the ledger and the guard verdicts do not depend on the model.
- These are sandbox and paper figures: no real money moved.

## What is real and what isn't

- **PayPal:** sandbox only, no real money. Without keys, the top-up is an OFFLINE SIMULATION with its own ledger
  file (`var/paypal_ledger_offline.sqlite`), and every screen and log line says so.
- **Trades:** paper / DRY_RUN. They use real NEAR Intents 1Click `dry` quotes and real checks against the testnet
  guard, but nothing is swapped.
- **The PayPal budget is a paper budget.** Sandbox dollars aren't converted into the USDC the agent would trade with.
  Bridging real fiat to the trading account is on the roadmap, not built.
- **The tests fake PayPal and NEAR AI Cloud** with the APIs' response shapes, so `make test-py` needs no network.
