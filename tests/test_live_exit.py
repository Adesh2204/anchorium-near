"""LIVE micro-trade exit: per-trade cap on exits, aborted confirmations, the profit floor, the human alert.
No network and nothing signed."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from anchorium import live_exit, notify
from anchorium.agent.checkpoint import CheckpointStore
from anchorium.cli import live_ledger
from anchorium.near.live import ExecutionConfig, LiveGateError, check_live_trade
from anchorium.paper_trade import FT_TRANSFER_SAMPLES

LIVE = ExecutionConfig(True, Decimal('2'))


def test_exit_cap_applies_to_the_entry_cost_and_the_total_still_counts_the_exit():
    check_live_trade(LIVE, Decimal('2.02'), Decimal('2'), guard_allowed=True, entry_cost_usd=Decimal('2'))
    with pytest.raises(LiveGateError, match='per-trade cap'):
        check_live_trade(LIVE, Decimal('2.02'), Decimal('0'), guard_allowed=True)  # an entry above $2: refused
    with pytest.raises(LiveGateError, match='entry cost'):
        check_live_trade(LIVE, Decimal('2.02'), Decimal('0'), guard_allowed=True, entry_cost_usd=Decimal('2.01'))
    with pytest.raises(LiveGateError, match='session total'):
        check_live_trade(LIVE, Decimal('3.01'), Decimal('2'), guard_allowed=True, entry_cost_usd=Decimal('2'))


def store_with(tmp_path, rows):
    store = CheckpointStore(tmp_path / 'live.sqlite')
    for i, (side, state, data) in enumerate(rows):
        h = f'{i:064x}'
        store.create(h, 'LIVE', 'NEAR', {'signal': {}})
        store.advance(h, state, side=side, **data)
    return store


def test_an_unconfirmed_leg_neither_blocks_nor_uses_the_micro_trade(tmp_path):
    aborted = {'result': 'FAILED', 'outcome_args': {'tx_ref': 'aborted-before-deposit:QUOTE_EXPIRED'}}
    assert live_ledger(store_with(tmp_path, [('BUY', 'DONE', aborted)])) == {'entries': 0, 'exits': 0, 'blocked': None}


def test_a_failed_swap_still_blocks(tmp_path):
    failed = {'result': 'FAILED', 'outcome_args': {'tx_ref': 'Hx'}}
    assert live_ledger(store_with(tmp_path, [('BUY', 'DONE', failed)]))['blocked'].startswith('STOPPED')


def entry_row(before=None, after=None):
    return {'data': {'outcome_args': {'amount_in': '2000000'}, 'near_yocto_before': before,
                     'near_yocto_after': after, 'quote': {'quoteRequest': {'destinationAsset': 'nep141:wrap.near'}}}}


def test_floor_uses_the_measured_entry_cost_and_never_less_for_the_exit():
    measured = entry_row(str(10 ** 24), str(10 ** 24 - 1_500_000_000_000_000_000_000))  # 0.0015 NEAR spent
    f = live_exit.floor_parts(measured, Decimal('4'), Decimal('0.01'), Decimal('0.00125'))
    estimate = Decimal('0.00125') + max(FT_TRANSFER_SAMPLES.values())
    exit_near = max(Decimal('0.0015'), estimate)
    assert f['entry_near_measured'] and Decimal(f['entry_near']) == Decimal('0.0015')
    assert Decimal(f['floor_usdc']) == Decimal(2) + (Decimal('0.0015') + exit_near) * 4 + Decimal('0.01')
    unmeasured = live_exit.floor_parts(entry_row(), Decimal('4'), Decimal('0.01'), Decimal('0.00125'))
    assert not unmeasured['entry_near_measured'] and Decimal(unmeasured['entry_near']) == estimate


def test_wait_and_exit_sells_only_on_a_clearing_quote_and_gives_up_at_the_deadline(tmp_path, monkeypatch):
    store = store_with(tmp_path, [('BUY', 'DONE', {'result': 'SUCCESS', **entry_row()['data']})])
    results = iter([False, False, True])
    monkeypatch.setattr(live_exit, 'check', lambda *a: {'at': 't', 'amount_out': '2', 'min_amount_out': '2',
                        'floor_usdc': '2.02', 'paid_usdc': '2', 'near_cost_usd': '0.01', 'target_usd': '0.01',
                        'net_worst_case': '0', 'floor_units': 2_020_000, 'clears': next(results)})
    processed, alerts, now = [], [], [1000.0]
    agent = SimpleNamespace(store=store, process=lambda s, **kw: processed.append(kw) or {'state': 'DONE', 'data': {'result': 'SUCCESS'}})

    def sleep(s):
        now[0] += s
    kw = dict(coin='NEAR', amount_units=lambda: 1, target_usd=Decimal('0.01'), storage_min_near=Decimal('0.00125'),
              exit_signal=lambda c, k: k, notifier=lambda *a: alerts.append(a), print_=lambda *a, **k: None,
              sleep=sleep, clock=lambda: now[0])
    row = live_exit.wait_and_exit(agent, until_ms=10 ** 9 * 1000, **kw)
    assert row['data']['result'] == 'SUCCESS' and processed == [{'min_proceeds_units': 2_020_000}] and not alerts
    monkeypatch.setattr(live_exit, 'check', lambda *a: {**dict.fromkeys(('at', 'amount_out', 'min_amount_out',
                        'paid_usdc', 'target_usd'), '0'), 'floor_usdc': '1', 'near_cost_usd': '0',
                        'net_worst_case': '-1', 'floor_units': 1, 'clears': False})
    assert live_exit.wait_and_exit(agent, until_ms=int((now[0] + 600) * 1000), **kw) is None
    assert len(processed) == 1 and alerts  # nothing more sold; the human was told


def test_the_human_is_alerted_with_the_quote_deadline_before_the_prompt():
    alerts, seen = [], {}
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    wrapped = notify.notifying_confirm(lambda phrase, details: seen.update(details), 'entry leg',
                                       notifier=lambda t, m: alerts.append((t, m)))
    wrapped('LIVE 2.00 USD USDC', {'quote deadline': deadline})
    assert alerts and 'IST' in alerts[0][1] and 's left' in alerts[0][1]
    assert seen['quote deadline'].startswith(deadline) and 'IST' in seen['quote deadline']
