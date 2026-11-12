"""Version A: the fully autonomous long-only NEAR round trader (owner's FINAL TASK). DRY_RUN by default.

Entry: a LONG signal from the unchanged frozen strategy on NEAR (Hyperliquid 1m, latest completed bar), then
    1. caps: $2 per trade, 1 open round, 3 rounds per session, INR 20 session loss cap, no buy after the buy cut-off,
       60 min cooldown after a STOP_LOSS, guard not paused;
    2. cost filter: a live entry quote and a live exit quote for $2. Round-trip cost = (USDC in - worst-case USDC
       back + both legs' NEAR cost) / USDC in. Above the threshold (1.6%, owner's choice 2026-11-12) -> SKIPPED_COST;
    3. the orchestrator: check_trade -> fresh quote -> slippage gate -> record_decision -> fill (paper: the quote's
       guaranteed minAmountOut; LIVE: deposit + 1Click status) -> record_outcome.
Exit, every 60 s on a fresh dry sell quote's guaranteed output (see `anchorium.money`): PROFIT_EXIT, else STOP_LOSS,
else TIME_EXIT. The sell itself runs through the orchestrator on another fresh quote: a PROFIT_EXIT re-checks the
rule there (`min_proceeds_units`), STOP_LOSS and TIME_EXIT sell on it anyway. A guard rejection of a STOP_LOSS /
TIME_EXIT sell is retried every tick for 30 min, each attempt journaled; then the bot notifies the owner and waits
(`--resume-exit` opens another window). Nothing ever bypasses the guard.

Crash safety: the session state (JSON, atomic + fsync) records each round's leg BEFORE the leg starts; the
orchestrator's checkpoint store and the contract are idempotent per signal hash; so re-running the same command
resumes the leg instead of repeating it, and an interrupted LIVE deposit is parked as NEEDS_REVIEW (hard stop),
never re-sent.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal as signal_mod
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from near import errors as near_errors

from anchorium import money as M
from anchorium.agent.orchestrator import DRY_RUN_PLACEHOLDER_RECIPIENT, USDC
from anchorium.agent.signals import Signal
from anchorium.near.guard_client import tx_url
from anchorium.near.intents import OneClickError, QuoteRequest

IST = timezone(timedelta(hours=5, minutes=30))
WNEAR = 'nep141:wrap.near'
WNEAR_CONTRACT = 'wrap.near'
USDC_CONTRACT = USDC.removeprefix('nep141:')
HL_INFO = 'https://api.hyperliquid.xyz/info'
BACKOFF_S = (5, 15, 60, 60)  # between 5 tries
NETWORK_PAUSE_S = 300
STATE_VERSION = 1
SIGNAL_MAX_AGE_MS = 180_000  # a buy acts on a signal bar at most 3 min old
DECISIONS_RESERVED_FOR_TRADES = 50  # on-chain skip journaling stops when the guard's daily budget runs this low


def ist(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, IST).strftime('%Y-%m-%d %H:%M:%S IST')


def ist_ms(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp() * 1000)


class HardStop(RuntimeError):
    """No retry. The owner is notified with the reason."""


class NetworkDown(RuntimeError):
    """5 tries failed: pause 5 min, then resume."""


def is_network_error(exc: BaseException) -> bool:
    if isinstance(exc, OneClickError):
        return exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500
    return isinstance(exc, (httpx.HTTPError, ConnectionError, TimeoutError, near_errors.RpcError,
                            near_errors.InvalidNonceError, near_errors.TransactionExpiredError))


@dataclass
class AutoConfig:
    session: str
    live: bool = False
    start_inr: Decimal = Decimal('500')
    order_usd: Decimal = Decimal('2.00')
    max_rounds: int = 3
    loss_cap_inr: Decimal = Decimal('20')
    profit_target_inr: Decimal = Decimal('1.00')
    cost_filter: Decimal = Decimal('0.016')
    no_buy_after_ms: int = ist_ms('2026-11-13T16:00:00+05:30')
    time_exit_ms: int = ist_ms('2026-11-13T20:00:00+05:30')
    stop_cooldown_s: int = 3600
    tick_s: int = 60
    exit_retry_window_s: int = 1800
    heartbeat_s: int = 7200
    fx_refresh_s: int = 1800
    skip_journal_gap_s: int = 900  # at most one on-chain SKIPPED_COST per 15 min (100 decisions/day guard cap)
    run_until_ms: int | None = None  # paper: stop the session here (an open round is reported open)
    stop_after_closed: int | None = None  # paper: stop after this many closed rounds
    report_path: str = 'docs/paper_trade_A_report.md'

    def __post_init__(self):
        if self.order_usd > Decimal('2.00') or self.order_usd <= 0:
            raise ValueError('order_usd must be > 0 and <= 2.00')
        if not 1 <= self.max_rounds <= 3:
            raise ValueError('max_rounds must be 1..3')
        if self.profit_target_inr <= 0:
            raise ValueError('PROFIT_TARGET_INR must be positive')

    def to_dict(self) -> dict[str, Any]:
        return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(self).items()}


class StateFile:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any] | None:
        return json.loads(self.path.read_text()) if self.path.exists() else None

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        with tmp.open('w') as f:
            f.write(json.dumps(state, indent=1, default=str))
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.path)


class SessionLock:
    """One session at a time per state directory (and visible to `live-preflight`)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HardStop(f'ANOTHER_SESSION_RUNNING (lock {self.path})') from exc
        os.ftruncate(self.fd, 0)
        os.write(self.fd, str(os.getpid()).encode())

    def release(self) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    @staticmethod
    def held(path: Path) -> bool:
        if not Path(path).exists():
            return False
        fd = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
        finally:
            os.close(fd)


