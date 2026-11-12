"""PAPER-ONLY research experiment: bear rounds on live data. Never trades and never signs anything.

A bear round (variant C of docs/regime_backtest.md): on the unchanged frozen strategy's SHORT signal, sell $2 of the
wallet's wNEAR for USDC, then buy wNEAR back with all of that USDC when the round's profit after the two legs' fees,
slippage, deposit NEAR and the 31.2% per-disposal tax reserve is at least the target (INR 1.00). Profit-only close,
as in the backtest: no stop, no time exit. A round still open at the end is reported open with its unrealized numbers.

Two modes, each run as its own session:
* `--regime-filter` (variant C as backtested): a SHORT opens a round only while the confirmed regime is BEAR.
* no filter (UNTESTED, added on the owner's request on 12 Nov): every SHORT signal may open a round.

Live inputs: Hyperliquid public 1m/15m/1h candles and allMids, real 1Click DRY quotes (fills simulated at each quote's
guaranteed `minAmountOut`), Coinbase USD/INR. The wallet starts at INR 500, half USDC and half wNEAR at the start price,
booked as FIFO lots (`research.regime.ledger`).

Not journaled on chain: the deployed v1 guard only allows a SELL against an open BUY position, so a sell-first round
cannot pass check_trade without a contract change, and the owner forbade any guard change. This is research, never
imported by `anchorium` and never run LIVE.
"""
from __future__ import annotations

import argparse
import json
import os
import signal as signal_mod
import time
import traceback
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import httpx

from anchorium import money as M
from anchorium.agent import signals as sig
from anchorium.agent.orchestrator import DRY_RUN_PLACEHOLDER_RECIPIENT
from anchorium.agent.orchestrator import USDC as USDC_ASSET
from anchorium.near.intents import OneClickClient, QuoteRequest
from anchorium.paper_trade import fetch_fx
from research.regime import ledger as L
from research.regime import regime as R

IST = timezone(timedelta(hours=5, minutes=30))
WNEAR_ASSET = 'nep141:wrap.near'
HL_INFO = 'https://api.hyperliquid.xyz/info'
GAS_LEG_NEAR = Decimal('0.0002170906627479') + Decimal('0.00125')  # ft_transfer + new deposit address storage
ORDER_USD = Decimal('2.00')
COST_FILTER = Decimal('0.016')  # the owner's version A threshold, deposit NEAR included
TARGET_INR = L.DEFAULT_PROFIT_TARGET_INR
TOL_BPS = 10
SIGNAL_MAX_AGE_MS = 180_000
START_INR = Decimal('500')


def ist(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, IST).strftime('%Y-%m-%d %H:%M:%S IST')


def now_ms() -> int:
    return int(time.time() * 1000)


