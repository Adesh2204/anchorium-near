"""CLI commands for NEAR AI Cloud evidence. None of them makes a billable call.

    python -m anchorium verify-ai [TARGET] [--full]   offline verifier (no key): a receipt or attestation file, a
                                                      rationale_hash, or a guard decision id. No TARGET: every
                                                      committed fixture under fixtures/near_ai/.
    python -m anchorium near-ai-attest                record a fresh attestation report (free endpoint; needs a key)
    python -m anchorium near-ai-usage                 the local spend ledger
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from anchorium.ai.near_ai import (HOME, Check, NearAIClient, UsageLedger, canonical, nras_check, record_attestation,
                                  sha256_hex, verify_attestation_record, verify_receipt)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'fixtures' / 'near_ai'


def _print(title: str, checks: list[Check]) -> bool:
    print(f'== {title}')
    for c in checks:
        print(f'  {"PASS" if c.ok else "FAIL"}  {c.step:<26} {c.detail}')
    ok = bool(checks) and all(c.ok for c in checks)
    print(f'  => {"PASS" if ok else "FAIL"}\n')
    return ok


def _find_receipt(rationale_hash: str) -> Path | None:
    for base in (FIXTURES / 'receipts', HOME / 'receipts'):
        p = base / f'{rationale_hash}.json'
        if p.exists():
            return p
    return None


def _verify_file(path: Path, full: bool) -> bool:
    data = json.loads(path.read_text())
    if data.get('type') == 'near-ai-attestation':
        checks = verify_attestation_record(data)
        if full:
            att = json.loads(data['raw'])
            for node in att.get('model_attestations') or []:
                checks.append(nras_check(node['nvidia_payload'], data['nonce']))
        return _print(f'attestation {path.name} ({data["model"]}, fetched {data["fetched_at"]})', checks)
    checks = verify_receipt(data)
    checks.append(Check('rationale_hash', True, f'sha256(canonical receipt) = {sha256_hex(canonical(data))}'))
    if full:
        checks.append(nras_check(data['evidence']['model_nvidia_payload'], data['nonce']))
    return _print(f'signed review {path.name} ({data["kind"]} {data["verdict"]} by {data["model"]})', checks)


def _verify_decision(decision_id: int, full: bool) -> bool:
    from dotenv import load_dotenv  # noqa: PLC0415

    from anchorium.near.guard_client import GuardReader  # noqa: PLC0415
    load_dotenv(ROOT / '.env')
    guard = os.environ.get('GUARD_CONTRACT_ID') or 'guard.anchorium-team.testnet'
    rows = GuardReader(guard, os.environ.get('NEAR_NETWORK', 'testnet')).decisions(decision_id, 1)
    if not rows or rows[0].get('id') != decision_id:
        return _print(f'decision #{decision_id} on {guard}', [Check('decision exists', False, 'not found')])
    onchain = rows[0].get('rationale_hash')
    checks = [Check('decision exists', True, f'#{decision_id} on {guard}: {rows[0].get("side")} '
                                             f'{"ALLOWED" if rows[0].get("allowed") else rows[0].get("reason")}'),
              Check('on-chain rationale_hash', bool(onchain), f'{onchain}')]
    path = _find_receipt(onchain) if onchain else None
    checks.append(Check('receipt found', path is not None, str(path.relative_to(ROOT)) if path and
                        path.is_relative_to(ROOT) else str(path)))
    if path:
        data = json.loads(path.read_text())
        checks.append(Check('hash matches chain', sha256_hex(canonical(data)) == onchain,
                            'sha256(canonical receipt) == record_decision.rationale_hash'))
        ok = _print(f'decision #{decision_id}', checks)
        return _verify_file(path, full) and ok
    return _print(f'decision #{decision_id}', checks)


def cmd_verify_ai(args: Any) -> int:
    target, full = args.target, args.full
    if not target:
        files = sorted(FIXTURES.glob('*.json')) + sorted((FIXTURES / 'receipts').glob('*.json'))
        if not files:
            print(f'no fixtures under {FIXTURES.relative_to(ROOT)}')
            return 1
        results = [_verify_file(f, full) for f in files]
        print(f'{sum(results)}/{len(results)} PASS')
        return 0 if all(results) else 1
    if re.fullmatch(r'\d+', target):
        return 0 if _verify_decision(int(target), full) else 1
    if re.fullmatch(r'[0-9a-f]{64}', target):
        path = _find_receipt(target)
        if path is None:
            print(f'no receipt {target} under fixtures/near_ai/receipts or {HOME / "receipts"}')
            return 1
        return 0 if _verify_file(path, full) else 1
    return 0 if _verify_file(Path(target), full) else 1


def cmd_near_ai_attest(args: Any) -> int:
    from dotenv import load_dotenv  # noqa: PLC0415
    load_dotenv(ROOT / '.env')
    if not os.environ.get('NEAR_AI_API_KEY'):
        print('NEAR_AI_API_KEY is not set (put it in .env); attestation reports need a key but are never billed')
        return 1
    client = NearAIClient.from_env(os.environ)
    rec = record_attestation(client)
    slug = re.sub(r'[^A-Za-z0-9]+', '-', client.model).strip('-').lower()
    out = FIXTURES / f'attestation-{slug}-{rec["fetched_at"][:10]}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=1))
    print(f'recorded {out.relative_to(ROOT)} (free endpoint, nothing billed)')
    return 0 if _verify_file(out, False) else 1


def cmd_near_ai_usage(args: Any) -> int:
    ledger = UsageLedger()
    calls = ledger.calls()
    print(f'NEAR AI Cloud spend ledger: {ledger.path}')
    for c in calls:
        print(f'  {c["ts"]}  {c["model"]:<40} {c["prompt_tokens"]:>6} in {c["completion_tokens"]:>5} out  ${c["est_usd"]}')
    print(f'total: {len(calls)} billable call(s), ${ledger.total_usd()} (estimated from the published per-token price)')
    return 0
