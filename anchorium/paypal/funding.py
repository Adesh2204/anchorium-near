"""Shared funding steps for the CLI demo and the dashboard: AI review of a top-up, then the PayPal capture."""
from __future__ import annotations

import secrets
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from anchorium.paypal.client import CUSTOM_ID_PREFIX, Capture, parse_amount
from anchorium.paypal.ledger import FundingLedger

ROOT = Path(__file__).resolve().parents[2]
SANDBOX_LEDGER = ROOT / 'var/paypal_ledger.sqlite'
#: Without PayPal keys the demo simulates the capture locally. It uses its own ledger file so a simulated top-up
#: can never be mixed up with a real sandbox capture, and every line it prints says OFFLINE SIMULATION.
OFFLINE_LEDGER = ROOT / 'var/paypal_ledger_offline.sqlite'
AI_LOG = ROOT / 'var/ai_rationales.jsonl'


def new_custom_id() -> str:
    return f'{CUSTOM_ID_PREFIX}-{secrets.token_hex(6)}'


def topup_context(ledger: FundingLedger, amount_usd: Any, policy: dict[str, Any]) -> dict[str, Any]:
    amount = parse_amount(amount_usd)
    hour_ago = time.time() - 3600
    recent = sum(1 for t in ledger.topups() if t['status'] == 'COMPLETED' and (t['captured_at'] or 0) >= hour_ago)
    return {'amount_usd': f'{amount:.2f}', 'balance_usd': f'{ledger.balance_cents() / 100:.2f}',
            'max_trade_usd': f'{int(policy["max_notional_per_trade_usd_cents"]) / 100:.2f}',
            'daily_loss_cap_usd': f'{int(policy["max_daily_loss_usd_cents"]) / 100:.2f}',
            'max_trades_per_day': policy.get('max_trades_per_day'), 'guard_paused': policy.get('paused'),
            'topups_last_hour': recent, 'currency': 'USD', 'environment': 'PayPal sandbox (no real money)'}


def simulated_capture(amount_usd: Any, custom_id: str) -> Capture:
    """OFFLINE SIMULATION only (no PayPal keys): the shape of a COMPLETED capture, clearly marked as fake."""
    amount = parse_amount(amount_usd)
    tag = secrets.token_hex(4).upper()
    return Capture(order_id=f'OFFLINE{tag}', capture_id=f'OFFLINESIM{tag}', status='COMPLETED',
                   amount_usd=Decimal(amount), custom_id=custom_id, payer_email=None,
                   raw={'simulated': True, 'note': 'no PayPal API call was made'})
