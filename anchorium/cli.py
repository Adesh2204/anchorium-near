"""Command line: `python -m anchorium {demo,status,run}`.

    demo            Path A: zero keys. REPLAY signals -> live testnet guard views -> real 1Click dry quotes ->
                    prints the exact record_decision call the agent would sign, plus the contract's real journal.
    demo --onchain  Path B: same, but journals decisions and paper outcomes with the agent's function-call key.
    status          Policy, stats and the latest on-chain decisions (keyless).
    run             Live loop on current public candles (DRY_RUN unless the LIVE gates are all set).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from anchorium.agent import signals as sig
from anchorium.agent.checkpoint import CheckpointStore
from anchorium.agent.orchestrator import Agent, AgentConfig, explorer_summary
from anchorium.near import live as live_gates
from anchorium.near.guard_client import (DEFAULT_RPC, GuardReader, GuardWriter, account_url,
                                         load_function_call_signer)
from anchorium.near.intents import OneClickClient

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GUARD = 'guard.anchorium-team.testnet'
# Path A checks run against a second deployment of the same wasm that nothing journals to, so its state stays
# flat and the demo is deterministic. The real journal is read from DEFAULT_GUARD.
DEFAULT_DEMO_GUARD = 'demo.anchorium-team.testnet'
DEFAULT_FIXTURE = ROOT / 'fixtures/replay/hyperliquid_1m.json'
JOURNAL_LOG = ROOT / 'var/decisions.jsonl'


def utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def render(event: dict) -> str | None:
    e = event['event']
    h = event.get('signal_hash', '')[:10]
    if e == 'signal':
        return (f'[signal]  {event["coin"]} {event["signal_type"]} close {event["close"]} '
                f'(bar {utc(event["bar_open_ms"])}, VWAP {Decimal(event["vwap"]):.4f}, ATR {Decimal(event["atr_14"]):.4f}) '
                f'hash {h}...')
    if e == 'guard_check':
        return (f'[guard]   check_trade {event["side"]} {event["asset"]} ${event["notional_usd_cents"] / 100:.2f} '
                f'-> {event["reason"]}')
    if e == 'quote':
        gate = '<=' if event['worst_case_slippage_bps'] <= event['policy_max_slippage_bps'] else '>'
        return (f'[quote]   1Click {"dry " if event["dry"] else ""}quote {event["amount_in"]} {event["in_symbol"]} -> '
                f'{Decimal(event["amount_out"]):.6f} {event["out_symbol"]} (min {Decimal(event["min_amount_out"]):.6f}); '
                f'worst case {event["worst_case_slippage_bps"]} bps {gate} policy {event["policy_max_slippage_bps"]} bps; '
                f'fee {event["app_fee_bps"]} bps; quote {event["quote_id"]}')
    if e == 'quote_failed':
        return f'[quote]   FAILED: {event["error"]}'
    if e == 'would_journal':
        a = event['args']
        return (f'[journal] (Path A, no key) would call {event["contract"]}.record_decision(side={a["side"]}, '
                f'notional_usd_cents={a["notional_usd_cents"]}, slippage_bps={a["slippage_bps"]}, '
                f'agent_allowed={a["agent_allowed"]}, agent_reason={a["agent_reason"]}'
                f'{", rationale_hash=" + a["rationale_hash"][:16] + "..." if a.get("rationale_hash") else ""})')
    if e == 'decision_journaled':
        verdict = 'ALLOWED' if event['allowed'] else f'REJECTED ({event["reason"]})'
        extra = '  <- contract overrode the agent (verdict_override event)' if event['verdict_override'] else ''
        return f'[journal] decision #{event["decision_id"]} {verdict}{extra}\n          {event["explorer"]}'
    if e == 'outcome_journaled':
        pnl = event['realized_pnl_usd_cents']
        pnl_text = '' if pnl is None else f', realized PnL {pnl / 100:+.2f} USD{" (paper)" if event["paper"] else ""}'
        paused = '  <- daily loss cap hit: contract auto-paused' if event['auto_paused'] else ''
        return f'[outcome] decision #{event["decision_id"]} {event["status"]}{pnl_text}{paused}\n          {event["explorer"]}'
    if e == 'intent_status':
        return f'[intent]  {event["status"]}: {event["funds_location"]}'
    if e == 'deposit_sent':
        return f'[deposit] {event["amount"]} {event["symbol"]} -> {event["deposit_address"]}\n          {event["explorer"]}'
    if e == 'ai_review':
        return (f'[ai]      co-pilot ({event["provider"]}): {event["verdict"]} risk {event["risk_score"]}/100 '
                f'{event["reason_code"]}\n          {event["explanation"]}\n'
                f'          rationale_hash {event["rationale_hash"]}')
    if e == 'skipped':
        return f'[skip]    {event["reason"]}'
    if e == 'already_processed':
        return f'[skip]    already processed ({event["state"]}) {event.get("decision_tx_url") or ""}'
    return None


class Logger:
    def __init__(self, json_mode: bool):
        self.json_mode = json_mode
        JOURNAL_LOG.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: dict) -> None:
        event = {'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'), **event}
        line = json.dumps(event, separators=(',', ':'), default=str)
        with JOURNAL_LOG.open('a') as f:
            f.write(line + '\n')
        if self.json_mode:
            print(line, flush=True)
        else:
            text = render(event)
            if text:
                print(text, flush=True)


def build(args, *, onchain: bool, store_path: str, guard_id: str | None = None):
    load_dotenv(ROOT / '.env')
    execution = live_gates.ExecutionConfig.from_env(os.environ)
    guard_id = guard_id or os.environ.get('GUARD_CONTRACT_ID') or DEFAULT_GUARD
    network = os.environ.get('NEAR_NETWORK', 'testnet')
    reader = GuardReader(guard_id, network, rpc_url=os.environ.get('NEAR_RPC_URL') or None)
    writer = trader = None
    if onchain:
        agent_id = os.environ.get('AGENT_ACCOUNT_ID') or reader.roles()['agent']
        writer = GuardWriter(reader, load_function_call_signer(agent_id, guard_id, network))
    trader_account = os.environ.get('TRADER_ACCOUNT_ID') or None
    if execution.live:
        from near import Near  # noqa: PLC0415
        trader = Near.from_file(trader_account, 'mainnet', rpc_url=DEFAULT_RPC['mainnet'])
    config = AgentConfig(network=network, execution=execution, trader_account=trader_account,
                         order_usd=Decimal(os.environ.get('ORDER_USD', '2.00')),
                         quote_tolerance_bps=int(os.environ.get('QUOTE_SLIPPAGE_TOLERANCE_BPS', '10')),
                         session_label=getattr(args, 'session', None))
    oneclick = OneClickClient(os.environ.get('ONECLICK_BASE_URL') or 'https://1click.chaindefuser.com',
                              jwt=os.environ.get('ONECLICK_JWT') or None)
    log = Logger(args.json)
    confirm = live_gates.require_typed_confirmation
    if execution.live:  # the human must type each confirmation: get their attention, show the quote deadline
        from anchorium.notify import notifying_confirm  # noqa: PLC0415
        confirm = notifying_confirm(confirm, f'{getattr(args, "leg", None) or "deposit"} leg')
    agent = Agent(config, reader, oneclick, CheckpointStore(store_path), writer=writer, trader_near=trader,
                  confirm=confirm, log=log)
    return agent, reader, config, oneclick


def header(reader: GuardReader, config: AgentConfig, oneclick: OneClickClient, path: str) -> None:
    policy, roles = reader.policy(), reader.roles()
    print(f'=== Anchorium {path} | {config.execution.banner} ===')
    print(f'guard contract : {reader.contract_id} ({reader.network})  {account_url(reader.network, reader.contract_id)}')
    print(f'owner / agent  : {roles["owner"]} / {roles["agent"]}')
    print(f'code_hash      : {reader.code_hash()} (compare with contracts/anchorium-guard/res/SHA256SUMS)')
    print(f'policy         : max ${policy["max_notional_per_trade_usd_cents"] / 100:.2f}/trade, '
          f'{policy["max_trades_per_day"]} entries/day, daily loss cap ${policy["max_daily_loss_usd_cents"] / 100:.2f}, '
          f'max slippage {policy["max_slippage_bps"]} bps, cooldown {policy["cooldown_secs"]} s, '
          f'paused={policy["paused"]}')
    print(f'1Click         : {"JWT" if oneclick.authenticated else "no JWT (observed fee 20 bps; docs say 0.25%)"}')
    print()


def cmd_demo(args) -> int:
    onchain = args.onchain
    store = str(ROOT / 'var/agent-demo-onchain.sqlite') if onchain else ':memory:'
    load_dotenv(ROOT / '.env')
    check_guard = None if onchain else (os.environ.get('DEMO_GUARD_CONTRACT_ID') or DEFAULT_DEMO_GUARD)
    agent, reader, config, oneclick = build(args, onchain=onchain, store_path=store, guard_id=check_guard)
    journal = reader if onchain else GuardReader(os.environ.get('GUARD_CONTRACT_ID') or DEFAULT_GUARD, reader.network)
    if config.execution.live:
        print('demo runs in DRY_RUN only; unset LIVE')
        return 2
    header(reader, config, oneclick, 'Path B: journaled on-chain' if onchain else 'Path A: zero keys, zero funds')
    cases = {c.name: c for c in sig.load_replay(Path(args.fixture))}
    names = args.cases.split(',') if args.cases else (['near-entry', 'near-exit'] if onchain else
                                                      ['near-entry', 'btc-entry', 'eth-entry'])
    for name in names:
        case = cases[name]
        signal, _ = sig.evaluate_latest(case.rows, case.coin, case.now_ms)
        print(f'--- REPLAY {name}: recorded bar closing {utc(case.now_ms - 1)} ---')
        agent.process(signal)
        print()
    if not args.no_compromised:
        case = cases[names[0]]
        signal, _ = sig.evaluate_latest(case.rows, case.coin, case.now_ms)
        print('--- Simulated compromised agent: asks for 2.5x the cap and claims it is allowed ---')
        agent.process(signal, compromised=True)
        print()
    summary = explorer_summary(journal)
    if not onchain:
        print(f'Checks above ran against {reader.contract_id} (same code_hash, never journaled: total_decisions='
              f'{reader.stats()["total_decisions"]}).')
    print(f'Real on-chain journal written by the agent ({summary["contract"]}): {summary["explorer"]}')
    for d in summary['latest_decisions']:
        print(f'  #{d["id"]} {d["mode"]} {d["side"]} {d["asset"]} -> '
              f'{"ALLOWED" if d["allowed"] else "REJECTED " + d["reason"]}')
    stats = journal.stats()
    print(f'Stats read from the contract: decisions={stats["total_decisions"]}, paper closed trades='
          f'{stats["dry_run"]["closed_trades"]} (wins {stats["dry_run"]["wins"]}, losses {stats["dry_run"]["losses"]}, '
          f'PnL {stats["dry_run"]["realized_pnl_usd_cents"] / 100:+.2f} USD paper), live trades={stats["live"]["closed_trades"]}')
    return 0


def cmd_status(args) -> int:
    load_dotenv(ROOT / '.env')
    reader = GuardReader(os.environ.get('GUARD_CONTRACT_ID') or DEFAULT_GUARD, os.environ.get('NEAR_NETWORK', 'testnet'))
    print(json.dumps({'policy': reader.policy(), 'roles': reader.roles(), 'stats': reader.stats(),
                      'positions': reader.positions(), **explorer_summary(reader, 10)}, indent=2))
    return 0


def cmd_run(args) -> int:
    agent, reader, config, oneclick = build(args, onchain=True,
                                            store_path=str(ROOT / f'var/agent-{"live" if args.live_ok else "dry"}.sqlite'))
    if config.execution.live and not args.live_ok:
        print('LIVE flags are set: also pass --i-understand-live to run with real funds')
        return 2
    parked = agent.store.recover()
    for row in parked:
        print(f'NEEDS_REVIEW {row["signal_hash"]}: {row["data"]["review_reason"]}')
    header(reader, config, oneclick, 'agent loop (live public candles)')
    feed = sig.HyperliquidCandles()
    coins = args.coins.split(',')
    for row in agent.store.unfinished():
        if row['state'] != 'NEEDS_REVIEW':
            agent.process(sig.Signal(**row['data']['signal']))
    while True:
        now = int(time.time() * 1000)
        for coin in coins:
            try:
                signal, info = sig.evaluate_latest(feed.fetch(coin, now), coin, now)
            except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the loop
                print(f'[feed]    {coin}: {type(exc).__name__}: {exc}')
                continue
            if signal:
                agent.process(signal)
            elif args.verbose:
                print(f'[scan]    {coin} {info["status"]} close {info.get("close")}')
        if args.once:
            return 0
        time.sleep(60 - (time.time() % 60) + 2)  # just after the next 1m bar closes


def latest_bar_signal(coin: str, signal_type: str) -> sig.Signal:
    """A human-ordered signal at the latest completed bar (journaled as such; never a strategy signal)."""
    return sig.latest_bar_signal(sig.HyperliquidCandles(), coin, signal_type, source='human-ordered-exit')


def cmd_exit(args) -> int:
    """Human-ordered exit of the open position on --coin (journaled with signal_type MANUAL_EXIT)."""
    load_dotenv(ROOT / '.env')
    mode = 'live' if live_gates.ExecutionConfig.from_env(os.environ).live else 'dry'
    agent, reader, config, oneclick = build(args, onchain=True, store_path=str(ROOT / f'var/agent-{mode}.sqlite'))
    header(reader, config, oneclick, f'manual exit {args.coin}')
    if config.execution.live and live_ledger(agent.store)['blocked']:
        print(live_ledger(agent.store)['blocked'])
        return 1
    row = agent.process(latest_bar_signal(args.coin, 'MANUAL_EXIT'))
    return report_live(row) if config.execution.live else 0


def live_ledger(store) -> dict:
    """The controlled micro-trade budget: one entry, one exit, stop on any refund/failure/review."""
    rows = store.db.execute("SELECT side, state, data FROM runs WHERE mode = 'LIVE'").fetchall()
    entries = exits = 0
    blocked = None
    for side, state, data in rows:
        data = json.loads(data)
        result = data.get('result')
        if result == 'FAILED' and str((data.get('outcome_args') or {}).get('tx_ref', '')).startswith(
                'aborted-before-deposit'):
            continue  # the human did not confirm (or the quote expired): nothing was sent, the leg is not used
        sent = state in ('DEPOSIT_PENDING', 'DEPOSITED', 'OUTCOME_PENDING', 'NEEDS_REVIEW') or (
            state == 'DONE' and result not in ('REJECTED', None))
        if sent and side == 'BUY':
            entries += 1
        if sent and side == 'SELL':
            exits += 1
        if state == 'NEEDS_REVIEW' or result in ('REFUNDED', 'FAILED'):
            blocked = f'STOPPED: a LIVE trade ended {result or state}. Report and get a human decision before any retry.'
    return {'entries': entries, 'exits': exits, 'blocked': blocked}


def report_live(row) -> int:
    data = row['data']
    print(f"\nLIVE result: state={row['state']} result={data.get('result')} decision #{data.get('decision_id')}")
    for key in ('decision_tx_url', 'deposit_tx_url'):
        if data.get(key):
            print(f'  {key}: {data[key]}')
    for step in data.get('timeline') or []:
        print(f"  {step['status']}: {step['funds_location']}")
    return 0 if data.get('result') == 'SUCCESS' else 1


def cmd_live_trade(args) -> int:
    """Controlled micro-trade: --leg entry waits for a live LONG signal; --leg exit waits for a SHORT signal on the
    position (or --manual). Refuses a second entry/exit and stops after any REFUNDED/FAILED."""
    agent, reader, config, oneclick = build(args, onchain=True, store_path=str(ROOT / 'var/agent-live.sqlite'))
    if not config.execution.live:
        print('live-trade needs LIVE=1 DRY_RUN=0 MAX_LIVE_NOTIONAL_USD<=2 TRADER_ACCOUNT_ID=<mainnet account>')
        return 2
    for row in agent.store.recover():
        print(f"NEEDS_REVIEW {row['signal_hash']}: {row['data']['review_reason']}")
    ledger = live_ledger(agent.store)
    if ledger['blocked']:
        print(ledger['blocked'])
        return 1
    if (args.leg == 'entry' and ledger['entries'] >= 1) or (args.leg == 'exit' and ledger['exits'] >= 1):
        print(f'REFUSED: the {args.leg} of this micro-trade was already used ({ledger}).')
        return 1
    header(reader, config, oneclick, f'LIVE micro-trade: {args.leg}')
    for row in agent.store.unfinished():
        if row['state'] in ('DEPOSITED', 'OUTCOME_PENDING'):
            print('resuming an unfinished LIVE trade')
            return report_live(agent.process(sig.Signal(**row['data']['signal'])))
    coins = args.coins.split(',')
    if args.leg == 'exit':
        held = [p for p in reader.positions() if p['mode'] == 'LIVE' and p['state'] == 'OPEN']
        if not held:
            print('no open LIVE position to exit')
            return 1
        coins = [c for c, a in sig.SPOT_ASSET.items() if a == held[0]['asset']]
        if args.manual:
            return report_live(agent.process(latest_bar_signal(coins[0], 'MANUAL_EXIT')))
        if args.exit_rule == 'PROFIT_TARGET':
            return live_profit_exit(args, agent, reader, held[0], coins[0])
    want = 'LONG' if args.leg == 'entry' else 'SHORT'
    feed, deadline = sig.HyperliquidCandles(), time.time() + args.wait_minutes * 60
    print(f'waiting up to {args.wait_minutes} min for a live {want} signal on {",".join(coins)} ...', flush=True)
    while time.time() < deadline:
        now = int(time.time() * 1000)
        for coin in coins:
            try:
                signal, info = sig.evaluate_latest(feed.fetch(coin, now), coin, now)
            except Exception as exc:  # noqa: BLE001 - one bad candle read must not end a long wait
                print(f'[feed]    {coin}: {type(exc).__name__}: {exc}', flush=True)
                continue
            if signal and signal.direction == want:
                row = agent.process(signal)
                if row['state'] == 'SKIPPED' or row['data'].get('result') == 'REJECTED':
                    continue  # the guard said no: keep waiting for the next signal
                return report_live(row)
        time.sleep(60 - (time.time() % 60) + 2)
    print('no qualifying signal before the wait limit; nothing was sent')
    return 1


def live_profit_exit(args, agent, reader, position, coin) -> int:
    """Waits for the profit floor (see anchorium/live_exit.py), then runs the LIVE exit with the typed confirmation."""
    from anchorium import live_exit  # noqa: PLC0415
    from anchorium.notify import notify  # noqa: PLC0415
    until_ms = int(datetime.fromisoformat(args.until).timestamp() * 1000)
    token = agent.oneclick.token(position['asset'])
    storage_min = Decimal(int(agent.trader_near.view(token.contract_address, 'storage_balance_bounds')['min'])) / 10 ** 24

    def amount_units() -> int:  # never more than the account holds
        held = int(agent.trader_near.ft_balance(token.contract_address, agent.config.trader_account))
        return min(int(position['amount']), held)
    print(f'exit rule PROFIT_TARGET: sell only when the guaranteed quote output covers the USDC paid, the measured '
          f'NEAR cost of the entry deposit, an estimate for the exit deposit and +${args.target_usd}. '
          f'Waiting until {args.until}; nothing is sold after that without --manual.', flush=True)
    row = live_exit.wait_and_exit(
        agent, coin=coin, amount_units=amount_units, target_usd=Decimal(args.target_usd), until_ms=until_ms,
        storage_min_near=storage_min, notifier=notify,
        exit_signal=lambda c, kind: sig.latest_bar_signal(sig.HyperliquidCandles(), c, kind, source='profit-target'))
    if row is None:
        print(f'profit target not reached by {args.until}: nothing was sold. To exit at market: '
              'live-trade --leg exit --manual')
        return 1
    return report_live(row)


PAPER_DIR = ROOT / 'var/paper_trade'
PAPER_REPORT = ROOT / 'docs/paper_trade_report.md'


def cmd_paper_trade(args) -> int:
    """One paper round trip (DRY_RUN): live signals, real dry quotes, real testnet journal, paper fills."""
    from anchorium import paper_trade as pt  # noqa: PLC0415
    load_dotenv(ROOT / '.env')
    if live_gates.ExecutionConfig.from_env(os.environ).live:
        print('paper-trade is DRY_RUN only; unset LIVE')
        return 2
    args.session = args.session or 'paper-trade'
    agent, reader, config, oneclick = build(args, onchain=True, store_path=str(PAPER_DIR / f'{args.session}.sqlite'))
    session = pt.Session(PAPER_DIR / f'{args.session}.json')
    if args.report_only:
        return write_paper_report(agent, reader, session, args.report)
    header(reader, config, oneclick, f'paper trade (session {args.session})')
    cutoff_ms = None
    if args.cutoff:
        cutoff_ms = int(datetime.fromisoformat(args.cutoff).timestamp() * 1000)
    pt.run(agent, reader, coins=args.coins.split(','), session=session, paper_inr=Decimal(args.inr),
           max_hold_s=int(args.max_hold_hours * 3600), exit_rule=args.exit_rule,
           target_net_usd=Decimal(args.target_usd), cutoff_ms=cutoff_ms)
    return write_paper_report(agent, reader, session, args.report)


def write_paper_report(agent, reader, session, report: str | None = None) -> int:
    from anchorium import paper_trade as pt  # noqa: PLC0415
    path = ROOT / report if report else PAPER_REPORT
    state = session.load()
    data = pt.gather(agent.store, state, agent_id=reader.roles()['agent'])
    path.write_text(pt.render_report(data, state, guard=reader.contract_id, network=reader.network))
    print(f'report written: {path.relative_to(ROOT)}')
    return 0


def cmd_setup_trader(args) -> int:
    """Registers the mainnet trader account on the USDC and wNEAR token contracts (NEP-145 storage_deposit)."""
    from near import Amount, Near  # noqa: PLC0415
    load_dotenv(ROOT / '.env')
    account = os.environ.get('TRADER_ACCOUNT_ID')
    if not account:
        print('set TRADER_ACCOUNT_ID')
        return 2
    near = Near.from_file(account, 'mainnet', rpc_url=DEFAULT_RPC['mainnet'])
    print(f'trader {account}: {near.account(account).amount} on mainnet')
    for token in ('17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1', 'wrap.near'):
        if near.view(token, 'storage_balance_of', {'account_id': account}) is not None:
            print(f'  {token}: already registered')
            continue
        minimum = near.view(token, 'storage_balance_bounds')['min']
        result = near.call(token, 'storage_deposit', {'account_id': account, 'registration_only': True},
                           deposit=Amount.yocto(int(minimum)), gas='30 Tgas')
        print(f'  {token}: registered ({int(minimum) / 1e24} NEAR) https://nearblocks.io/txns/{result.transaction_hash}')
    return 0


AUTO_DIR = ROOT / 'var/autonomous'


def _auto_paths(session: str) -> tuple[Path, Path]:
    return AUTO_DIR / f'{session}.json', AUTO_DIR / f'{session}.sqlite'


def cmd_autonomous(args) -> int:
    """Version A: fully autonomous long-only NEAR rounds (DRY_RUN unless LIVE=1 + the owner types GO LIVE)."""
    from dataclasses import replace  # noqa: PLC0415

    from anchorium import autonomous as A  # noqa: PLC0415
    from anchorium import paper_trade as pt  # noqa: PLC0415
    from anchorium.notify import notify  # noqa: PLC0415
    load_dotenv(ROOT / '.env')
    execution = live_gates.ExecutionConfig.from_env(os.environ)
    live = execution.live
    session = args.session or ('live-a' if live else 'paper-a')
    state_path, store_path = _auto_paths(session)
    if live:
        print('LIVE autonomous trading: real mainnet swaps, no per-trade confirmation, only inside the limits '
              '($2/trade, 1 open round, 3 rounds, INR 20 loss cap).')
        if not sys.stdin.isatty() or input('Type GO LIVE to start: ').strip() != 'GO LIVE':
            print('not confirmed: nothing started')
            return 2
    args.session = session
    agent, reader, config, oneclick = build(args, onchain=True, store_path=str(store_path))
    config.paper_fill_worst_case = True
    config.live_total_entries_only = True
    config.order_usd = Decimal('2.00')
    if live:
        config.execution = replace(execution, max_total_usd=live_gates.AUTONOMOUS_MAX_ENTRIES_USD)
        agent.confirm = lambda *a, **k: None  # the owner's GO LIVE covers every trade inside the limits
    until = int(datetime.fromisoformat(args.until).timestamp() * 1000) if args.until else None
    cfg = A.AutoConfig(session=session, live=live, profit_target_inr=Decimal(os.environ.get('PROFIT_TARGET_INR', '1.00')),
                       cost_filter=Decimal(os.environ.get('COST_FILTER_PCT', '1.6')) / 100,
                       run_until_ms=until, stop_after_closed=args.stop_after_rounds,
                       report_path='docs/live_trade_report.md' if live else args.report)
    lock = A.SessionLock(AUTO_DIR / 'LOCK')
    try:
        lock.acquire()  # before touching the saved state: never edit a running session's file
    except A.HardStop as exc:
        print(f'HARD STOP: {exc}')
        return 3
    sf = A.StateFile(state_path)
    st = sf.load()
    if st and args.resume_exit:
        for r in st['rounds']:
            if r['status'] == 'OPEN' and r.get('exit_blocked'):
                r['exit_blocked'], r['reject_since_ms'] = False, None
        sf.save(st)
    if st and args.clear_hard_stop and st.get('hard_stop'):
        st['events'].append({'at': A.ist(int(time.time() * 1000)), 'event': 'HARD_STOP_CLEARED',
                             'text': f'owner cleared: {st["hard_stop"]}'})
        st['hard_stop'], st['finished'] = None, None
        sf.save(st)
    if st and st.get('finished') and not st.get('hard_stop'):
        st['finished'] = None  # re-running a finished session resumes it (stop conditions are re-checked)
        sf.save(st)
    trader_near = agent.trader_near if live else None
    runner = A.Runner(cfg, agent, A.Market(), A.Wallet(live, trader_near, config.trader_account), sf,
                      fetch_fx=pt.fetch_fx, notify=lambda t, m: notify(t, m, repeat=1), root=ROOT)
    runner.install_sigint()
    try:
        header(reader, config, oneclick, f'autonomous version A (session {session})')
        reason = runner.run()
    finally:
        lock.release()
    print(f'\nstopped: {reason}\nreport: {cfg.report_path}\nstate: {state_path.relative_to(ROOT)}')
    return 3 if reason.startswith('HARD_STOP') else 0


def cmd_autonomous_status(args) -> int:
    from types import SimpleNamespace  # noqa: PLC0415

    from anchorium import autonomous as A  # noqa: PLC0415
    from anchorium import paper_trade as pt  # noqa: PLC0415
    load_dotenv(ROOT / '.env')
    session = args.session or ('live-a' if live_gates.ExecutionConfig.from_env(os.environ).live else 'paper-a')
    state_path, _ = _auto_paths(session)
    sf = A.StateFile(state_path)
    st = sf.load()
    if st is None:
        print(f'no session state at {state_path.relative_to(ROOT)}')
        return 1
    live_line = None
    r = next((x for x in st['rounds'] if x['status'] == 'OPEN'), None)
    if r and not args.offline:  # a fresh read-only valuation; never writes the running session's state
        class ReadOnly(A.StateFile):
            def save(self, state): pass
        cfg = A.AutoConfig(session=session, live=st['mode'] == 'LIVE',
                           profit_target_inr=Decimal(st['config']['profit_target_inr']),
                           time_exit_ms=int(st['config']['time_exit_ms']))
        agent = SimpleNamespace(oneclick=OneClickClient(), config=SimpleNamespace(quote_tolerance_bps=10))
        ro = A.Runner(cfg, agent, A.Market(), A.Wallet(False), ReadOnly(state_path), fetch_fx=pt.fetch_fx,
                      notify=lambda t, m: None, print_=lambda s: None)
        ro.state = st
        try:
            ro.status_line(r)
            live_line = 'now: ' + st['last_status']['line']
        except Exception as exc:  # noqa: BLE001
            live_line = f'now: could not quote ({type(exc).__name__}: {exc})'
    print(A.status_text(st, live_line))
    return 0


def cmd_live_preflight(args) -> int:
    from anchorium.preflight import run_preflight  # noqa: PLC0415
    load_dotenv(ROOT / '.env')
    return run_preflight(os.environ, AUTO_DIR / 'LOCK')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='anchorium')
    parser.add_argument('--json', action='store_true', help='print raw JSON events instead of readable lines')
    sub = parser.add_subparsers(dest='cmd', required=True)
    demo = sub.add_parser('demo')
    demo.add_argument('--onchain', action='store_true', help='Path B: journal with the agent function-call key')
    demo.add_argument('--fixture', default=str(DEFAULT_FIXTURE))
    demo.add_argument('--cases', help='comma-separated replay case names')
    demo.add_argument('--session', help='label mixed into signal hashes (a distinct demo session)')
    demo.add_argument('--no-compromised', action='store_true')
    sub.add_parser('status')
    run = sub.add_parser('run')
    run.add_argument('--coins', default='BTC,ETH,NEAR')
    run.add_argument('--once', action='store_true')
    run.add_argument('--verbose', action='store_true')
    run.add_argument('--i-understand-live', dest='live_ok', action='store_true')
    exit_ = sub.add_parser('exit', help='human-ordered exit of the open position (journaled as MANUAL_EXIT)')
    exit_.add_argument('--coin', required=True, choices=sorted(sig.SPOT_ASSET))
    lt = sub.add_parser('live-trade', help='controlled LIVE micro-trade: one entry, one exit')
    lt.add_argument('--leg', required=True, choices=['entry', 'exit'])
    lt.add_argument('--coins', default='NEAR', help='the trader account is registered on USDC and wNEAR only')
    lt.add_argument('--manual', action='store_true', help='exit now at market (overrides --exit-rule)')
    lt.add_argument('--exit-rule', choices=('PROFIT_TARGET', 'SIGNAL'), default='PROFIT_TARGET',
                    help='exit: wait for the profit floor (default) or for a strategy SHORT signal')
    lt.add_argument('--target-usd', default='0.01', help='PROFIT_TARGET: net profit after every cost')
    lt.add_argument('--until', default='2026-11-13T20:00:00+05:30', help='PROFIT_TARGET: stop waiting at this time')
    lt.add_argument('--wait-minutes', type=int, default=120)
    sub.add_parser('setup-trader', help='register the mainnet trader account on USDC and wNEAR')
    pt = sub.add_parser('paper-trade', help='one DRY_RUN round trip with a paper INR balance, then a report')
    pt.add_argument('--inr', default='500', help='paper balance in INR (converted at the live USD/INR rate)')
    pt.add_argument('--coins', default='NEAR,BTC,ETH')
    pt.add_argument('--max-hold-hours', type=float, default=4.0)
    pt.add_argument('--session', help='session name (separate state + checkpoint store); default paper-trade')
    pt.add_argument('--report-only', action='store_true', help='regenerate the report from the recorded session')
    pt.add_argument('--exit-rule', choices=('SIGNAL_OR_TIME', 'PROFIT_TARGET'), default='SIGNAL_OR_TIME',
                    help='PROFIT_TARGET: sell only when the guaranteed quote output covers cost, gas and --target-usd')
    pt.add_argument('--target-usd', default='0.01', help='PROFIT_TARGET: minimum net profit after every cost')
    pt.add_argument('--cutoff', help='PROFIT_TARGET: ISO time to stop and report (e.g. 2026-11-14T18:00:00+05:30)')
    pt.add_argument('--report', help='report path relative to the repo (default docs/paper_trade_report.md)')
    au = sub.add_parser('autonomous', help='version A: autonomous long-only NEAR rounds (DRY_RUN unless LIVE=1)')
    au.add_argument('--session', help='session name (default paper-a, or live-a when LIVE=1)')
    au.add_argument('--until', help='stop the session at this ISO time (an open round is reported open)')
    au.add_argument('--stop-after-rounds', type=int, help='stop after this many closed rounds')
    au.add_argument('--report', default='docs/paper_trade_A_report.md', help='paper report path')
    au.add_argument('--resume-exit', action='store_true', help='retry a guard-blocked stop/time exit for 30 more min')
    au.add_argument('--clear-hard-stop', action='store_true', help='owner: clear a recorded hard stop and resume')
    ast = sub.add_parser('autonomous-status', help='version A session status (+ a fresh valuation of an open round)')
    ast.add_argument('--session')
    ast.add_argument('--offline', action='store_true', help='no network: saved state only')
    sub.add_parser('live-preflight', help='PASS/FAIL checks before a LIVE autonomous session')
    pp = sub.add_parser('paypal-demo', help='PayPal AI edition: PayPal sandbox top-up + AI co-pilot + guard demo')
    pp.add_argument('--topup', default='10.00', help='top-up amount in USD (sandbox), 1.00 to 50.00')
    pp.add_argument('--order-id', help='capture a PayPal sandbox order the buyer already approved')
    pp.add_argument('--skip-topup', action='store_true', help='use the existing ledger balance')
    pp.add_argument('--no-wait', action='store_true', help='print the approve link and exit')
    pp.add_argument('--wait-seconds', type=int, default=300, help='how long to wait for the buyer to approve')
    pp.add_argument('--fixture', default=str(DEFAULT_FIXTURE))
    pp.add_argument('--cases', help='comma-separated replay case names')
    pp.add_argument('--no-compromised', action='store_true')
    args = parser.parse_args(argv)
    if args.cmd == 'paypal-demo':
        from anchorium.paypal_demo import cmd_paypal_demo  # noqa: PLC0415
        return cmd_paypal_demo(args)
    return {'autonomous': cmd_autonomous, 'autonomous-status': cmd_autonomous_status,
            'live-preflight': cmd_live_preflight, 'demo': cmd_demo, 'status': cmd_status, 'run': cmd_run, 'exit': cmd_exit, 'live-trade': cmd_live_trade,
            'setup-trader': cmd_setup_trader, 'paper-trade': cmd_paper_trade}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
