"""Idempotent funding ledger: PayPal sandbox top-ups in, the agent's paper trades out. Integer USD cents only.

    balance = captured top-ups (COMPLETED captures only) + paper trade entries (BUY = negative, SELL = positive)

Every write is keyed (PayPal order id, or the trade's signal hash), so a replayed webhook, a double click on
"capture" or a re-run of the demo never counts the same money twice.
"""
from __future__ import annotations

import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from anchorium.paypal.client import CUSTOM_ID_PREFIX, Capture, PayPalError

SCHEMA = """
CREATE TABLE IF NOT EXISTS topups (
    order_id TEXT PRIMARY KEY, custom_id TEXT NOT NULL, requested_cents INTEGER NOT NULL, status TEXT NOT NULL,
    capture_id TEXT, captured_cents INTEGER, payer_email TEXT, source TEXT NOT NULL,
    ai_note TEXT, created_at REAL NOT NULL, captured_at REAL);
CREATE TABLE IF NOT EXISTS entries (
    ref TEXT PRIMARY KEY, kind TEXT NOT NULL, cents INTEGER NOT NULL, note TEXT, created_at REAL NOT NULL);
"""


def to_cents(usd: Decimal) -> int:
    cents = usd * 100
    if cents != cents.to_integral_value():
        raise PayPalError(f'amount has sub-cent precision: {usd}')
    return int(cents)


class FundingLedger:
    def __init__(self, path: str | Path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ----- top-ups -----

    def record_order(self, order_id: str, custom_id: str, requested_usd: Decimal, source: str) -> None:
        self.db.execute('INSERT OR IGNORE INTO topups (order_id, custom_id, requested_cents, status, source, '
                        'created_at) VALUES (?, ?, ?, ?, ?, ?)',
                        (order_id, custom_id, to_cents(requested_usd), 'CREATED', source, time.time()))

    def record_capture(self, capture: Capture, source: str = 'paypal-sandbox') -> bool:
        """Credits a COMPLETED capture once. Returns True only the first time it is credited."""
        if not (capture.custom_id or '').startswith(CUSTOM_ID_PREFIX):
            raise PayPalError(f'capture {capture.capture_id} is not an Anchorium top-up (custom_id={capture.custom_id})')
        row = self.db.execute('SELECT status FROM topups WHERE order_id = ?', (capture.order_id,)).fetchone()
        if row and row['status'] == 'COMPLETED':
            return False
        cents = to_cents(capture.amount_usd)
        if row is None:
            self.db.execute('INSERT INTO topups (order_id, custom_id, requested_cents, status, source, created_at) '
                            'VALUES (?, ?, ?, ?, ?, ?)',
                            (capture.order_id, capture.custom_id, cents, 'CREATED', source, time.time()))
        self.db.execute('UPDATE topups SET status = ?, capture_id = ?, captured_cents = ?, payer_email = ?, '
                        'captured_at = ? WHERE order_id = ?',
                        (capture.status, capture.capture_id, cents if capture.status == 'COMPLETED' else 0,
                         capture.payer_email, time.time(), capture.order_id))
        return capture.status == 'COMPLETED'

    def set_ai_note(self, order_id: str, note: str) -> None:
        self.db.execute('UPDATE topups SET ai_note = ? WHERE order_id = ?', (note[:2000], order_id))

    # ----- paper trades -----

    def apply(self, ref: str, kind: str, cents: int, note: str = '') -> bool:
        """Applies one paper trade leg once (BUY spends: negative cents; SELL returns proceeds: positive)."""
        if kind not in ('BUY', 'SELL', 'ADJUST'):
            raise ValueError(kind)
        cur = self.db.execute('INSERT OR IGNORE INTO entries (ref, kind, cents, note, created_at) VALUES (?, ?, ?, ?, ?)',
                              (ref, kind, int(cents), note[:500], time.time()))
        return cur.rowcount == 1

    # ----- reads -----

    def funded_cents(self) -> int:
        return int(self.db.execute("SELECT COALESCE(SUM(captured_cents), 0) FROM topups "
                                   "WHERE status = 'COMPLETED'").fetchone()[0])

    def balance_cents(self) -> int:
        spent = int(self.db.execute('SELECT COALESCE(SUM(cents), 0) FROM entries').fetchone()[0])
        return self.funded_cents() + spent

    def topups(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute('SELECT * FROM topups ORDER BY created_at DESC')]

    def entries(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute('SELECT * FROM entries ORDER BY created_at')]

    def summary(self) -> dict[str, Any]:
        return {'funded_usd': f'{self.funded_cents() / 100:.2f}', 'balance_usd': f'{self.balance_cents() / 100:.2f}',
                'topups': self.topups(), 'entries': self.entries()}
