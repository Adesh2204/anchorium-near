"""`python -m anchorium paypal-demo`: fund the agent with PayPal (sandbox), let the AI co-pilot review the top-up
and every trade, then run the replay signals through the NEAR guard exactly like `make demo` (Path A, keyless).

    1. FUND    AI reviews the top-up -> PayPal Orders v2 create -> buyer approves (sandbox) -> server capture
               -> idempotent ledger. No PayPal keys: an OFFLINE SIMULATION with its own ledger, labelled as such.
    2. TRADE   replay signal -> guard check_trade -> 1Click dry quote -> AI review (veto-only)
               -> record_decision args with the AI rationale_hash -> paper fill debited from the PayPal budget.
    3. REPORT  funded, spent, balance, AI verdicts and the rationale hashes.

Nothing here moves real money: PayPal is sandbox-only (enforced in the client) and trades are DRY_RUN quotes.
"""
from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from anchorium.agent import signals as sig
from anchorium.ai.copilot import Copilot
from anchorium.paypal.client import PayPalClient, PayPalError, approve_url, parse_amount
from anchorium.paypal.funding import (AI_LOG, OFFLINE_LEDGER, SANDBOX_LEDGER, new_custom_id, simulated_capture,
                                      topup_context)
from anchorium.paypal.ledger import FundingLedger

ROOT = Path(__file__).resolve().parents[1]


def _fund(args, ledger: FundingLedger, copilot: Copilot, policy: dict, paypal: PayPalClient | None) -> None:
    amount = parse_amount(args.topup)
    if args.order_id:  # an order the buyer already approved (e.g. in the dashboard): capture it, no AI re-review
        if paypal is None:
            raise PayPalError('--order-id needs PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET')
        cap = paypal.capture_order(args.order_id)
        fresh = ledger.record_capture(cap)
        print(f'[paypal]  capture {cap.capture_id} {cap.status} ${cap.amount_usd} '
              f'({"credited" if fresh else "already credited, not counted twice"})')
        return
    review = copilot.review_topup(topup_context(ledger, amount, policy))
    print(f'[ai]      top-up review ({review.provider}): {review.verdict} risk {review.risk_score}/100 '
          f'{review.reason_code}\n          {review.explanation}')
    if review.vetoed:
        print('[paypal]  top-up NOT created: the co-pilot vetoed it (nothing is charged)')
        return
    custom_id = new_custom_id()
    if paypal is None:
        cap = simulated_capture(amount, custom_id)
        ledger.record_order(cap.order_id, custom_id, amount, 'offline-simulation')
        ledger.record_capture(cap, source='offline-simulation')
        ledger.set_ai_note(cap.order_id, review.canonical())
        print(f'[paypal]  OFFLINE SIMULATION (no PayPal keys, no API call): pretend capture of ${amount} '
              f'{cap.capture_id}. Set PAYPAL_CLIENT_ID/SECRET for a real sandbox order.')
        return
    # A return page lets PayPal finish the approval cleanly (no dead end after "Pay"); the CLI then polls and captures.
    back = os.environ.get('PAYPAL_RETURN_URL') or 'https://github.com/Adesh2204/anchorium-near-Paypal/blob/main/docs/PAYPAL_AI.md'
    order = paypal.create_order(amount, custom_id=custom_id, return_url=back)
    ledger.record_order(order['id'], custom_id, amount, 'paypal-sandbox')
    ledger.set_ai_note(order['id'], review.canonical())
    link = approve_url(order)
    print(f'[paypal]  sandbox order {order["id"]} {order["status"]} for ${amount}\n'
          f'          approve it with your SANDBOX personal account: {link}')
    if args.no_wait:
        print(f'          then run: make paypal-demo ARGS="--order-id {order["id"]}"')
        return
    deadline = time.time() + args.wait_seconds
    while time.time() < deadline:
        status = paypal.get_order(order['id'])['status']
        if status == 'APPROVED':
            break
        time.sleep(3)
    else:
        print(f'[paypal]  not approved within {args.wait_seconds} s; capture later with --order-id {order["id"]}')
        return
    cap = paypal.capture_order(order['id'])
    ledger.record_capture(cap)
    print(f'[paypal]  captured {cap.capture_id} {cap.status} ${cap.amount_usd} from {cap.payer_email or "buyer"}')