# ----- market inputs (injectable) -----

@dataclass
class Market:
    """Hyperliquid public reads + the frozen-strategy signal path. No signer anywhere."""
    feed: Any = None
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=20))

    def __post_init__(self):
        if self.feed is None:
            from anchorium.agent.signals import HyperliquidCandles  # noqa: PLC0415
            self.feed = HyperliquidCandles()

    def mid(self) -> Decimal:
        mids = self.http.post(HL_INFO, json={'type': 'allMids'}).raise_for_status().json()
        return Decimal(str(mids['NEAR']))

    def entry_signal(self, now_ms: int) -> Signal | None:
        from anchorium.agent import signals as sig  # noqa: PLC0415
        found, _ = sig.evaluate_latest(self.feed.fetch('NEAR', now_ms), 'NEAR', now_ms)
        return found

    def exit_signal(self, kind: str, attempt: int, round_n: int, now_ms: int) -> Signal:
        """A non-strategy exit at the latest completed bar, journaled with its own type and source."""
        from anchorium.agent import signals as sig  # noqa: PLC0415
        return sig.latest_bar_signal(self.feed, 'NEAR', kind, source=f'autonomous-a:{kind.lower()}:r{round_n}#{attempt}',
                                     now_ms=now_ms)


class Wallet:
    """Balances. Paper: simulated in the state. LIVE: read from the mainnet trader account."""

    def __init__(self, live: bool, trader_near=None, account: str | None = None):
        self.live, self.near, self.account = live, trader_near, account

    def usdc_units(self, state: dict[str, Any]) -> int:
        if not self.live:
            return int(state['paper_usdc_units'])
        return int(self.near.ft_balance(USDC_CONTRACT, self.account))

    def wnear_units(self, state: dict[str, Any]) -> int:
        if not self.live:
            return sum(int(r['buy']['wnear_units']) for r in state['rounds'] if r['status'] in ('OPEN', 'SELLING'))
        return int(self.near.ft_balance(WNEAR_CONTRACT, self.account))

    def near_yocto(self) -> int | None:
        if not self.live:
            return None
        return int(self.near.account(self.account).amount)


# ----- the runner -----

