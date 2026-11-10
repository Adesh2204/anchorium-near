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
from anchorium.near.guard_client import GuardReader, GuardWriter, account_url, load_function_call_signer
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
                f'agent_allowed={a["agent_allowed"]}, agent_reason={a["agent_reason"]})')
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
        trader = Near.from_file(trader_account, 'mainnet')
    config = AgentConfig(network=network, execution=execution, trader_account=trader_account,
                         order_usd=Decimal(os.environ.get('ORDER_USD', '2.00')),
                         quote_tolerance_bps=int(os.environ.get('QUOTE_SLIPPAGE_TOLERANCE_BPS', '10')),
                         session_label=getattr(args, 'session', None))
    oneclick = OneClickClient(os.environ.get('ONECLICK_BASE_URL') or 'https://1click.chaindefuser.com',
                              jwt=os.environ.get('ONECLICK_JWT') or None)
    log = Logger(args.json)
    agent = Agent(config, reader, oneclick, CheckpointStore(store_path), writer=writer, trader_near=trader, log=log)
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
        print('demo runs in DRY_RUN only; unset LIVE'); return 2
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
        print('LIVE flags are set: also pass --i-understand-live to run with real funds'); return 2
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
    args = parser.parse_args(argv)
    return {'demo': cmd_demo, 'status': cmd_status, 'run': cmd_run}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
