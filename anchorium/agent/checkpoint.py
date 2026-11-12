"""Crash-safe checkpoint store (SQLite, WAL, one row per signal hash).

Every external side effect is preceded by a durable PENDING state:

    NEW -> DECISION_PENDING -> DECISION_RECORDED -> [OUTCOME_PENDING -> DONE]
                                                 -> [LIVE: DEPOSIT_PENDING -> DEPOSITED -> OUTCOME_PENDING -> DONE]

On restart:
* DECISION_PENDING / OUTCOME_PENDING are re-sent: the guard contract is idempotent (same signal hash returns
  the same decision; an identical outcome is a no-op), so nothing is journaled twice.
* DEPOSIT_PENDING becomes NEEDS_REVIEW: a deposit that may or may not have been broadcast is never re-sent
  automatically. A human checks the trader account and the 1Click status first.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATES = ('NEW', 'DECISION_PENDING', 'DECISION_RECORDED', 'DEPOSIT_PENDING', 'DEPOSITED', 'OUTCOME_PENDING',
          'DONE', 'SKIPPED', 'NEEDS_REVIEW')
TERMINAL = ('DONE', 'SKIPPED')

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    signal_hash TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    coin TEXT NOT NULL,
    side TEXT,
    state TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    live_notional_cents INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class CheckpointStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def get(self, signal_hash: str) -> dict[str, Any] | None:
        row = self.db.execute('SELECT signal_hash, mode, coin, side, state, data, live_notional_cents FROM runs '
                              'WHERE signal_hash = ?', (signal_hash,)).fetchone()
        if not row:
            return None
        return {'signal_hash': row[0], 'mode': row[1], 'coin': row[2], 'side': row[3], 'state': row[4],
                'data': json.loads(row[5]), 'live_notional_cents': row[6]}

    def create(self, signal_hash: str, mode: str, coin: str, data: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        self.db.execute('INSERT OR IGNORE INTO runs (signal_hash, mode, coin, state, data, created_at, updated_at) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?)', (signal_hash, mode, coin, 'NEW', json.dumps(data), now, now))
        return self.require(signal_hash)

    def require(self, signal_hash: str) -> dict[str, Any]:
        row = self.get(signal_hash)
        if row is None:
            raise KeyError(signal_hash)
        return row

    def advance(self, signal_hash: str, state: str, *, side: str | None = None,
                live_notional_cents: int | None = None, **data: Any) -> dict[str, Any]:
        """Atomically moves a row to `state`, merging `data`. Durable before the caller's next side effect."""
        if state not in STATES:
            raise ValueError(state)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            current = self.get(signal_hash)
            if current is None:
                raise KeyError(signal_hash)
            merged = {**current['data'], **data}
            self.db.execute(
                'UPDATE runs SET state = ?, side = COALESCE(?, side), data = ?, '
                'live_notional_cents = COALESCE(?, live_notional_cents), updated_at = ? WHERE signal_hash = ?',
                (state, side, json.dumps(merged), live_notional_cents, _now(), signal_hash))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        return self.require(signal_hash)

    def live_spent_cents(self, side: str | None = None) -> int:
        """Session LIVE total: every deposit that may have been sent counts (pending, sent, settled or in review).
        `side='BUY'` counts entries only."""
        sql = ("SELECT COALESCE(SUM(live_notional_cents), 0) FROM runs WHERE mode = 'LIVE' AND "
               "state IN ('DEPOSIT_PENDING', 'DEPOSITED', 'OUTCOME_PENDING', 'DONE', 'NEEDS_REVIEW')")
        row = self.db.execute(sql + ' AND side = ?', (side,)).fetchone() if side else self.db.execute(sql).fetchone()
        return int(row[0])

    def unfinished(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT signal_hash FROM runs WHERE state NOT IN ('DONE', 'SKIPPED', 'NEW') "
                               'ORDER BY created_at').fetchall()
        return [self.require(r[0]) for r in rows]

    def recover(self) -> list[dict[str, Any]]:
        """Call once at startup. Interrupted deposits are parked for a human; returns them."""
        parked = []
        for row in self.unfinished():
            if row['state'] == 'DEPOSIT_PENDING':
                parked.append(self.advance(row['signal_hash'], 'NEEDS_REVIEW',
                                           review_reason='process stopped while a LIVE deposit was being sent; '
                                                         'check the trader account and 1Click status before retrying'))
        return parked