class Runner:
    def __init__(self, cfg: AutoConfig, agent, market: Market, wallet: Wallet, statefile: StateFile, *,
                 fetch_fx: Callable[[], dict[str, Any]], notify: Callable[[str, str], None],
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 print_: Callable[[str], None] = print, root: Path | None = None):
        self.cfg, self.agent, self.market, self.wallet, self.sf = cfg, agent, market, wallet, statefile
        self.fetch_fx, self._notify, self.clock, self.sleep, self.print = fetch_fx, notify, clock, sleep, print_
        self.root = root or Path(__file__).resolve().parents[1]
        self.state: dict[str, Any] = {}
        self.stop_requested = False

    # --- helpers ---

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def save(self) -> None:
        self.sf.save(self.state)

    def event(self, kind: str, text: str, notify: bool = False, **data: Any) -> None:
        e = {'at': ist(self.now_ms()), 'event': kind, 'text': text, **data}
        self.state.setdefault('events', []).append(e)
        self.state['events'] = self.state['events'][-200:]
        self.print(f'[{e["at"]}] {kind}: {text}')
        if notify:
            self._notify(f'Anchorium {"LIVE" if self.cfg.live else "PAPER"}: {kind}', text)
        self.save()

    def net(self, fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
        """Network call with backoff 5 s, 15 s, 60 s, 60 s (5 tries), then NetworkDown."""
        for i in range(len(BACKOFF_S) + 1):
            try:
                return fn(*args, **kw)
            except Exception as exc:  # noqa: BLE001
                if not is_network_error(exc):
                    raise
                if i == len(BACKOFF_S):
                    raise NetworkDown(f'{type(exc).__name__}: {exc}') from exc
                self.print(f'  network error ({type(exc).__name__}: {str(exc)[:80]}); retry in {BACKOFF_S[i]} s')
                self.sleep(BACKOFF_S[i])
        raise AssertionError('unreachable')

    def leg(self, sig: Signal, min_proceeds_units: int | None = None) -> dict[str, Any]:
        """Runs one leg through the orchestrator. A network error is retried (the orchestrator resumes from its
        checkpoint, idempotently), EXCEPT when a LIVE deposit may already have been sent: that row is parked as
        NEEDS_REVIEW and the bot hard-stops. A deposit is never re-sent automatically."""
        for i in range(len(BACKOFF_S) + 1):
            try:
                return self.agent.process(sig, min_proceeds_units=min_proceeds_units)
            except Exception as exc:  # noqa: BLE001
                if not is_network_error(exc):
                    raise
                parked = self.agent.store.recover()
                if parked:
                    raise HardStop(f'DEPOSIT_NEEDS_REVIEW: network error while a deposit was being sent '
                                   f'({type(exc).__name__}: {exc}); check the trader account and 1Click status') from exc
                if i == len(BACKOFF_S):
                    raise NetworkDown(f'{type(exc).__name__}: {exc}') from exc
                self.print(f'  network error in a leg ({type(exc).__name__}); resuming it in {BACKOFF_S[i]} s')
                self.sleep(BACKOFF_S[i])
        raise AssertionError('unreachable')

    def fx(self) -> dict[str, Any]:
        cur = self.state.get('fx')
        if cur is None or self.now_ms() - cur['_ms'] >= self.cfg.fx_refresh_s * 1000:
            try:
                fresh = self.net(self.fetch_fx)
                self.state['fx'] = {**fresh, '_ms': self.now_ms()}
                self.save()
            except NetworkDown:
                if cur is None:
                    raise
                self.print('  FX refresh failed; keeping the last rate')
        return self.state['fx']

    def rate(self) -> Decimal:
        return Decimal(self.fx()['usd_inr'])

    def current_round(self) -> dict[str, Any] | None:
        return next((r for r in self.state['rounds'] if r['status'] in ('BUYING', 'OPEN', 'SELLING')), None)

    def closed_rounds(self) -> list[dict[str, Any]]:
        return [r for r in self.state['rounds'] if r['status'] == 'CLOSED']

    def session_after_tax_inr(self) -> Decimal:
        return sum((Decimal(r['after_tax_inr']) for r in self.closed_rounds()), Decimal(0))

    def leg_gas_estimate(self) -> Decimal:
        """NEAR per deposit leg: the measured cost of the last LIVE leg if any, else the documented estimate."""
        measured = [Decimal(r[leg]['gas_near']) for r in self.state['rounds'] for leg in ('buy', 'sell')
                    if r.get(leg) and r[leg].get('gas_measured')]
        return measured[-1] if measured else M.LEG_NEAR_ESTIMATE

    # --- startup ---

    def start(self) -> None:
        parked = self.agent.store.recover()
        loaded = self.sf.load()
        if loaded is None:
            fx = self.net(self.fetch_fx)
            rate = Decimal(fx['usd_inr'])
            paper_usdc = M.to_units(self.cfg.start_inr / rate, M.USDC_DECIMALS)
            self.state = {'version': STATE_VERSION, 'session': self.cfg.session, 'mode': 'LIVE' if self.cfg.live
                          else 'DRY_RUN', 'started_ms': self.now_ms(), 'started_at': ist(self.now_ms()),
                          'config': self.cfg.to_dict(), 'fx_start': fx, 'fx': {**fx, '_ms': self.now_ms()},
                          'paper_usdc_units': paper_usdc, 'rounds': [], 'events': [], 'last_bar_ms': 0,
                          'cooldown_until_ms': 0, 'last_skip_journal_ms': 0, 'last_skip_notify_ms': 0,
                          'last_heartbeat_ms': self.now_ms(), 'hard_stop': None, 'loss_cap_hit': False,
                          'finished': None}
            if self.cfg.live:
                self.state['start_usdc_units'] = self.net(self.wallet.usdc_units, self.state)
                self.state['start_near_yocto'] = self.net(self.wallet.near_yocto)
            else:
                self.state['start_usdc_units'] = paper_usdc
            start_usd = M.usdc(self.state['start_usdc_units'])
            self.state['start_inr_at_start_fx'] = str(start_usd * rate)
            self.save()
            self.event('START', f'{self.state["mode"]} session {self.cfg.session}: {start_usd:.6f} USDC = INR '
                                f'{start_usd * rate:.2f} at {rate} INR/USD ({fx["source"]})')
        else:
            self.state = loaded
            if self.state.get('session') != self.cfg.session:
                raise HardStop('STATE_SESSION_MISMATCH')
            self.event('RESUME', f'resumed from {self.sf.path.name}; rounds so far {len(self.state["rounds"])}')
        if parked:
            raise HardStop(f'DEPOSIT_NEEDS_REVIEW: {len(parked)} deposit(s) interrupted mid-send; check the trader '
                           f'account and 1Click status before running again')
        for row in self.agent.store.db.execute("SELECT signal_hash FROM runs WHERE state = 'NEEDS_REVIEW'"):
            raise HardStop(f'NEEDS_REVIEW row {row[0][:12]}...: resolve it before running again')

    # --- main loop ---

    def install_sigint(self) -> None:
        def handler(signum, frame):
            if self.stop_requested:
                pending = self.agent.store.db.execute(
                    "SELECT COUNT(*) FROM runs WHERE state = 'DEPOSIT_PENDING'").fetchone()[0]
                if pending:
                    self.print('\nA deposit is being sent: not interrupting it. The bot exits right after.')
                    return
                raise KeyboardInterrupt
            self.stop_requested = True
            self.print('\nCtrl+C: finishing the current step, then saving and exiting '
                       '(press again to force; state is checkpointed either way)')
        signal_mod.signal(signal_mod.SIGINT, handler)

    def run(self) -> str:
        """Runs until a stop condition. Returns the reason."""
        try:
            self.start()
        except (HardStop, NetworkDown) as exc:
            return self.hard_stop(f'{"" if isinstance(exc, HardStop) else "STARTUP_NETWORK_DOWN: "}{exc}')
        except Exception as exc:  # noqa: BLE001
            return self.hard_stop(f'UNEXPECTED_ERROR at startup: {type(exc).__name__}: {exc}')
        if self.state.get('hard_stop'):
            return self.finish(f'HARD_STOP already recorded: {self.state["hard_stop"]}')
        if self.loss_cap_reached():
            self.state['loss_cap_hit'] = True
            return self.finish('SESSION_LOSS_CAP already hit')
        while True:
            if self.stop_requested:
                return self.finish('CTRL_C')
            reason = self.stop_reason()
            if reason:
                return self.finish(reason)
            started = self.clock()
            try:
                self.tick()
            except HardStop as exc:
                return self.hard_stop(str(exc))
            except NetworkDown as exc:
                self.event('NETWORK_PAUSE', f'{exc}; pausing {NETWORK_PAUSE_S // 60} min, then resuming')
                self.wait(NETWORK_PAUSE_S)
                continue
            except Exception as exc:  # noqa: BLE001  (never die silently with a round open)
                return self.hard_stop(f'UNEXPECTED_ERROR: {type(exc).__name__}: {exc}')
            if self.state.get('loss_cap_hit'):
                return self.finish('SESSION_LOSS_CAP')
            reason = self.stop_reason()
            if reason:
                return self.finish(reason)
            self.heartbeat()
            self.wait(max(1.0, self.cfg.tick_s - (self.clock() - started)))  # a tick every 60 s, not 60 s + work

    def wait(self, seconds: float) -> None:
        end = self.clock() + seconds
        while not self.stop_requested and self.clock() < end:
            self.sleep(min(1.0, max(0.0, end - self.clock())))

    def stop_reason(self) -> str | None:
        if self.cfg.stop_after_closed is not None and len(self.closed_rounds()) >= self.cfg.stop_after_closed \
                and self.current_round() is None:
            return f'{len(self.closed_rounds())} ROUND(S) CLOSED'
        if self.cfg.run_until_ms is not None and self.now_ms() >= self.cfg.run_until_ms:
            return f'RUN_UNTIL {ist(self.cfg.run_until_ms)}'
        if self.current_round() is None and self.now_ms() >= self.cfg.time_exit_ms:
            return 'TIME_EXIT CUT-OFF PASSED (flat)'
        return None

    def heartbeat(self) -> None:
        if self.now_ms() - self.state['last_heartbeat_ms'] >= self.cfg.heartbeat_s * 1000:
            self.state['last_heartbeat_ms'] = self.now_ms()
            r = self.current_round()
            text = (f'alive; {len(self.closed_rounds())} closed, session after tax INR '
                    f'{self.session_after_tax_inr():+.2f}; ' + (f'round #{r["n"]} {r["status"]}' if r else 'flat'))
            self.event('HEARTBEAT', text, notify=True)

    def hard_stop(self, reason: str) -> str:
        if self.state:
            self.state['hard_stop'] = reason
            self.save()
            self.event('HARD_STOP', reason, notify=True)
            self.write_report()
        else:
            self.print(f'HARD_STOP: {reason}')
            self._notify('Anchorium: HARD STOP', reason)
        return f'HARD_STOP: {reason}'

    def finish(self, reason: str) -> str:
        self.state['finished'] = {'at': ist(self.now_ms()), 'reason': reason}
        r = self.current_round()
        if r and r['status'] == 'OPEN' and not self.stop_requested:
            try:
                self.status_line(r)  # value the open round one last time for the report
            except Exception as exc:  # noqa: BLE001  (reporting only; the stop reason stands)
                self.print(f'  could not value the open round: {type(exc).__name__}: {exc}')
        self.save()
        self.event('STOP', reason, notify=reason != 'CTRL_C')
        self.write_report()
        return reason

    # --- one tick ---

    def tick(self) -> None:
        self.fx()
        r = self.current_round()
        if r is None:
            self.maybe_enter()
        elif r['status'] == 'BUYING':
            row = self.agent.store.get(self.agent._hash(Signal(**r['signal'])))
            if row is None or row['state'] == 'NEW':  # crashed before the leg started: re-check every entry gate
                block = self.entry_block(self.now_ms(), Signal(**r['signal']))
                if block:
                    r['status'], r['abort_reason'] = 'ABORTED', f'RESUME_{block}'
                    self.save()
                    self.event('BUY_NOT_ALLOWED', f'round #{r["n"]}: not resumed ({block})')
                    return
            self.book_buy(r, self.leg(Signal(**r['signal'])))
        elif r['status'] == 'SELLING':
            p = r['pending_exit']
            self.book_sell(r, self.leg(Signal(**p['signal']), min_proceeds_units=p['floor_units']))
        else:
            self.manage_exit(r)

    # --- entry ---

    def local_skip(self, kind: str, text: str) -> None:
        self.event(kind, text)

    def maybe_enter(self) -> None:
        now = self.now_ms()
        sig = self.net(self.market.entry_signal, now)
        if sig is None or sig.bar_open_ms <= self.state['last_bar_ms']:
            return
        self.state['last_bar_ms'] = sig.bar_open_ms
        self.save()
        if sig.direction != 'LONG':
            return  # long-only: a SHORT signal without a position is nothing to do
        label = f'{sig.signal_type} bar {ist(sig.bar_open_ms)} close {sig.close}'
        block = self.entry_block(now, sig)
        if block:
            return self.local_skip(f'SKIPPED_{block}', f'{label}: {self.block_text(block)}')
        if self.net(self.agent.reader.policy)['paused']:
            raise HardStop('GUARD_PAUSED: the owner paused the guard contract (new buys blocked)')
        order_units = M.to_units(self.cfg.order_usd, M.USDC_DECIMALS)
        if self.net(self.wallet.usdc_units, self.state) < order_units:
            return self.local_skip('SKIPPED_BALANCE', f'{label}: USDC balance below ${self.cfg.order_usd}')
        if self.cfg.live:
            near = self.net(self.wallet.near_yocto) or 0
            if Decimal(near) / Decimal(10) ** 24 < 3 * self.leg_gas_estimate():
                raise HardStop(f'NEAR_GAS_LOW: {Decimal(near) / Decimal(10) ** 24} NEAR is below 3 legs of gas')
        cost = self.cost_check(order_units)
        if cost['round_trip'] > self.cfg.cost_filter:
            return self.skip_cost(sig, label, cost)
        n = 1 + sum(1 for r in self.state['rounds'] if r['status'] != 'ABORTED')  # receipt numbering
        mid = self.net(self.market.mid)
        rnd = {'n': n, 'attempt_id': len(self.state['rounds']) + 1, 'status': 'BUYING', 'signal': sig.to_dict(), 'signal_label': label, 'cost_check': cost,
               'buy_mid': str(mid), 'buy_started_ms': now, 'sell_attempts': 0, 'reject_since_ms': None,
               'exit_blocked': False, 'stop_notified': False, 'time_notified': False}
        self.state['rounds'].append(rnd)
        self.save()  # durable BEFORE the leg: a crash resumes this exact signal
        self.event('BUY_START', f'round #{n}: {label}; round-trip cost {cost["round_trip_pct"]}% '
                                f'<= {self.cfg.cost_filter * 100}%')
        self.book_buy(rnd, self.leg(sig))

    def entry_block(self, now: int, sig: Signal) -> str | None:
        """The time/count gates of an entry (also re-checked when a crashed buy is resumed)."""
        opened = sum(1 for r in self.state['rounds'] if r['status'] in ('OPEN', 'SELLING', 'CLOSED'))
        if self.loss_cap_reached():
            return 'LOSS_CAP'
        if opened >= self.cfg.max_rounds:
            return 'ROUND_CAP'
        if now >= self.cfg.no_buy_after_ms:
            return 'BUY_CUTOFF'
        if now < self.state['cooldown_until_ms']:
            return 'COOLDOWN'
        if now - sig.bar_close_ms > SIGNAL_MAX_AGE_MS:
            return 'STALE_SIGNAL'
        return None

    def block_text(self, block: str) -> str:
        return {'LOSS_CAP': f'session loss cap INR {self.cfg.loss_cap_inr} reached',
                'ROUND_CAP': f'{self.cfg.max_rounds} rounds used',
                'BUY_CUTOFF': f'no new buys after {ist(self.cfg.no_buy_after_ms)}',
                'COOLDOWN': f'STOP_LOSS cooldown until {ist(self.state["cooldown_until_ms"])}',
                'STALE_SIGNAL': 'the signal bar is more than 3 min old'}[block]

    def loss_cap_reached(self) -> bool:
        return self.state.get('loss_cap_hit') or self.session_after_tax_inr() <= -self.cfg.loss_cap_inr

    def cost_check(self, order_units: int) -> dict[str, Any]:
        oc = self.agent.oneclick
        tol = self.agent.config.quote_tolerance_bps
        buy = self.net(oc.quote, QuoteRequest(USDC, WNEAR, order_units, DRY_RUN_PLACEHOLDER_RECIPIENT,
                                              DRY_RUN_PLACEHOLDER_RECIPIENT, tol, dry=True))
        sell = self.net(oc.quote, QuoteRequest(WNEAR, USDC, buy.min_amount_out, DRY_RUN_PLACEHOLDER_RECIPIENT,
                                               DRY_RUN_PLACEHOLDER_RECIPIENT, tol, dry=True))
        mid = self.net(self.market.mid)
        gas_usd = 2 * self.leg_gas_estimate() * mid
        spent = M.usdc(order_units)
        back = M.usdc(sell.min_amount_out)
        rt = (spent - back + gas_usd) / spent
        return {'entry_quote': buy.quote_id, 'exit_quote': sell.quote_id, 'usdc_in': str(spent),
                'wnear_guaranteed': str(M.wnear(buy.min_amount_out)), 'usdc_back_guaranteed': str(back),
                'gas_usd_both_legs': str(gas_usd), 'near_usd': str(mid), 'round_trip': rt,
                'round_trip_pct': str((rt * 100).quantize(Decimal('0.001')))}

    def skip_cost(self, sig: Signal, label: str, cost: dict[str, Any]) -> None:
        now = self.now_ms()
        text = (f'{label}: round-trip cost {cost["round_trip_pct"]}% > {self.cfg.cost_filter * 100}% '
                f'(quotes {cost["entry_quote"]} / {cost["exit_quote"]})')
        onchain = now - self.state['last_skip_journal_ms'] >= self.cfg.skip_journal_gap_s * 1000
        if onchain:
            today = self.net(self.agent.reader.stats)['today']['decisions']
            budget = self.net(self.agent.reader.policy).get('max_decisions_per_day', 100)
            onchain = budget - today > DECISIONS_RESERVED_FOR_TRADES
        if onchain:
            self.state['last_skip_journal_ms'] = now
            url = self.journal_skip(sig, f'SKIPPED_COST:{cost["round_trip_pct"]}%')
            text += f'; journaled {url}'
        else:
            text += '; journaled locally (on-chain skips are rate-limited to 1 per 15 min)'
        notify = now - self.state['last_skip_notify_ms'] >= self.cfg.skip_journal_gap_s * 1000
        if notify:
            self.state['last_skip_notify_ms'] = now
        self.event('SKIPPED_COST', text, notify=notify, cost=cost | {'round_trip': str(cost['round_trip'])})

    def journal_skip(self, sig: Signal, reason: str) -> str:
        """check_trade + record_decision with the agent's own verdict = rejected (no trade)."""
        a = self.agent
        h = sig.signal_hash(f'{a.config.mode}:{a.config.session_label}:SKIP')
        row = a.store.get(h) or a.store.create(h, a.config.mode, 'NEAR', {'signal': sig.to_dict()})
        if row['state'] == 'DONE':
            return row['data'].get('decision_tx_url', '')
        order_cents = int(self.cfg.order_usd * 100)
        verdict = self.net(a.reader.check_trade, WNEAR, 'BUY', order_cents, None)
        args = {'signal_hash': h, 'asset': WNEAR, 'side': 'BUY', 'mode': a.config.mode,
                'notional_usd_cents': order_cents, 'slippage_bps': 0, 'quote_id': 'none', 'agent_allowed': False,
                'agent_reason': reason[:128], 'rationale_hash': None}
        a.store.advance(h, 'DECISION_PENDING', side='BUY', decision_args=args, guard_check=verdict)
        written = self.net(a.writer.record_decision, **args)
        url = tx_url(a.reader.network, written.tx_hash)
        a.store.advance(h, 'DONE', result='SKIPPED', decision=written.result, decision_id=written.result['id'],
                        decision_tx_url=url)
        return url

    def leg_data(self, row: dict[str, Any], mid: Decimal) -> dict[str, Any]:
        d = row['data']
        o = d['outcome']
        gas_measured = False
        gas = M.LEG_NEAR_ESTIMATE
        before, after = d.get('near_yocto_before'), d.get('near_yocto_after')
        if self.cfg.live and before is not None and after is not None:
            gas, gas_measured = (Decimal(int(before)) - Decimal(int(after))) / Decimal(10) ** 24, True
        return {'amount_in': str(o['amount_in']), 'amount_out': str(o['amount_out']), 'near_usd': str(mid),
                'app_fee_bps': int(d['quote']['quote'].get('appFeeBps') or sum(int(f.get('fee', 0)) for f in
                                   d['quote'].get('quoteRequest', {}).get('appFees') or [])),
                'gas_near': str(gas), 'gas_measured': gas_measured, 'at_ms': self.now_ms(),
                'decision_id': d['decision_id'], 'decision_tx_url': d.get('decision_tx_url'),
                'deposit_tx_url': d.get('deposit_tx_url'), 'quote_id': d['quote'].get('correlationId'),
                'result': d.get('result')}

    def terminal_failure(self, row: dict[str, Any], leg: str) -> None:
        if row['state'] == 'NEEDS_REVIEW':
            raise HardStop(f'{leg}: NEEDS_REVIEW ({row["data"].get("review_reason")})')
        result = row['data'].get('result')
        if row['state'] == 'DONE' and result == 'FAILED' and \
                str((row['data'].get('outcome_args') or {}).get('tx_ref', '')).startswith('aborted-before-deposit'):
            return  # refused locally before anything was sent (e.g. an expired quote): handled like a rejection
        if row['state'] == 'DONE' and result in ('REFUNDED', 'FAILED'):
            last = (row['data'].get('timeline') or [{}])[-1].get('status', result)
            raise HardStop(f'{leg}: 1Click {last}')

    def book_buy(self, rnd: dict[str, Any], row: dict[str, Any]) -> None:
        self.terminal_failure(row, f'BUY round #{rnd["n"]}')
        state, result = row['state'], row['data'].get('result')
        if state == 'DONE' and result in ('DRY_RUN', 'SUCCESS'):
            leg = self.leg_data(row, Decimal(rnd['buy_mid']))
            if int(leg['amount_in']) > M.to_units(self.cfg.order_usd, M.USDC_DECIMALS):
                raise HardStop(f'BALANCE_MISMATCH: buy spent {leg["amount_in"]} units > ${self.cfg.order_usd}')
            rnd['buy'] = {'usdc_units': leg['amount_in'], 'wnear_units': leg['amount_out'], **leg}
            rnd['status'] = 'OPEN'
            if not self.cfg.live:
                self.state['paper_usdc_units'] = int(self.state['paper_usdc_units']) - int(leg['amount_in'])
            else:
                held = self.net(self.wallet.wnear_units, self.state)
                if held < int(leg['amount_out']):
                    raise HardStop(f'BALANCE_MISMATCH: wNEAR balance {held} < bought {leg["amount_out"]}')
            self.save()
            self.event('BUY', f'round #{rnd["n"]}: {M.usdc(leg["amount_in"]):.6f} USDC -> '
                              f'{M.wnear(leg["amount_out"]):.6f} wNEAR @ ${leg["near_usd"]} '
                              f'(decision #{leg["decision_id"]} {leg["decision_tx_url"]})', notify=True)
        elif state in ('SKIPPED', 'DONE'):  # guard rejected / local guard pre-check skip: no trade
            rnd['status'] = 'ABORTED'
            rnd['abort_reason'] = row['data'].get('skip_reason') or row['data'].get('decision', {}).get('reason')
            self.save()
            self.event('BUY_NOT_ALLOWED', f'round #{rnd["n"]}: {rnd["abort_reason"]} '
                                          f'{row["data"].get("decision_tx_url", "")}')
        # anything else (DEPOSITED with a tracking timeout): stays BUYING; the next tick resumes it

    # --- exit ---

    def exit_check(self, rnd: dict[str, Any]) -> tuple[M.ExitCheck, Decimal, int]:
        b = rnd['buy']
        q = self.net(self.agent.oneclick.quote, QuoteRequest(
            WNEAR, USDC, int(b['wnear_units']), DRY_RUN_PLACEHOLDER_RECIPIENT, DRY_RUN_PLACEHOLDER_RECIPIENT,
            self.agent.config.quote_tolerance_bps, dry=True))
        mid = self.net(self.market.mid)
        gas_usd = Decimal(b['gas_near']) * Decimal(b['near_usd']) + self.leg_gas_estimate() * mid
        chk = M.check(int(b['usdc_units']), q.min_amount_out, gas_usd, self.rate(), self.cfg.profit_target_inr,
                      self.now_ms(), self.cfg.time_exit_ms)
        return chk, mid, q.min_amount_out

    def status_line(self, rnd: dict[str, Any]) -> M.ExitCheck:
        chk, mid, proceeds = self.exit_check(rnd)
        line = (f'[{ist(self.now_ms())}] round #{rnd["n"]} OPEN | NEAR ${mid} | worst-case sell '
                f'{M.usdc(proceeds):.6f} USDC | net INR {chk.net_received_inr:+.2f} | after tax INR '
                f'{chk.net_after_tax_inr:+.2f} | to target INR {max(chk.to_target_inr(), Decimal(0)):.2f} | '
                f'to stop INR {chk.to_stop_inr():.2f} | {chk.decision}')
        self.state['last_status'] = {'line': line, 'check': chk.to_dict(), 'near_usd': str(mid)}
        self.save()
        self.print(line)
        return chk

    def manage_exit(self, rnd: dict[str, Any]) -> None:
        if self.cfg.live:
            held = self.net(self.wallet.wnear_units, self.state)
            if held < int(rnd['buy']['wnear_units']):
                raise HardStop(f'BALANCE_MISMATCH: wNEAR balance {held} < round #{rnd["n"]} bought '
                               f'{rnd["buy"]["wnear_units"]}')
        chk = self.status_line(rnd)
        decision = chk.decision
        if rnd.get('exit_blocked'):
            self.print('  exit blocked by the guard for 30 min: waiting for the owner (run with --resume-exit)')
            return
        if decision in (M.HOLD, M.PROFIT_EXIT):
            rnd['reject_since_ms'] = None  # a stop/time exit episode ended: a later one gets a fresh 30 min
        if decision == M.HOLD:
            return
        if decision == M.STOP_LOSS and not rnd['stop_notified']:
            rnd['stop_notified'] = True
            self.event('STOP_LOSS', f'round #{rnd["n"]}: net INR {chk.net_received_inr:+.2f} <= -5% of entry; '
                                    f'selling on a fresh quote', notify=True)
        if decision == M.TIME_EXIT and not rnd['time_notified']:
            rnd['time_notified'] = True
            self.event('TIME_EXIT', f'round #{rnd["n"]}: cut-off {ist(self.cfg.time_exit_ms)}; selling at market',
                       notify=True)
        floor = None
        if decision == M.PROFIT_EXIT:
            b = rnd['buy']
            floor = M.profit_floor_units(int(b['usdc_units']), chk.gas_total, chk.usd_inr, chk.target_inr)
        rnd['sell_attempts'] += 1
        sig = self.net(self.market.exit_signal, decision, rnd['sell_attempts'], rnd['n'], self.now_ms())
        rnd['pending_exit'] = {'reason': decision, 'signal': sig.to_dict(), 'floor_units': floor,
                               'attempt': rnd['sell_attempts'], 'check': chk.to_dict(), 'started_ms': self.now_ms()}
        rnd['status'] = 'SELLING'
        rnd['sell_mid'] = self.state['last_status']['near_usd']
        self.save()  # durable BEFORE the leg
        self.book_sell(rnd, self.leg(sig, min_proceeds_units=floor))

    def book_sell(self, rnd: dict[str, Any], row: dict[str, Any]) -> None:
        p = rnd['pending_exit']
        self.terminal_failure(row, f'SELL round #{rnd["n"]}')
        state, result = row['state'], row['data'].get('result')
        if state == 'DONE' and result in ('DRY_RUN', 'SUCCESS'):
            leg = self.leg_data(row, Decimal(rnd['sell_mid']))
            if int(leg['amount_in']) != int(rnd['buy']['wnear_units']):
                raise HardStop(f'BALANCE_MISMATCH: sold {leg["amount_in"]} wNEAR units, bought '
                               f'{rnd["buy"]["wnear_units"]}')
            rnd['sell'] = {'usdc_units': leg['amount_out'], 'wnear_units': leg['amount_in'], **leg}
            rnd['exit_reason'] = p['reason']
            self.close_round(rnd)
            return
        rnd['status'] = 'OPEN'
        reason = row['data'].get('skip_reason') or row['data'].get('decision', {}).get('reason')
        if state == 'SKIPPED' and reason == 'BELOW_PROFIT_FLOOR':
            self.save()
            self.event('PROFIT_RECHECK_FAILED', f'round #{rnd["n"]}: the fresh quote no longer clears the target; '
                                                f'not selling, still watching')
            return
        if state == 'SKIPPED' and reason == 'SHORT_SIGNAL_WITHOUT_SPOT_POSITION':
            raise HardStop(f'BALANCE_MISMATCH: the guard has no open position for round #{rnd["n"]}')
        if state in ('SKIPPED', 'DONE'):
            url = row['data'].get('decision_tx_url', '')
            if p['reason'] in (M.STOP_LOSS, M.TIME_EXIT):
                rnd['reject_since_ms'] = rnd['reject_since_ms'] or p['started_ms']
                waited = self.now_ms() - rnd['reject_since_ms']
                if waited >= self.cfg.exit_retry_window_s * 1000:
                    rnd['exit_blocked'] = True
                    self.save()
                    self.event('EXIT_BLOCKED', f'round #{rnd["n"]}: the guard rejected the {p["reason"]} sell for '
                                               f'{waited // 60000} min ({reason}); waiting for your decision',
                               notify=True)
                    return
            self.save()
            self.event('SELL_NOT_ALLOWED', f'round #{rnd["n"]} {p["reason"]} attempt {p["attempt"]}: {reason} {url}; '
                                           f'retrying next tick')
            return
        rnd['status'] = 'SELLING'  # LIVE tracking timed out: the next tick resumes it
        self.save()

    def close_round(self, rnd: dict[str, Any]) -> None:
        b, s = rnd['buy'], rnd['sell']
        fx = self.fx()
        mk = lambda leg: M.Leg(int(leg['usdc_units']), int(leg['wnear_units']), Decimal(leg['near_usd']),  # noqa: E731
                               int(leg['app_fee_bps']), Decimal(leg['gas_near']), int(leg['at_ms']),
                               leg.get('deposit_tx_url') or f'(paper) quote {leg["quote_id"]}',
                               leg.get('decision_tx_url') or '')
        receipt = M.Receipt.build(rnd['n'], rnd['exit_reason'], mk(b), mk(s), Decimal(fx['usd_inr']), fx['source'])
        lines = receipt.lines()
        rnd['receipt'] = receipt.to_dict()
        rnd['after_tax_inr'] = str(receipt.after_tax * receipt.usd_inr)
        rnd['status'] = 'CLOSED'
        rnd['closed_ms'] = self.now_ms()
        if not self.cfg.live:
            self.state['paper_usdc_units'] = int(self.state['paper_usdc_units']) + int(s['usdc_units'])
        if rnd['exit_reason'] == M.STOP_LOSS:
            self.state['cooldown_until_ms'] = self.now_ms() + self.cfg.stop_cooldown_s * 1000
        try:
            wallet_usdc = M.usdc(self.net(self.wallet.usdc_units, self.state))
        except NetworkDown:  # the round is booked; only the wallet line of the receipt is affected
            wallet_usdc = M.usdc(self.state.get('paper_usdc_units', 0))
        text = receipt.render(wallet_usdc=wallet_usdc, wallet_inr=wallet_usdc * receipt.usd_inr,
                              session_after_tax_inr=self.session_after_tax_inr(), ist=IST)
        rnd['receipt_text'] = text
        if self.session_after_tax_inr() <= -self.cfg.loss_cap_inr:
            self.state['loss_cap_hit'] = True
        self.save()  # the closed round, the paper balance, the cooldown and the loss cap: one atomic write
        self.print('\n' + text + '\n')
        self.event('SELL', f'round #{rnd["n"]} {rnd["exit_reason"]}: after tax INR {lines["after_tax"][1]:+.2f} '
                           f'(net INR {lines["received"][1]:+.2f})', notify=True)
        if self.state['loss_cap_hit']:
            self.event('SESSION_LOSS_CAP', f'realized after tax INR {self.session_after_tax_inr():+.2f} reached '
                                           f'-INR {self.cfg.loss_cap_inr}: stopped for good', notify=True)
        self.write_report()

    # --- report ---

    def write_report(self) -> None:
        from anchorium.autonomous_report import render  # noqa: PLC0415
        path = self.root / self.cfg.report_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(self.state, guard=self.agent.reader.contract_id, network=self.agent.reader.network))


def status_text(state: dict[str, Any], live_line: str | None = None) -> str:
    lines = [f'session {state["session"]} ({state["mode"]}), started {state["started_at"]}']
    r = next((x for x in state['rounds'] if x['status'] in ('BUYING', 'OPEN', 'SELLING')), None)
    closed = [x for x in state['rounds'] if x['status'] == 'CLOSED']
    total = sum((Decimal(x['after_tax_inr']) for x in closed), Decimal(0))
    lines.append(f'rounds closed {len(closed)}; session after tax INR {total:+.2f}; '
                 + (f'round #{r["n"]} {r["status"]}' if r else 'flat')
                 + (f'; HARD STOP: {state["hard_stop"]}' if state.get('hard_stop') else '')
                 + (f'; finished: {state["finished"]["reason"]}' if state.get('finished') else ''))
    if live_line:
        lines.append(live_line)
    elif r and state.get('last_status'):
        lines.append('last status: ' + state['last_status']['line'])
    lines.append('last 5 events:')
    lines += [f'  [{e["at"]}] {e["event"]}: {e["text"]}' for e in state.get('events', [])[-5:]]
    return '\n'.join(lines)
