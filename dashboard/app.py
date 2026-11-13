"""Dashboard. Contract state is read from NEAR testnet on every request; nothing here can sign a NEAR transaction.
The fiat on-ramp adds /paypal (dashboard/paypal_routes.py): PayPal sandbox top-ups of the paper budget and
NEAR AI Cloud (TEE-signed) explanations of guard decisions.

    make dashboard   ->  http://127.0.0.1:8000

Sources, in order of authority:
1. the guard contract (policy, kill switch, stats, positions, decisions and outcomes): truth;
2. the public 1Click status API, for LIVE decisions whose on-chain quote_id carries a deposit address;
3. local agent logs (var/decisions.jsonl) and committed evidence (docs/evidence/journal_txs.json), used only to
   attach transaction links and the observed status timeline to on-chain decisions.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from anchorium.agent import signals as sig
from anchorium.near import live as live_gates
from anchorium.near.guard_client import GuardReader, account_url
from anchorium.near.intents import FUNDS_LOCATION, OneClickClient, OneClickError

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
GUARD = os.environ.get('GUARD_CONTRACT_ID') or 'guard.anchorium-team.testnet'
NETWORK = os.environ.get('NEAR_NETWORK', 'testnet')
INTENTS_EXPLORER = 'https://explorer.near-intents.org/transactions'

app = FastAPI(title='Anchorium dashboard', docs_url=None, redoc_url=None)
reader = GuardReader(GUARD, NETWORK, rpc_url=os.environ.get('NEAR_RPC_URL') or None)
oneclick = OneClickClient(os.environ.get('ONECLICK_BASE_URL') or 'https://1click.chaindefuser.com')
_cache: dict[str, tuple[float, Any]] = {}

from dashboard import paypal_routes  # noqa: E402

paypal_routes.State.reader = reader
app.include_router(paypal_routes.router)


def cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def local_events() -> dict[str, dict[str, Any]]:
    """signal_hash -> merged local events (quote, tx links, timeline). Optional enrichment only."""
    merged: dict[str, dict[str, Any]] = {}
    sources = [ROOT / 'docs/evidence/journal_events.jsonl', ROOT / 'var/decisions.jsonl']
    for path in sources:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            h = e.get('signal_hash')
            if not h:
                continue
            row = merged.setdefault(h, {'timeline': []})
            kind = e['event']
            if kind == 'signal':
                row['signal'] = {k: e.get(k) for k in ('coin', 'signal_type', 'close', 'vwap', 'atr_14', 'bar_open_ms')}
            elif kind == 'quote':
                row['quote'] = {k: e.get(k) for k in ('amount_in', 'in_symbol', 'amount_out', 'out_symbol',
                                                      'min_amount_out', 'worst_case_slippage_bps', 'app_fee_bps',
                                                      'deposit_address', 'dry')}
            elif kind == 'decision_journaled':
                row['decision_tx'] = e.get('explorer')
            elif kind == 'outcome_journaled':
                row['outcome_tx'] = e.get('explorer')
            elif kind == 'deposit_sent':
                row['deposit_tx'] = e.get('explorer')
            elif kind == 'intent_status' and (not row['timeline'] or row['timeline'][-1]['status'] != e['status']):
                row['timeline'].append({'status': e['status'], 'at': e.get('ts'), 'funds_location': e['funds_location']})
    return merged


def deposit_address(decision: dict[str, Any]) -> str | None:
    quote_id = decision.get('quote_id') or ''
    return quote_id.split(':', 1)[1] if decision.get('mode') == 'LIVE' and ':' in quote_id else None


def funds_timeline(decision: dict[str, Any], local: dict[str, Any]) -> list[dict[str, Any]]:
    """Where the funds are, step by step. DRY_RUN decisions never leave the account."""
    if not decision['allowed']:
        return [{'status': 'NOT_SENT', 'funds_location': 'In your account: the trade was rejected, nothing was sent'}]
    if decision['mode'] == 'DRY_RUN':
        return [{'status': 'DRY_RUN', 'funds_location': 'In your account: dry-run quote only, nothing was sent'}]
    steps = list(local.get('timeline') or [])
    address = deposit_address(decision)
    if address and (not steps or steps[-1]['status'] not in ('SUCCESS', 'REFUNDED', 'FAILED', 'INCOMPLETE_DEPOSIT')):
        try:
            update = cached(f'status:{address}', 20, lambda: oneclick.status(address))
            if not steps or steps[-1]['status'] != update.status:
                steps.append({'status': update.status, 'at': update.updated_at, 'funds_location': update.funds_location})
        except OneClickError as exc:
            steps.append({'status': 'UNKNOWN', 'funds_location': f'1Click status unavailable: {exc}'})
    return steps


@app.get('/')
def index():
    return FileResponse(Path(__file__).with_name('static') / 'index.html')


@app.get('/api/overview')
def overview():
    execution = live_gates.ExecutionConfig.from_env(os.environ)
    return {
        'contract': GUARD, 'network': NETWORK, 'contract_url': account_url(NETWORK, GUARD),
        'code_hash': cached('code_hash', 300, reader.code_hash),
        'roles': cached('roles', 60, reader.roles),
        'policy': reader.policy(), 'stats': reader.stats(), 'positions': reader.positions(),
        'banner': execution.banner, 'live': execution.live,
        'agent_keys_url': account_url(NETWORK, cached('roles', 60, reader.roles)['agent']),
    }


@app.get('/api/decisions')
def decisions(limit: int = 25):
    limit = max(1, min(limit, 50))
    local = local_events()
    rows = []
    for d in reversed(reader.latest_decisions(limit)):
        extra = local.get(d['signal_hash'], {})
        address = deposit_address(d)
        rows.append({**d, 'local': {k: v for k, v in extra.items() if k != 'timeline'},
                     'funds_timeline': funds_timeline(d, extra),
                     'intents_explorer': f'{INTENTS_EXPLORER}?search={address}' if address else None})
    return rows


@app.get('/api/signals')
def latest_signals():
    """Current state of the unchanged strategy on public candles (cached 60 s)."""
    def scan():
        feed, now, out = sig.HyperliquidCandles(), int(time.time() * 1000), []
        for coin in ('BTC', 'ETH', 'NEAR'):
            try:
                signal, info = sig.evaluate_latest(feed.fetch(coin, now), coin, now)
                out.append({**info, 'signal': signal.to_dict() if signal else None})
            except Exception as exc:  # noqa: BLE001 - show the feed error instead of failing the page
                out.append({'coin': coin, 'status': f'FEED_ERROR: {type(exc).__name__}'})
        return {'as_of_ms': now, 'coins': out}
    return cached('signals', 60, scan)


@app.get('/api/intent/{address}')
def intent_status(address: str):
    if not address.isalnum() or len(address) > 128:
        raise HTTPException(400, 'invalid deposit address')
    try:
        u = oneclick.status(address)
    except OneClickError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {'status': u.status, 'updated_at': u.updated_at, 'funds_location': u.funds_location,
            'swap_details': u.swap_details, 'all_locations': FUNDS_LOCATION}
