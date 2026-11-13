"""Fiat on-ramp routes for the dashboard: PayPal sandbox checkout to fund the agent's paper budget, a NEAR AI
Cloud (TEE-signed) review of every top-up before capture, and plain-English explanations of guard decisions.

    GET  /paypal                           checkout page (PayPal JS SDK buttons, sandbox client id)
    GET  /api/paypal/config                sandbox / offline mode, client id, AI provider, ledger
    POST /api/paypal/orders                {"amount": "10.00"} -> AI top-up review -> Orders v2 create
    POST /api/paypal/orders/{id}/capture   server-side capture of an order this server created -> ledger credit
    POST /api/paypal/simulate              OFFLINE SIMULATION only (no keys): a labelled local top-up
    GET  /api/ai/explain/{decision_id}     AI explanation of one on-chain journal decision

The NEAR side stays read-only: nothing here can sign a NEAR transaction. PayPal is sandbox-only (the client
refuses any other base URL), so no real money moves.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from anchorium.ai.copilot import Copilot
from anchorium.paypal.client import MAX_TOPUP_USD, MIN_TOPUP_USD, PayPalClient, PayPalError, parse_amount
from anchorium.paypal.funding import (AI_LOG, OFFLINE_LEDGER, SANDBOX_LEDGER, new_custom_id, simulated_capture,
                                      topup_context)
from anchorium.paypal.ledger import FundingLedger

router = APIRouter()


class State:
    """Lazily built so tests can swap in fakes (`State.paypal = ...`)."""
    paypal: PayPalClient | None = None
    ledger: FundingLedger | None = None
    copilot: Copilot | None = None
    reader: Any = None
    explained: dict[int, dict[str, Any]] = {}
    ready = False

    @classmethod
    def init(cls) -> None:
        if cls.ready:
            return
        env = os.environ
        if env.get('PAYPAL_CLIENT_ID') and env.get('PAYPAL_CLIENT_SECRET'):
            cls.paypal = PayPalClient.from_env(env)
        cls.ledger = FundingLedger(SANDBOX_LEDGER if cls.paypal else OFFLINE_LEDGER)
        cls.copilot = Copilot.from_env(env, log_path=AI_LOG)
        cls.ready = True


def _ledger_view() -> dict[str, Any]:
    assert State.ledger is not None
    s = State.ledger.summary()
    s['topups'] = [{k: t[k] for k in ('order_id', 'status', 'capture_id', 'captured_cents', 'requested_cents',
                                      'source', 'ai_note', 'created_at')} for t in s['topups'][:20]]
    return s


@router.get('/paypal')
def paypal_page():
    return FileResponse(Path(__file__).with_name('static') / 'paypal.html')


@router.get('/api/paypal/config')
def paypal_config():
    State.init()
    assert State.copilot is not None
    return {'mode': 'sandbox' if State.paypal else 'offline',
            'client_id': State.paypal.client_id if State.paypal else None,
            'min_usd': str(MIN_TOPUP_USD), 'max_usd': str(MAX_TOPUP_USD),
            'ai_provider': State.copilot.provider, 'ledger': _ledger_view()}


def _review(amount: Any) -> Any:
    assert State.copilot is not None and State.ledger is not None
    try:
        ctx = topup_context(State.ledger, amount, State.reader.policy())
    except PayPalError as exc:
        raise HTTPException(400, str(exc)) from exc
    return State.copilot.review_topup(ctx)


@router.post('/api/paypal/orders')
def create_order(payload: dict[str, Any] = Body(...)):
    State.init()
    if State.paypal is None:
        raise HTTPException(400, 'PayPal sandbox keys are not set: use /api/paypal/simulate or set PAYPAL_CLIENT_ID')
    assert State.ledger is not None
    review = _review(payload.get('amount'))
    if review.vetoed:
        raise HTTPException(409, {'message': 'AI co-pilot vetoed this top-up; no order was created',
                                  'review': review.to_dict()})
    amount, custom_id = parse_amount(payload.get('amount')), new_custom_id()
    try:
        order = State.paypal.create_order(amount, custom_id=custom_id)
    except PayPalError as exc:
        raise HTTPException(502, str(exc)) from exc
    State.ledger.record_order(order['id'], custom_id, amount, 'paypal-sandbox')
    State.ledger.set_ai_note(order['id'], review.canonical())
    return {'id': order['id'], 'status': order['status'], 'review': review.to_dict()}


@router.post('/api/paypal/orders/{order_id}/capture')
def capture_order(order_id: str):
    State.init()
    if State.paypal is None:
        raise HTTPException(400, 'PayPal sandbox keys are not set')
    assert State.ledger is not None
    if not any(t['order_id'] == order_id for t in State.ledger.topups()):
        raise HTTPException(404, 'unknown order: only orders created by this server can be captured')
    try:
        cap = State.paypal.capture_order(order_id)
        credited = State.ledger.record_capture(cap)
    except PayPalError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {'order_id': cap.order_id, 'capture_id': cap.capture_id, 'status': cap.status,
            'amount_usd': str(cap.amount_usd), 'credited': credited, 'ledger': _ledger_view()}


@router.post('/api/paypal/simulate')
def simulate_topup(payload: dict[str, Any] = Body(...)):
    State.init()
    if State.paypal is not None:
        raise HTTPException(400, 'sandbox keys are set: use the PayPal buttons, not the simulation')
    assert State.ledger is not None
    review = _review(payload.get('amount'))
    if review.vetoed:
        raise HTTPException(409, {'message': 'AI co-pilot vetoed this top-up', 'review': review.to_dict()})
    cap = simulated_capture(payload.get('amount'), new_custom_id())
    State.ledger.record_order(cap.order_id, cap.custom_id or '', cap.amount_usd, 'offline-simulation')
    State.ledger.record_capture(cap, source='offline-simulation')
    State.ledger.set_ai_note(cap.order_id, review.canonical())
    return {'simulated': True, 'order_id': cap.order_id, 'review': review.to_dict(), 'ledger': _ledger_view()}


@router.get('/api/ai/explain/{decision_id}')
def explain(decision_id: int):
    State.init()
    assert State.copilot is not None
    if decision_id in State.explained:
        return State.explained[decision_id]
    rows = State.reader.decisions(decision_id, 1)
    if not rows:
        raise HTTPException(404, f'no decision #{decision_id} on {State.reader.contract_id}')
    result = {'decision_id': decision_id, **State.copilot.explain_decision(rows[0])}
    State.explained[decision_id] = result
    return result