class Bear:
    def __init__(self, session: str, regime_filter: bool, until_ms: int, stop_after: int, out: Path):
        self.session, self.regime_filter, self.until_ms, self.stop_after = session, regime_filter, until_ms, stop_after
        self.path = out / f'{session}.json'
        self.report = out / f'{session}.md'
        self.http = httpx.Client(timeout=20)
        self.oc = OneClickClient()
        self.stop = False
        self.state: dict[str, Any] = {}
        self.led = L.Ledger()

    # ----- io -----
    def save(self) -> None:
        self.state['ledger'] = self.led.to_dict()
        tmp = self.path.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            json.dump(self.state, f, indent=1, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self.report.write_text(self.render())

    def event(self, kind: str, text: str) -> None:
        e = {'at': ist(now_ms()), 'event': kind, 'text': text}
        self.state.setdefault('events', []).append(e)
        self.state['events'] = self.state['events'][-300:]
        print(f'[{e["at"]}] {kind}: {text}', flush=True)

    def candles(self, interval: str, lookback_ms: int, t: int) -> list[dict[str, Any]]:
        body = {'type': 'candleSnapshot', 'req': {'coin': 'NEAR', 'interval': interval, 'startTime': t - lookback_ms,
                                                  'endTime': t}}
        return self.http.post(HL_INFO, json=body).raise_for_status().json()

    def mid(self) -> Decimal:
        return Decimal(str(self.http.post(HL_INFO, json={'type': 'allMids'}).raise_for_status().json()['NEAR']))

    def quote(self, origin: str, dest: str, amount: int):
        return self.oc.quote(QuoteRequest(origin, dest, amount, DRY_RUN_PLACEHOLDER_RECIPIENT,
                                          DRY_RUN_PLACEHOLDER_RECIPIENT, TOL_BPS, dry=True))

    def fx(self) -> Decimal:
        f = self.state.get('fx')
        if not f or now_ms() - f['ms'] > 30 * 60_000:
            got = fetch_fx(self.http)
            self.state['fx'] = {'usd_inr': got['usd_inr'], 'source': got['source'], 'ms': now_ms()}
        return Decimal(self.state['fx']['usd_inr'])

    # ----- lifecycle -----
    def start(self) -> None:
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            self.led = L.Ledger.from_dict(self.state['ledger'])
            self.event('RESUME', f'resumed session {self.session}')
            return
        t, p, fx = now_ms(), self.mid(), None
        self.state = {'session': self.session, 'mode': 'PAPER (DRY_RUN)', 'regime_filter': self.regime_filter,
                      'started_ms': t, 'started_at': ist(t), 'until': ist(self.until_ms), 'rounds': [],
                      'last_bar_ms': 0, 'skips': {}}
        fx = self.fx()
        with localcontext(prec=L.PREC):
            cap = START_INR / fx
            u0, n0 = L.units(L.USDC, cap / 2), L.units(L.WNEAR, cap / 2 / p)
            self.led.acquire(L.USDC, u0, L.value_usd(L.USDC, u0, p) * fx, t, 'start')
            self.led.acquire(L.WNEAR, n0, L.value_usd(L.WNEAR, n0, p) * fx, t, 'start')
        self.state.update({'start_price': str(p), 'start_fx': str(fx), 'start_usdc_units': u0, 'start_wnear_units': n0})
        self.event('START', f'PAPER bear session {self.session} ({"variant C: SHORT only in BEAR" if self.regime_filter else "UNTESTED: every SHORT"}): '
                   f'{L.human(L.USDC, u0):.6f} USDC + {L.human(L.WNEAR, n0):.6f} wNEAR @ ${p} = INR 500 at {fx} INR/USD')
        self.save()

    def skip(self, why: str, text: str) -> None:
        self.state['skips'][why] = self.state['skips'].get(why, 0) + 1
        self.event(why, text)

    def open_round(self) -> dict[str, Any] | None:
        return next((r for r in self.state['rounds'] if r['status'] == 'OPEN'), None)

    def tick(self) -> None:
        t = now_ms()
        rnd = self.open_round()
        if rnd:
            self.manage(rnd, t)
            return
        found, _ = sig.evaluate_latest(sig.HyperliquidCandles().fetch('NEAR', t), 'NEAR', t)
        if found is None or found.bar_open_ms <= self.state['last_bar_ms']:
            return
        self.state['last_bar_ms'] = found.bar_open_ms
        if found.direction != 'SHORT':
            self.skip('SKIPPED_LONG_BEAR_ONLY', f'{found.signal_type} close {found.close} (this experiment trades SHORTs only)')
            return
        if t - found.bar_close_ms > SIGNAL_MAX_AGE_MS:
            self.skip('SKIPPED_STALE_SIGNAL', f'{found.signal_type} is {(t - found.bar_close_ms) // 1000} s old')
            return
        p, fx = self.mid(), self.fx()
        n = L.units(L.WNEAR, ORDER_USD / p)
        if n > self.led.balance(L.WNEAR):
            self.skip('SKIPPED_BALANCE', 'not enough wNEAR for a $2 sell')
            return
        sell = self.quote(WNEAR_ASSET, USDC_ASSET, n)
        back = self.quote(USDC_ASSET, WNEAR_ASSET, sell.min_amount_out)
        with localcontext(prec=L.PREC):
            rt = (Decimal(n - back.min_amount_out) / n) + 2 * GAS_LEG_NEAR * p / (L.human(L.WNEAR, n) * p)
        rt_pct = (rt * 100).quantize(Decimal('0.001'))
        if self.regime_filter:
            reg = R.current_regime(self.candles('15m', 10 * 86_400_000, t), self.candles('1h', 10 * 86_400_000, t), t, rt)
            regime = reg.confirmed if reg else R.CHOP
            if regime != R.BEAR:
                why = reg.raw.reason if reg else 'no history'
                self.skip('SKIPPED_REGIME', f'{found.signal_type} close {found.close}: regime {regime} ({why}), round trip {rt_pct}%')
                return
        if rt > COST_FILTER:
            self.skip('SKIPPED_COST', f'{found.signal_type}: round trip {rt_pct}% > {COST_FILTER * 100}%')
            return
        sw = L.Swap(L.WNEAR, n, L.USDC, sell.min_amount_out, p, fx, GAS_LEG_NEAR, t, sell.quote_id)
        d = L.apply_swap(self.led, sw)
        num = len(self.state['rounds']) + 1
        self.state['rounds'].append({'n': num, 'status': 'OPEN', 'signal': found.signal_type, 'signal_close': found.close,
                                     'signal_bar': ist(found.bar_open_ms), 'open': sw.to_dict(),
                                     'open_tax_inr': str(d.tax_reserve_inr), 'open_cost_check_pct': str(rt_pct),
                                     'checks': 0})
        self.event('SELL_OPEN', f'round #{num} on {found.signal_type} ({ist(found.bar_open_ms)}): sold '
                   f'{L.human(L.WNEAR, n):.6f} wNEAR @ ${p} -> {L.human(L.USDC, sell.min_amount_out):.6f} USDC '
                   f'(guaranteed; quote {sell.quote_id}); round-trip check {rt_pct}%; open tax reserve INR '
                   f'{d.tax_reserve_inr:.4f}')

    def manage(self, rnd: dict[str, Any], t: int) -> None:
        op = L.Swap.from_dict(rnd['open'])
        p, fx = self.mid(), self.fx()
        q = self.quote(USDC_ASSET, WNEAR_ASSET, op.bought_units)
        cc = L.close_check(self.led, L.BEAR_ROUND, op, Decimal(rnd['open_tax_inr']), q.min_amount_out, p, fx,
                           GAS_LEG_NEAR, TARGET_INR, t)
        rnd['checks'] += 1
        rnd['last'] = {'at': ist(t), 'near_usd': str(p), 'wnear_back': str(L.human(L.WNEAR, q.min_amount_out)),
                       'net_after_tax_inr': str(cc.net_after_tax_inr)}
        print(f'[{ist(t)}] HOLD #{rnd["n"]}: NEAR ${p}; buy-back {L.human(L.WNEAR, q.min_amount_out):.6f} wNEAR for '
              f'{L.human(L.WNEAR, op.sold_units):.6f} sold; net after tax INR {cc.net_after_tax_inr:+.4f} '
              f'(target {TARGET_INR})', flush=True)
        if not cc.clears:
            return
        q2 = self.quote(USDC_ASSET, WNEAR_ASSET, op.bought_units)  # fresh quote right before the (simulated) deposit
        p2 = self.mid()
        t2 = now_ms()
        cc2 = L.close_check(self.led, L.BEAR_ROUND, op, Decimal(rnd['open_tax_inr']), q2.min_amount_out, p2, fx,
                            GAS_LEG_NEAR, TARGET_INR, t2)
        if not cc2.clears:
            self.event('RECHECK_FAILED', f'round #{rnd["n"]}: fresh quote net INR {cc2.net_after_tax_inr:+.4f}')
            return
        sw = L.Swap(L.USDC, op.bought_units, L.WNEAR, q2.min_amount_out, p2, fx, GAS_LEG_NEAR, t2, q2.quote_id)
        d = L.apply_swap(self.led, sw)
        rnd.update({'status': 'CLOSED', 'close': sw.to_dict(), 'close_tax_inr': str(d.tax_reserve_inr),
                    'net_after_tax_inr': str(cc2.net_after_tax_inr), 'check': cc2.to_dict()})
        rnd['receipt'] = self.receipt(rnd)
        self.event('BUY_BACK_CLOSE', f'round #{rnd["n"]} closed: net after tax INR {cc2.net_after_tax_inr:+.4f}')
        print(rnd['receipt'], flush=True)

    # ----- receipts and report -----
    def receipt(self, rnd: dict[str, Any]) -> str:
        op, cl = L.Swap.from_dict(rnd['open']), L.Swap.from_dict(rnd['close'])
        with localcontext(prec=L.PREC):
            q, P, q2 = L.human(L.WNEAR, op.sold_units), L.human(L.USDC, op.bought_units), L.human(L.WNEAR, cl.bought_units)
            po, pc, fx = op.near_usd, cl.near_usd, cl.usd_inr
            gross = q * (po - pc)                       # the price fall on the wNEAR sold
            cost_open = q * po - P                      # fee + slippage of the sell leg (USD at the open price)
            cost_close = P - q2 * pc                    # fee + slippage of the buy-back (USD at the close price)
            gas = (op.gas_near + cl.gas_near) * pc
            pnl = gross - cost_open - cost_close - gas  # == (q2 - q) * pc - gas
            tax = Decimal(rnd['open_tax_inr']) + Decimal(rnd['close_tax_inr'])
            net_inr = pnl * fx - tax
            assert abs(net_inr - Decimal(rnd['net_after_tax_inr'])) < Decimal('1e-30'), 'receipt != booked close'
            usd = M._alloc([gross, -cost_open, -cost_close, -gas], pnl, Decimal('0.000001'))
            inr = M._alloc([gross * fx, -cost_open * fx, -cost_close * fx, -gas * fx], pnl * fx, Decimal('0.01'))
            tax_d = tax.quantize(Decimal('0.01'), rounding='ROUND_CEILING')
            pnl_d = pnl.quantize(Decimal('0.000001'), rounding='ROUND_FLOOR')
            pnl_i = (pnl * fx).quantize(Decimal('0.01'), rounding='ROUND_FLOOR')
        w = '=' * 64
        rows = [('Price move on the wNEAR sold', 0), ('Sell leg: 1Click fee + slippage', 1),
                ('Buy-back: 1Click fee + slippage', 2), ('Deposit NEAR (gas + storage, 2 legs)', 3)]
        lines = [w, f'ROUND #{rnd["n"]}  CLOSED: PROFIT (bear round)   PAPER: no funds moved', w,
                 f'Signal      : {rnd["signal"]} at {rnd["signal_bar"]} (frozen strategy, unchanged)',
                 f'Sold        : {q:.6f} wNEAR @ ${po} -> {P:.6f} USDC ({ist(op.at_ms)})',
                 f'Bought back : {P:.6f} USDC -> {q2:.6f} wNEAR @ ${pc} ({ist(cl.at_ms)})',
                 f'Gained      : {q2 - q:+.6f} wNEAR, worth ${(q2 - q) * pc:.6f} at the close price',
                 f'Quotes      : {op.tx} / {cl.tx} (1Click DRY, filled at the guaranteed minAmountOut)',
                 f'FX          : {fx} INR/USD', '-' * 64, f'{"":38}{"USD":>12}{"INR":>12}']
        for label, i in rows:
            lines.append(f'{label:38}{usd[i]:>+12.6f}{inr[i]:>+12.2f}')
        lines += ['-' * 64, f'{"Net received":38}{pnl_d:>+12.6f}{pnl_i:>+12.2f}',
                  f'{"Tax reserve 31.2% (rounded up)":38}{"":>12}{-tax_d:>+12.2f}',
                  f'{"YOURS AFTER TAX":38}{"":>12}{pnl_i - tax_d:>+12.2f}', w]
        return '\n'.join(lines)

    def render(self) -> str:
        s = self.state
        closed = [r for r in s['rounds'] if r['status'] == 'CLOSED']
        total = sum((Decimal(r['net_after_tax_inr']) for r in closed), Decimal(0))
        out = [f'# Bear-round paper experiment: session `{s["session"]}`', '',
               'PAPER (DRY_RUN) research, not version A and never LIVE. Real live signals, real 1Click dry quotes '
               '(fills at `minAmountOut`), no funds moved, not journaled on chain (the v1 guard allows SELL only '
               'against an open BUY position).', '',
               f'* Mode: {"variant C as backtested (SHORT only while the regime is BEAR)" if s["regime_filter"] else "UNTESTED: every SHORT signal (no regime filter), on the owner request of 12 Nov"}',
               f'* Started {s["started_at"]}; runs until {s["until"]} or {self.stop_after} closed round(s)'
               + (f'; finished {s["finished"]}' if s.get('finished') else ''),
               f'* Wallet at start: INR 500 = {L.human(L.USDC, s["start_usdc_units"]):.6f} USDC + '
               f'{L.human(L.WNEAR, s["start_wnear_units"]):.6f} wNEAR @ ${s["start_price"]} ({s["start_fx"]} INR/USD)',
               f'* Closed rounds: {len(closed)}, realized after tax INR {total:+.4f}',
               f'* Skips: {", ".join(f"{k}: {v}" for k, v in sorted(s["skips"].items())) or "none"}', '']
        for r in closed:
            out += ['```', r['receipt'], '```', '']
        o = self.open_round()
        if o:
            last = o.get('last', {})
            out += ['## Open round (not a result)', '',
                    f'Round #{o["n"]} sold {L.human(L.WNEAR, int(o["open"]["sold_units"])):.6f} wNEAR @ ${o["open"]["near_usd"]} '
                    f'on {o["signal"]}. Last check {last.get("at")}: NEAR ${last.get("near_usd")}, net after tax INR '
                    f'{Decimal(last.get("net_after_tax_inr", "0")):+.4f} (target {TARGET_INR}).', '']
        out += ['## Honesty notes', '',
                '* Half the wallet sits in wNEAR, so the wallet itself loses value when NEAR falls; a round\'s profit is '
                'measured as wNEAR regained, valued at the close price.',
                '* No stop and no time exit (profit-only close, as backtested): a round can stay open.', '',
                '## Events', ''] + [f'* [{e["at"]}] {e["event"]}: {e["text"]}' for e in s.get('events', [])[-60:]]
        return '\n'.join(out) + '\n'

    def run(self) -> None:
        signal_mod.signal(signal_mod.SIGINT, lambda *_: setattr(self, 'stop', True))
        self.start()
        fails = 0
        while not self.stop:
            closed = sum(r['status'] == 'CLOSED' for r in self.state['rounds'])
            if closed >= self.stop_after or now_ms() >= self.until_ms:
                break
            try:
                self.tick()
                fails = 0
            except Exception as e:  # paper: log, back off, keep going
                fails += 1
                self.event('ERROR', f'{type(e).__name__}: {e} (retry {fails})')
                if fails >= 10:
                    traceback.print_exc()
                    break
            self.save()
            time.sleep(max(1, 60 - (time.time() % 60) + 3))
        self.state['finished'] = ist(now_ms())
        self.event('FINISHED', f'{sum(r["status"] == "CLOSED" for r in self.state["rounds"])} closed')
        self.save()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--session', required=True)
    ap.add_argument('--regime-filter', action='store_true')
    ap.add_argument('--until', required=True, help='ISO time, e.g. 2026-11-13T20:00:00+05:30')
    ap.add_argument('--stop-after-rounds', type=int, default=1)
    ap.add_argument('--out', default='var/research')
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    until = int(datetime.fromisoformat(a.until).timestamp() * 1000)
    Bear(a.session, a.regime_filter, until, a.stop_after_rounds, out).run()


if __name__ == '__main__':
    main()
