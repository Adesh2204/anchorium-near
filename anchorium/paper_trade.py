"""Paper-trade session: one round trip through the real pipeline with a paper INR balance.

* Signals: the frozen strategy on live Hyperliquid 1m candles (completed bars only).
* Every entry/exit gets a real 1Click DRY quote and a real `record_decision` on the testnet guard (agent
  function-call key). Allowed legs are settled on paper at the quote's `amountOut` (`record_outcome` DRY_RUN).
* Exit: the next SHORT signal on the held coin, or TIME_EXIT once the position is `max_hold_s` old.
* Nothing moves real funds. The report is built only from what was recorded: the checkpoint store (quotes,
  decisions, outcomes), the session state (FX rate, starting balance) and chain/RPC reads (gas burnt).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import httpx

from anchorium.agent import signals as sig

MAX_HOLD_S = 4 * 3600
COINBASE_FX = 'https://api.coinbase.com/v2/exchange-rates?currency=USD'
ECB_FX = 'https://api.frankfurter.dev/v1/latest?base=USD&symbols=INR'
TESTNET_RPC = 'https://rpc.testnet.fastnear.com'
MAINNET_RPC = 'https://rpc.mainnet.fastnear.com'
#: Real mainnet wrap.near `ft_transfer` transactions measured on 2026-11-11 (gas burnt incl. all receipts). Used as
#: the estimate for one NEP-141 deposit per leg; the USDC contract's ft_transfer was not sampled.
FT_TRANSFER_SAMPLES = {
    '5gXzCv5RiZkBPN7YW2e815Yu1mshXbjXXhXqNoWrVqyy': Decimal('0.0002168146437504'),
    'AyNvWU5TU1rfHoZBYjETAEwYvwEzmjHpPrw1Mpi92HJr': Decimal('0.0002170906627479'),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def fetch_fx(client: httpx.Client | None = None) -> dict[str, Any]:
    """Live USD/INR from Coinbase's public rates API, with the ECB reference rate as a cross-check."""
    client = client or httpx.Client(timeout=15)
    rate = Decimal(str(client.get(COINBASE_FX).raise_for_status().json()['data']['rates']['INR']))
    fx = {'usd_inr': str(rate.quantize(Decimal('0.0001'))), 'source': 'Coinbase public exchange-rates API',
          'url': COINBASE_FX, 'fetched_at': utcnow()}
    try:
        ecb = client.get(ECB_FX).raise_for_status().json()
        fx['cross_check'] = {'usd_inr': str(ecb['rates']['INR']), 'source': f'ECB reference rate {ecb["date"]}',
                             'url': ECB_FX}
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        fx['cross_check'] = {'error': f'{type(exc).__name__}: {exc}'}
    return fx


# ----- session state -----

@dataclass
class Session:
    path: Path

    def load(self) -> dict[str, Any] | None:
        return json.loads(self.path.read_text()) if self.path.exists() else None

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, indent=2, default=str))
        tmp.replace(self.path)


def rows(store) -> list[dict[str, Any]]:
    hashes = [r[0] for r in store.db.execute('SELECT signal_hash FROM runs ORDER BY created_at, rowid')]
    return [store.get(h) for h in hashes]


def settled(row: dict[str, Any], side: str) -> bool:
    return row['state'] == 'DONE' and row['side'] == side and row['data'].get('result') == 'DRY_RUN'


def legs(store) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The session's entry and exit, derived from the checkpoint store (the source of truth)."""
    entry = exit_ = None
    for row in rows(store):
        if entry is None and settled(row, 'BUY'):
            entry = row
        elif entry is not None and exit_ is None and settled(row, 'SELL'):
            exit_ = row
    return entry, exit_


def decision_time_ms(row: dict[str, Any]) -> int:
    return int(row['data']['decision']['timestamp_ms'])