def cmd_paypal_demo(args) -> int:
    from anchorium.cli import DEFAULT_DEMO_GUARD, build, header, utc  # noqa: PLC0415 - avoid an import cycle

    load_dotenv(ROOT / '.env')
    env = os.environ
    paypal = PayPalClient.from_env(env) if env.get('PAYPAL_CLIENT_ID') and env.get('PAYPAL_CLIENT_SECRET') else None
    ledger = FundingLedger(SANDBOX_LEDGER if paypal else OFFLINE_LEDGER)
    copilot = Copilot.from_env(env, log_path=AI_LOG)

    agent, reader, config, oneclick = build(args, onchain=False, store_path=':memory:',
                                            guard_id=env.get('DEMO_GUARD_CONTRACT_ID') or DEFAULT_DEMO_GUARD)
    if config.execution.live:
        print('paypal-demo runs in DRY_RUN only; unset LIVE')
        return 2
    header(reader, config, oneclick, 'PayPal + AI edition (Path A: no NEAR keys)')
    print(f'PayPal         : {"SANDBOX (api-m.sandbox.paypal.com)" if paypal else "OFFLINE SIMULATION (no keys set)"}'
          f'; ledger {ledger_path(paypal)}')
    print(f'AI co-pilot    : {copilot.provider}'
          f'{"" if copilot.api_key else " (no ANTHROPIC_API_KEY: deterministic rules reviewer, not a model)"}\n')

    policy = reader.policy()
    print('=== 1. FUND the agent with PayPal ===')
    if not args.skip_topup:
        _fund(args, ledger, copilot, policy, paypal)
    print(f'[budget]  funded ${ledger.funded_cents() / 100:.2f}, available ${ledger.balance_cents() / 100:.2f}\n')

    print('=== 2. TRADE: guard + 1Click quote + AI co-pilot (veto-only) ===')
    events: list[dict] = []
    printer = agent.log

    def log(event: dict) -> None:
        events.append(event)
        printer(event)
    agent.log = log
    agent.copilot = copilot
    agent.copilot_context = lambda: {'paypal_budget_usd': f'{ledger.balance_cents() / 100:.2f}'}
    cases = {c.name: c for c in sig.load_replay(Path(args.fixture))}
    names = args.cases.split(',') if args.cases else ['near-entry', 'btc-entry', 'eth-entry']
    order_cents = int(config.order_usd * 100)
    for name in names:
        case = cases[name]
        signal, _ = sig.evaluate_latest(case.rows, case.coin, case.now_ms)
        print(f'--- REPLAY {name}: recorded bar closing {utc(case.now_ms - 1)} ---')
        if signal is None:
            print('[skip]    no signal on this bar\n')
            continue
        if signal.direction == 'LONG' and ledger.balance_cents() < order_cents:
            print(f'[budget]  skip: ${ledger.balance_cents() / 100:.2f} available < ${config.order_usd} order. '
                  'Top up with PayPal first.\n')
            continue
        start = len(events)
        row = agent.process(signal)
        _settle_paper(ledger, row, events[start:])
        print()
    if not args.no_compromised and names:
        case = cases[names[0]]
        signal, _ = sig.evaluate_latest(case.rows, case.coin, case.now_ms)
        assert signal is not None
        print('--- Simulated compromised agent: skips its own checks (the AI included), asks 2.5x the cap ---')
        start = len(events)
        row = agent.process(signal, compromised=True)
        _settle_paper(ledger, row, events[start:])
        guard = next((e for e in events[start:] if e['event'] == 'guard_check'), {})
        print(f'[note]    the AI was bypassed, but the contract re-checks every rule: {guard.get("reason")}; '
              'nothing is debited from the PayPal budget\n')

    print('=== 3. REPORT ===')
    reviews = [e for e in events if e['event'] == 'ai_review']
    print(f'PayPal budget  : funded ${ledger.funded_cents() / 100:.2f}, paper spent '
          f'${(ledger.funded_cents() - ledger.balance_cents()) / 100:.2f}, available ${ledger.balance_cents() / 100:.2f}')
    print(f'AI reviews     : {len(reviews)} ({sum(r["verdict"] == "VETO" for r in reviews)} veto); '
          f'model {copilot.provider}')
    for r in reviews:
        print(f'  {r["verdict"]:5} {r["reason_code"]:22} by {r["provider"]:45} rationale_hash '
              f'{r["rationale_hash"][:16]}...')
    print('  (each rationale_hash goes on-chain as record_decision.rationale_hash)')
    print(f'Full AI rationales (re-hash to verify): {AI_LOG.relative_to(ROOT)}')
    print('Dashboard with PayPal checkout buttons: make dashboard -> http://127.0.0.1:8000/paypal')
    return 0


def ledger_path(paypal) -> str:
    return str((SANDBOX_LEDGER if paypal else OFFLINE_LEDGER).relative_to(ROOT))


def _settle_paper(ledger: FundingLedger, row: dict, events: list[dict]) -> None:
    """Debits a paper BUY from the PayPal budget once (keyed by signal hash) when the guard and the agent both
    allow it. Path A has no agent key, so the guard's check_trade verdict stands in for the journal."""
    data = row.get('data') or {}
    args = data.get('decision_args')
    if not args or not args.get('agent_allowed'):
        return
    guard = next((e for e in events if e['event'] == 'guard_check'), None)
    journaled = data.get('decision')
    allowed = journaled['allowed'] if journaled else bool(guard and guard.get('allowed'))
    if not allowed:
        return
    cents = int(args['notional_usd_cents'])
    if args['side'] == 'BUY' and ledger.apply(row['signal_hash'], 'BUY', -cents, f'{args["asset"]} paper buy'):
        print(f'[budget]  paper BUY ${Decimal(cents) / 100:.2f} debited from the PayPal budget; '
              f'available ${ledger.balance_cents() / 100:.2f}')
