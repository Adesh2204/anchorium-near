"""Record a REPLAY fixture: complete public Hyperliquid candleSnapshot responses + the bars where the frozen
strategy fired. Read-only public API, no account. Nothing is synthesized: every case is a real recorded bar.

    .venv/bin/python scripts/record_replay_fixture.py fixtures/replay/hyperliquid_1m.json

For each coin it picks the newest LONG entry bar and the first SHORT bar after it (an exit), and verifies by
re-evaluating each prefix exactly as a live agent would (`evaluate_latest`) that the signal appears on that bar.
"""
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from anchorium.agent.signals import (HYPERLIQUID_INFO_URL, LONG_TYPES, SHORT_TYPES, HyperliquidCandles,  # noqa: E402
                                     evaluate_latest)

COINS = ('BTC', 'ETH', 'NEAR')
FETCH_LOOKBACK_MS = 3 * 86_400_000


def find_cases(coin, rows):
    """Scan bar by bar like a live agent; return (entry, exit) cases."""
    closes = sorted(int(r['T']) for r in rows)
    found = []
    for close_ms in closes[-1500:]:  # last ~25 h of bars, each evaluated on its own live window
        now_ms = close_ms + 1
        signal, _ = evaluate_latest(rows, coin, now_ms)
        if signal:
            found.append((now_ms, signal.signal_type))
    entries = [(t, k) for t, k in found if k in LONG_TYPES]
    for entry_t, entry_k in reversed(entries):
        exit_ = next(((t, k) for t, k in found if t > entry_t and k in SHORT_TYPES), None)
        if exit_:
            return [(f'{coin.lower()}-entry', entry_t, entry_k), (f'{coin.lower()}-exit', exit_[0], exit_[1])]
    return []


def main(out_path):
    feed = HyperliquidCandles()
    fetched_at = int(time.time() * 1000)
    responses, cases = {}, []
    for coin in COINS:
        rows = feed.fetch(coin, fetched_at, lookback_ms=FETCH_LOOKBACK_MS)
        raw = json.dumps(rows, separators=(',', ':')).encode()
        responses[coin] = {
            'request': {'url': HYPERLIQUID_INFO_URL, 'body': {'type': 'candleSnapshot', 'req': {
                'coin': coin, 'interval': '1m', 'startTime': fetched_at - FETCH_LOOKBACK_MS, 'endTime': fetched_at}}},
            'sha256': hashlib.sha256(raw).hexdigest(), 'rows': rows}
        for name, now_ms, kind in find_cases(coin, rows):
            cases.append({'name': name, 'coin': coin, 'now_ms': now_ms, 'expected_signal_type': kind,
                          'bar_close_utc': datetime.fromtimestamp((now_ms - 1) / 1000, timezone.utc).isoformat()})
        print(coin, len(rows), 'rows;', [c['name'] + ':' + c['expected_signal_type'] for c in cases if c['coin'] == coin])
    doc = {'description': 'Recorded public Hyperliquid mainnet 1m candleSnapshot responses for REPLAY mode. '
                          'Cases are bars where the unchanged frozen strategy fired when evaluated on the live window.',
           'fetched_at_utc': datetime.fromtimestamp(fetched_at / 1000, timezone.utc).isoformat(),
           'cases': cases, 'responses': responses}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(doc, separators=(',', ':')) + '\n')
    print('wrote', out_path, len(cases), 'cases')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'fixtures/replay/hyperliquid_1m.json')
