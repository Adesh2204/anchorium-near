"""`make live-preflight`: PASS/FAIL checks before a LIVE autonomous version A session. Read-only: signs nothing."""
from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Mapping
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from anchorium.autonomous import USDC_CONTRACT, WNEAR_CONTRACT, SessionLock
from anchorium.money import LEG_NEAR_ESTIMATE
from anchorium.near.guard_client import DEFAULT_RPC, GuardReader

MIN_NEAR_FOR_GAS = Decimal('0.05')   # 6 legs x ~0.0015 NEAR + headroom
RECOMMENDED_NEAR = Decimal('0.1')
ORDER_UNITS = 2_000_000


def clock_skew_s(url: str = 'https://1click.chaindefuser.com/v0/tokens') -> float:
    """Local clock minus the server's Date header (1 s resolution), measured at the response midpoint."""
    t0 = time.time()
    r = httpx.head(url, timeout=15)
    t1 = time.time()
    server = parsedate_to_datetime(r.headers['date']).timestamp()
    return (t0 + t1) / 2 - server


def run_preflight(env: Mapping[str, str], lock_path: Path, out=print) -> int:
    from near import Near  # noqa: PLC0415
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f'{type(exc).__name__}: {exc}'
        results.append((name, ok, detail))
        out(f'{"PASS" if ok else "FAIL"}  {name:<34} {detail}')

    account = env.get('TRADER_ACCOUNT_ID') or ''
    guard = GuardReader(env.get('GUARD_CONTRACT_ID') or 'guard.anchorium-team.testnet', 'testnet')
    key_file = Path.home() / '.near-credentials' / 'mainnet' / f'{account}.json'
    mainnet = Near('mainnet', rpc_url=DEFAULT_RPC['mainnet'])

    def key():
        if not account:
            return False, 'TRADER_ACCOUNT_ID is not set in .env'
        if not key_file.exists():
            return False, f'missing {key_file}'
        public = json.loads(key_file.read_text())['public_key']
        keys = {str(k.public_key): k.access_key.permission for k in mainnet.access_keys(account)}
        if public not in keys:
            return False, f'key file {public[:16]}... is not an access key of {account} on mainnet'
        return keys[public] == 'FullAccess', f'{public[:16]}... ({"full access" if keys[public] == "FullAccess" else "not full access: cannot attach the 1 yocto deposit"})'

    def near_balance():
        amount = Decimal(int(mainnet.account(account).amount)) / Decimal(10) ** 24
        return amount >= MIN_NEAR_FOR_GAS, f'{amount} NEAR (need >= {MIN_NEAR_FOR_GAS}; ~{LEG_NEAR_ESTIMATE} per leg)'

    def usdc_balance():
        units = int(mainnet.view(USDC_CONTRACT, 'ft_balance_of', {'account_id': account}))
        return units >= ORDER_UNITS, f'{Decimal(units) / 10 ** 6} USDC (need >= 2.00)'

    def storage():
        missing = [t for t in (USDC_CONTRACT, WNEAR_CONTRACT)
                   if mainnet.view(t, 'storage_balance_of', {'account_id': account}) is None]
        return not missing, 'USDC + wNEAR registered' if not missing else f'not registered on {missing}: run make setup-trader'

    def not_paused():
        paused = guard.policy()['paused']
        return not paused, f'{guard.contract_id} paused={paused}'

    def no_position():
        pos = guard.positions()
        return not pos, 'no open position' if not pos else f'open: {pos}'

    def no_session():
        held = SessionLock.held(lock_path)
        procs = subprocess.run(['pgrep', '-f', 'anchorium (autonomous|paper-trade|live-trade|run)'],
                               capture_output=True, text=True).stdout.split()
        return not held and not procs, 'none running' if not held and not procs else f'lock held={held}, pids={procs}'

    def clock():
        skew = clock_skew_s()
        return abs(skew) < 3, f'local - server = {skew:+.2f} s (limit 3 s)'

    check('key file (trader, full access)', key)
    check('NEAR balance for gas', near_balance)
    check('USDC balance', usdc_balance)
    check('storage registration', storage)
    check('guard not paused', not_paused)
    check('no open position on the guard', no_position)
    check('no other session', no_session)
    check('clock in sync', clock)
    failed = [n for n, ok, _ in results if not ok]
    out(f'\n{"ALL PASS" if not failed else "FAILED: " + ", ".join(failed)}')
    return 0 if not failed else 1