def guard_prechecks(reader, order_cents: int) -> list[dict[str, Any]]:
    out = []
    for coin, asset in sig.SPOT_ASSET.items():
        verdict = reader.check_trade(asset, 'BUY', order_cents, None)
        out.append({'coin': coin, 'asset': asset, 'side': 'BUY', 'notional_usd_cents': order_cents, **verdict,
                    'checked_at': utcnow()})
    return out


def run(agent, reader, *, coins: list[str], session: Session, paper_inr: Decimal, max_hold_s: int = MAX_HOLD_S,
        print_=print, sleep=time.sleep, clock=time.time, feed=None, exit_signal=None,
        max_wait_s: int | None = None) -> dict[str, Any]:
    """Runs (or resumes) the session until the exit settles. Returns the final state."""
    feed = feed or sig.HyperliquidCandles()
    state = session.load()
    if state is None:
        fx = fetch_fx()
        rate = Decimal(fx['usd_inr'])
        order_usd = agent.config.order_usd
        state = {'started_at': utcnow(), 'paper_inr': str(paper_inr), 'fx': fx,
                 'paper_usd': str((paper_inr / rate).quantize(Decimal('0.000001'))),
                 'order_usd': str(order_usd), 'coins': coins, 'max_hold_s': max_hold_s,
                 'session_label': agent.config.session_label,
                 'guard_precheck': guard_prechecks(reader, int(order_usd * 100)),
                 'policy_at_start': reader.policy()}
        if Decimal(state['paper_usd']) < order_usd:
            raise SystemExit(f'paper balance ${state["paper_usd"]} is below the ${order_usd} order')
        session.save(state)
    print_(f'paper balance: INR {state["paper_inr"]} = ${state["paper_usd"]} at {state["fx"]["usd_inr"]} INR/USD '
           f'({state["fx"]["source"]}, {state["fx"]["fetched_at"]})')
    print_(f'order: ${state["order_usd"]} per trade (guard max ${state["policy_at_start"]["max_notional_per_trade_usd_cents"] / 100:.2f}); '
           f'cash kept: ${Decimal(state["paper_usd"]) - Decimal(state["order_usd"])}')
    for c in state['guard_precheck']:
        print_(f'guard check_trade BUY {c["coin"]} ${c["notional_usd_cents"] / 100:.2f} -> '
               f'{"ALLOWED" if c["allowed"] else "REJECTED"} ({c["reason"]})')

    for row in agent.store.unfinished():  # crash recovery: finish a half-journaled leg first
        if row['state'] != 'NEEDS_REVIEW':
            agent.process(sig.Signal(**row['data']['signal']))
    started = clock()
    exit_signal = exit_signal or (lambda coin, kind: sig.latest_bar_signal(feed, coin, kind, source='max-hold-timer'))
    while True:
        entry, exit_ = legs(agent.store)
        if exit_ is not None:
            state['finished_at'] = utcnow()
            # NEAR's reference price, used only to value gas
            state['near_usd_at_finish'] = str(agent.oneclick.tokens(refresh=True)['nep141:wrap.near'].price_usd)
            session.save(state)
            return state
        now_ms = int(clock() * 1000)
        if entry is None:
            if max_wait_s is not None and clock() - started > max_wait_s:
                raise SystemExit('no LONG entry before the wait limit; nothing to report')
            for coin in coins:
                try:
                    signal, info = sig.evaluate_latest(feed.fetch(coin, now_ms), coin, now_ms)
                except Exception as exc:  # noqa: BLE001 - one bad feed read must not stop the session
                    print_(f'[feed]    {coin}: {type(exc).__name__}: {exc}')
                    continue
                if signal and signal.direction == 'LONG':
                    agent.process(signal)
                    if legs(agent.store)[0] is not None:
                        break
        else:
            coin = entry['coin']
            held_s = (now_ms - decision_time_ms(entry)) / 1000
            signal = None
            try:
                candidate, _ = sig.evaluate_latest(feed.fetch(coin, now_ms), coin, now_ms)
                if candidate and candidate.direction == 'SHORT':
                    signal = candidate
            except Exception as exc:  # noqa: BLE001
                print_(f'[feed]    {coin}: {type(exc).__name__}: {exc}')
            if signal is None and held_s >= max_hold_s:
                signal = exit_signal(coin, 'TIME_EXIT')
            if signal is not None:
                agent.process(signal)
                if legs(agent.store)[1] is not None:
                    continue  # settled: finish now (a rejected exit waits for the next bar and tries again)
        sleep(max(1.0, 60 - (clock() % 60) + 2))  # just after the next 1m bar closes


# ----- report -----

def q(value: Decimal, places: str = '0.01') -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def money(value: Decimal, places: str = '0.000001', unit: str = '$') -> str:
    v = q(value, places)
    return f'{"-" if v < 0 else ""}{unit}{abs(v)}'


def leg_figures(row: dict[str, Any], oneclick_tokens: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execution and cost figures for one settled leg, from the recorded quote and the `/v0/tokens` reference
    prices recorded with it. (The quote's own `amountOutUsd` is not used: for USDC -> wNEAR it equals
    `amountInUsd`, so it cannot measure cost.)"""
    quote = row['data']['quote']
    qq, req = quote['quote'], quote['quoteRequest']
    fee_bps = sum(int(f.get('fee', 0)) for f in req.get('appFees') or [])
    in_amt, out_amt = Decimal(qq['amountInFormatted']), Decimal(qq['amountOutFormatted'])
    ref = row['data']['ref_prices_usd']
    ref_in, ref_out = Decimal(ref['in']), Decimal(ref['out'])
    in_usd, out_usd = in_amt * ref_in, out_amt * ref_out
    fee_usd = in_usd * fee_bps / 10_000
    total_cost = in_usd - out_usd  # value lost vs 1Click's reference prices at quote time
    buy = row['side'] == 'BUY'
    qty = out_amt if buy else in_amt
    usdc = in_amt if buy else out_amt
    ref_price = ref_out if buy else ref_in
    return {
        'side': row['side'], 'coin': row['coin'], 'quantity': qty, 'usdc': usdc,
        'exec_price': usdc / qty, 'ref_price': ref_price, 'usdc_ref_price': ref_in if buy else ref_out,
        'amount_in_usd': in_usd, 'amount_out_usd': out_usd,
        'fee_bps': fee_bps, 'fee_usd': fee_usd, 'slippage_usd': total_cost - fee_usd, 'total_cost_usd': total_cost,
        'worst_case_bps': row['data']['decision_args']['slippage_bps'],
        'quote_id': quote['correlationId'], 'quote_time': quote['timestamp'],
        'decision_id': row['data']['decision_id'], 'decision_time_ms': decision_time_ms(row),
        'decision_tx': row['data']['decision_tx'], 'outcome_tx': row['data']['outcome_tx'],
        'signal': row['data']['signal'], 'realized_pnl_usd_cents': row['data']['outcome'].get('realized_pnl_usd_cents'),
    }


def tx_gas(tx_hash: str, sender: str, rpc_url: str = TESTNET_RPC, client: httpx.Client | None = None) -> dict:
    client = client or httpx.Client(timeout=30)
    body = client.post(rpc_url, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tx',
                                      'params': {'tx_hash': tx_hash, 'sender_account_id': sender,
                                                 'wait_until': 'FINAL'}}).raise_for_status().json()
    if 'error' in body:
        raise RuntimeError(f'tx {tx_hash}: {body["error"]}')
    outs = [body['result']['transaction_outcome']] + body['result']['receipts_outcome']
    return {'gas_burnt': sum(int(o['outcome']['gas_burnt']) for o in outs),
            'near_burnt': Decimal(sum(int(o['outcome']['tokens_burnt']) for o in outs)) / Decimal(10) ** 24}


def gather(store, state: dict[str, Any], *, agent_id: str, gas_lookup=tx_gas) -> dict[str, Any]:
    """Everything the report needs, computed from recorded data plus RPC gas reads."""
    entry_row, exit_row = legs(store)
    if entry_row is None or exit_row is None:
        raise ValueError('the session has not settled both legs yet')
    entry, exit_ = leg_figures(entry_row), leg_figures(exit_row)
    rate = Decimal(state['fx']['usd_inr'])
    near_usd = Decimal(state['near_usd_at_finish'])
    journal = []
    for leg in (entry, exit_):
        for kind in ('decision_tx', 'outcome_tx'):
            g = gas_lookup(leg[kind], agent_id)
            journal.append({'leg': leg['side'], 'kind': kind, 'tx': leg[kind], **g})
    journal_near = sum((j['near_burnt'] for j in journal), Decimal(0))
    deposit_near_each = max(FT_TRANSFER_SAMPLES.values())
    deposit_near = deposit_near_each * 2
    gross = exit_['amount_in_usd'] - entry['amount_out_usd']  # quantity x (exit ref price - entry ref price)
    costs = entry['total_cost_usd'] + exit_['total_cost_usd']
    net_usdc = exit_['usdc'] - entry['usdc']  # what the paper balance (and the contract) sees, before gas
    peg_residual = net_usdc - (gross - costs)  # USDC reference price != 1.000000
    attempts = [{'signal_hash': r['signal_hash'], 'coin': r['coin'], 'side': r['side'], 'state': r['state'],
                 'signal_type': r['data']['signal']['signal_type'], 'result': r['data'].get('result'),
                 'skip_reason': r['data'].get('skip_reason'), 'decision_id': r['data'].get('decision_id'),
                 'decision': r['data'].get('decision'), 'decision_tx': r['data'].get('decision_tx'),
                 'slippage_bps': (r['data'].get('decision_args') or {}).get('slippage_bps')}
                for r in rows(store)]
    return {'entry': entry, 'exit': exit_, 'rate': rate, 'near_usd': near_usd, 'journal_gas': journal,
            'journal_near': journal_near, 'deposit_near_each': deposit_near_each, 'deposit_near': deposit_near,
            'gross_usd': gross, 'costs_usd': costs, 'net_usdc_before_gas': net_usdc, 'peg_residual': peg_residual,
            'attempts': attempts}


def _t(ms: int) -> str:
    utc = datetime.fromtimestamp(ms / 1000, timezone.utc)
    ist = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return f'{utc:%Y-%m-%d %H:%M:%S} UTC ({ist:%H:%M} IST)'


def render_report(g: dict[str, Any], state: dict[str, Any], *, guard: str, network: str) -> str:
    from anchorium.near.guard_client import account_url, tx_url  # noqa: PLC0415
    e, x, rate = g['entry'], g['exit'], g['rate']
    inr = lambda usd: q(usd * rate)  # noqa: E731
    near_usd = g['near_usd']
    paper_inr, paper_usd = Decimal(state['paper_inr']), Decimal(state['paper_usd'])
    cash = paper_usd - e['usdc']
    final_usdc = cash + x['usdc']
    deposit_usd = g['deposit_near'] * near_usd
    net_after_gas = g['net_usdc_before_gas'] - deposit_usd
    sym = 'wNEAR' if e['coin'] == 'NEAR' else {'BTC': 'nBTC', 'ETH': 'ETH'}.get(e['coin'], e['coin'])
    exit_reason = x['signal']['signal_type']
    exit_label = ('TIME_EXIT (max hold reached)' if exit_reason == 'TIME_EXIT'
                  else f'strategy SHORT signal ({exit_reason})')
    held_min = (x['decision_time_ms'] - e['decision_time_ms']) / 60000
    pct = lambda usd: q(usd * rate / paper_inr * 100, '0.001')  # noqa: E731
    L = []
    a = L.append
    a('# Paper trade report: INR 500, one $2 round trip')
    a('')
    a('**Paper trade (DRY_RUN). No real money moved.** The signals are live, the 1Click quotes are real dry quotes, '
      'and every decision is a real transaction on the testnet guard. Fills are simulated at each quote\'s '
      '`amountOut`. Nothing here was tuned or picked after the fact: the session took the first LONG signal the '
      'guard allowed, and the first exit the guard allowed.')
    a('')
    a(f'Generated by `python -m anchorium paper-trade` (session `{state.get("session_label")}`), '
      f'started {state["started_at"]}, finished {state.get("finished_at")}. '
      'Regenerate it from the recorded session with `python -m anchorium paper-trade --report-only`.')
    a('')
    a('## Result')
    a('')
    a('| | USD | INR |')
    a('|---|---:|---:|')
    a(f'| Starting paper balance | ${q(paper_usd, "0.0001")} | ₹{q(paper_inr)} |')
    a(f'| Traded (guard max per trade) | ${q(e["usdc"], "0.000001")} | ₹{inr(e["usdc"])} |')
    a(f'| Kept as cash | ${q(cash, "0.0001")} | ₹{inr(cash)} |')
    a(f'| Gross P&L (price move on the quantity held) | {q(g["gross_usd"], "0.000001"):+} | {inr(g["gross_usd"]):+} |')
    a(f'| 1Click fees + slippage vs reference (both legs) | {-q(g["costs_usd"], "0.000001"):+} | {-inr(g["costs_usd"]):+} |')
    a(f'| USDC reference price ≠ $1 (residual) | {q(g["peg_residual"], "0.000001"):+} | {inr(g["peg_residual"]):+} |')
    a(f'| **Net P&L before gas** (USDC out − USDC in) | **{q(g["net_usdc_before_gas"], "0.000001"):+}** | '
      f'**{inr(g["net_usdc_before_gas"]):+}** |')
    a(f'| Estimated mainnet deposit gas (2 × ft_transfer) | {-q(deposit_usd, "0.000001"):+} | {-inr(deposit_usd):+} |')
    a(f'| **Net P&L after estimated gas** | **{q(net_after_gas, "0.000001"):+}** | **{inr(net_after_gas):+}** |')
    a(f'| **Return on the ₹500 balance** (after gas) | | **{pct(net_after_gas):+}%** |')
    a(f'| Return on the $2 traded (after gas) | {q(net_after_gas / e["usdc"] * 100, "0.001"):+}% | |')
    a(f'| **Final paper balance** (USDC; gas is paid in NEAR) | **${q(final_usdc, "0.0001")}** | **₹{inr(final_usdc)}** |')
    a('')
    pnl = x['realized_pnl_usd_cents']
    a(f'The guard contract computed the realized paper P&L itself from the journaled amounts: '
      f'**{pnl / 100:+.2f} USD** (proceeds minus cost, each converted to whole cents rounding down, with USDC counted as $1; see `get_stats`).' if pnl is not None else
      'The guard contract returned no realized P&L for the exit.')
    a('')
    a('## FX rate used')
    a('')
    fx = state['fx']
    a(f'- **{fx["usd_inr"]} INR per USD**, {fx["source"]} (`{fx["url"]}`), fetched {fx["fetched_at"]}. '
      'All INR figures use this one rate, so the result has no FX effect.')
    cc = fx.get('cross_check') or {}
    if 'usd_inr' in cc:
        a(f'- Cross-check: {cc["usd_inr"]} INR per USD ({cc["source"]}).')
    a(f'- ₹{paper_inr} ÷ {fx["usd_inr"]} = **${paper_usd}** of paper USDC.')
    a('')
    a('## Guard check before trading')
    a('')
    pol = state['policy_at_start']
    a(f'Policy on [`{guard}`]({account_url(network, guard)}) at the start: max '
      f'${pol["max_notional_per_trade_usd_cents"] / 100:.2f} per trade, {pol["max_trades_per_day"]} entries/day, '
      f'daily loss cap ${pol["max_daily_loss_usd_cents"] / 100:.2f}, max slippage {pol["max_slippage_bps"]} bps, '
      f'cooldown {pol["cooldown_secs"]} s, {pol["max_open_positions"]} open position, paused={pol["paused"]}.')
    a('')
    a('| `check_trade` | Notional | Result |')
    a('|---|---:|---|')
    for c in state['guard_precheck']:
        a(f'| BUY {c["coin"]} (`{c["asset"]}`) | ${c["notional_usd_cents"] / 100:.2f} | '
          f'{"ALLOWED" if c["allowed"] else "REJECTED"} ({c["reason"]}) |')
    a('')
    a(f'So the session trades ${state["order_usd"]} and keeps the rest as cash.')
    a('')
    a('## Entry and exit')
    a('')
    a('| | Entry (BUY) | Exit (SELL) |')
    a('|---|---|---|')
    a(f'| Trigger | strategy LONG signal ({e["signal"]["signal_type"]}) | {exit_label} |')
    a(f'| Signal bar (Hyperliquid {e["coin"]} perp, 1m) | {_t(e["signal"]["bar_open_ms"])}, close {e["signal"]["close"]} | '
      f'{_t(x["signal"]["bar_open_ms"])}, close {x["signal"]["close"]} |')
    a(f'| Decision time (on-chain) | {_t(e["decision_time_ms"])} | {_t(x["decision_time_ms"])} |')
    a(f'| 1Click dry quote | `{e["quote_id"]}` | `{x["quote_id"]}` |')
    a(f'| Paid | {e["usdc"]} USDC | {x["quantity"]} {sym} |')
    a(f'| Received (simulated fill = `amountOut`) | {e["quantity"]} {sym} | {x["usdc"]} USDC |')
    a(f'| Execution price | ${q(e["exec_price"], "0.000001")} per {sym} | ${q(x["exec_price"], "0.000001")} per {sym} |')
    a(f'| 1Click reference price | ${q(e["ref_price"], "0.000001")} | ${q(x["ref_price"], "0.000001")} |')
    a(f'| Worst-case slippage checked by the guard | {e["worst_case_bps"]} bps | {x["worst_case_bps"]} bps |')
    a(f'| Holding time | | {held_min:.0f} min |')
    a('')
    a('## Fees, itemized')
    a('')
    a('Costs are measured against the 1Click `/v0/tokens` reference prices fetched right before each quote '
      f'(USDC ${e["usdc_ref_price"]} / ${x["usdc_ref_price"]}; {sym} ${e["ref_price"]} / ${x["ref_price"]} at entry / '
      'exit), so the price move between the legs is not counted as a cost. Those prices are published with few '
      'digits, which limits how finely the fee and the spread can be separated. A negative slippage means the '
      'quote was better than the reference price.')
    a('')
    a('| Item | Entry | Exit | Total USD | Total INR |')
    a('|---|---:|---:|---:|---:|')
    fee_t = e['fee_usd'] + x['fee_usd']
    slip_t = e['slippage_usd'] + x['slippage_usd']
    a(f'| 1Click app fee ({e["fee_bps"]} bps of the input) | {money(e["fee_usd"])} | {money(x["fee_usd"])} | '
      f'{money(fee_t)} | {money(fee_t * rate, "0.0001", "₹")} |')
    a(f'| Slippage / spread vs reference price | {money(e["slippage_usd"])} | {money(x["slippage_usd"])} | '
      f'{money(slip_t)} | {money(slip_t * rate, "0.0001", "₹")} |')
    dep_each_usd = g['deposit_near_each'] * near_usd
    a(f'| Gas, mainnet deposit (estimate: {g["deposit_near_each"]} NEAR each) | ${q(dep_each_usd, "0.000001")} | '
      f'${q(dep_each_usd, "0.000001")} | ${q(deposit_usd, "0.000001")} | ₹{q(deposit_usd * rate, "0.0001")} |')
    a('')
    a(f'Gas in NEAR: estimated mainnet deposits {g["deposit_near"]} NEAR (${q(deposit_usd, "0.000001")}, '
      f'₹{q(deposit_usd * rate, "0.0001")}) at NEAR = ${q(near_usd, "0.0001")} (1Click reference price at the end '
      'of the session). The estimate is the measured gas of real mainnet `wrap.near` `ft_transfer` transactions '
      f'({", ".join(f"[{h[:8]}…](https://nearblocks.io/txns/{h})" for h in FT_TRANSFER_SAMPLES)}); a paper trade sends '
      'no deposit, so it is an estimate, not a measurement. The swap leg itself runs inside 1Click and its cost is '
      'already in the quote.')
    a('')
    a('Journal gas (measured from the testnet transactions below; testnet NEAR has no value, so it is not '
      'subtracted above). If the guard ran on mainnet at the same gas price, it would cost:')
    a('')
    a('| Journal transaction | Gas burnt | NEAR | USD | INR |')
    a('|---|---:|---:|---:|---:|')
    for j in g['journal_gas']:
        usd = j['near_burnt'] * near_usd
        a(f'| {j["leg"]} `{j["kind"].replace("_tx", "")}` [{j["tx"][:8]}…]({tx_url(network, j["tx"])}) | '
          f'{j["gas_burnt"] / 1e12:.3f} Tgas | {j["near_burnt"]} | ${q(usd, "0.000001")} | ₹{q(usd * rate, "0.0001")} |')
    jusd = g['journal_near'] * near_usd
    a(f'| **Total** | | {g["journal_near"]} | ${q(jusd, "0.000001")} | ₹{q(jusd * rate, "0.0001")} |')
    a('')
    a('## On-chain journal entries (testnet)')
    a('')
    a('Every decision this session sent to the guard, including any it rejected:')
    a('')
    a('| # | Signal | Side | Guard verdict | Worst-case slippage | Decision tx | Outcome tx |')
    a('|---|---|---|---|---:|---|---|')
    outcome_tx = {e['decision_id']: e['outcome_tx'], x['decision_id']: x['outcome_tx']}
    for at in g['attempts']:
        if at['decision_id'] is None:
            continue
        d = at['decision'] or {}
        verdict = 'ALLOWED' if d.get('allowed') else f'REJECTED ({d.get("reason")})'
        otx = outcome_tx.get(at['decision_id'])
        a(f'| {at["decision_id"]} | {at["coin"]} {at["signal_type"]} | {at["side"]} | {verdict} | '
          f'{at["slippage_bps"]} bps | [{at["decision_tx"][:8]}…]({tx_url(network, at["decision_tx"])}) | '
          + (f'[{otx[:8]}…]({tx_url(network, otx)})' if otx else '—') + ' |')
    skipped = [at for at in g['attempts'] if at['decision_id'] is None]
    if skipped:
        a('')
        a('Signals skipped before journaling (the guard\'s `check_trade` view already said no, or a spot account '
          'cannot act on them):')
        a('')
        for at in skipped:
            a(f'- {at["coin"]} {at["signal_type"]} {at["side"] or ""}: {at["skip_reason"]}')
    a('')
    a(f'Contract account: [{guard}]({account_url(network, guard)}).')
    a('')
    a('## Method and limits')
    a('')
    a('- **Signals.** The frozen strategy (vendored unchanged; see `vendor/anchorium_strategy/SOURCE.md`) runs on '
      'completed 1-minute Hyperliquid perp candles. Execution is spot on NEAR Intents: a LONG buys, and only a held '
      'position can be sold.')
    a(f'- **Exit rule.** The next SHORT signal on the held coin, or after {state["max_hold_s"] / 3600:g} hours a '
      '`TIME_EXIT` at the latest completed bar. A timer exit is journaled as `TIME_EXIT` with source '
      '`max-hold-timer`, not as a strategy signal. An exit the guard rejects is retried at the next bar, and every '
      'attempt is in the journal above.')
    a('- **Fills.** Simulated at the dry quote\'s `amountOut`. A live swap settles at or above `minAmountOut`, so '
      'a real fill could differ from these figures by up to the worst-case slippage shown.')
    a('- **Sample size.** One round trip says nothing about the strategy\'s edge. It shows the pipeline: signal → '
      'guard check → quote → on-chain decision → outcome, with real costs.')
    a('')
    return '\n'.join(L)
