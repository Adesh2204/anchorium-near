"""LIVE gates: every precondition fails closed. No network, nothing is ever signed here."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from anchorium.near.live import (HARD_MAX_TOTAL_USD, HARD_MAX_TRADE_USD, ExecutionConfig, LiveGateError,
                                 check_live_trade, confirmation_phrase, deposit_nep141, require_typed_confirmation)


def test_default_is_dry_run():
    for env in ({}, {'DRY_RUN': '1'}, {'LIVE': '0'}, {'LIVE': '0', 'DRY_RUN': '0'}):
        cfg = ExecutionConfig.from_env(env)
        assert not cfg.live and cfg.mode == 'DRY_RUN' and cfg.banner == 'DRY RUN'


def test_live_needs_dry_run_explicitly_off():
    with pytest.raises(LiveGateError, match='DRY_RUN=0'):
        ExecutionConfig.from_env({'LIVE': '1'})
    with pytest.raises(LiveGateError, match='DRY_RUN=0'):
        ExecutionConfig.from_env({'LIVE': '1', 'DRY_RUN': '1'})


@pytest.mark.parametrize('cap', ['2.01', '100', '0', '-1', 'two'])
def test_live_cap_cannot_exceed_the_hard_cap(cap):
    with pytest.raises(LiveGateError):
        ExecutionConfig.from_env({'LIVE': '1', 'DRY_RUN': '0', 'MAX_LIVE_NOTIONAL_USD': cap})


def test_live_config_with_valid_cap():
    cfg = ExecutionConfig.from_env({'LIVE': '1', 'DRY_RUN': '0', 'MAX_LIVE_NOTIONAL_USD': '1.5'})
    assert cfg.live and cfg.max_trade_usd == Decimal('1.5') and cfg.max_total_usd == HARD_MAX_TOTAL_USD
    assert cfg.banner == 'LIVE (cap $1.5/trade, $5 total)'
    assert ExecutionConfig.from_env({'LIVE': '1', 'DRY_RUN': '0'}).max_trade_usd == HARD_MAX_TRADE_USD


def test_rejects_unknown_flag_values():
    with pytest.raises(LiveGateError):
        ExecutionConfig.from_env({'LIVE': 'yes'})


LIVE = ExecutionConfig(live=True, max_trade_usd=Decimal('2'))


def test_check_live_trade_paths():
    check_live_trade(LIVE, Decimal('2'), Decimal('3'), guard_allowed=True)  # exactly at both caps: ok
    with pytest.raises(LiveGateError, match='guard'):
        check_live_trade(LIVE, Decimal('1'), Decimal('0'), guard_allowed=False)
    with pytest.raises(LiveGateError, match='per-trade cap'):
        check_live_trade(LIVE, Decimal('2.01'), Decimal('0'), guard_allowed=True)
    with pytest.raises(LiveGateError, match='session total'):
        check_live_trade(LIVE, Decimal('2'), Decimal('3.01'), guard_allowed=True)
    with pytest.raises(LiveGateError, match='not in LIVE'):
        check_live_trade(ExecutionConfig(False, Decimal('0')), Decimal('1'), Decimal('0'), guard_allowed=True)


def test_typed_confirmation():
    phrase = confirmation_phrase(Decimal('2'), 'wNEAR')
    assert phrase == 'LIVE 2.00 USD wNEAR'
    require_typed_confirmation(phrase, prompt=lambda _: 'LIVE 2.00 USD wNEAR', is_tty=lambda: True)
    with pytest.raises(LiveGateError, match='did not match'):
        require_typed_confirmation(phrase, prompt=lambda _: 'yes', is_tty=lambda: True)
    with pytest.raises(LiveGateError, match='interactive terminal'):
        require_typed_confirmation(phrase, prompt=lambda _: phrase, is_tty=lambda: False)


def test_deposit_uses_ft_transfer_with_registration():
    calls = []

    class FakeNear:
        def ft_transfer(self, token, receiver, amount, register):
            calls.append((token, receiver, amount, register))
            return SimpleNamespace(transaction_hash='TxHash1')
    assert deposit_nep141(FakeNear(), 'usdc.near', 'dep-addr', Decimal('2.00')) == 'TxHash1'
    assert calls == [('usdc.near', 'dep-addr', '2.00', True)]

    class NoHash:
        def ft_transfer(self, *a, **k):
            return SimpleNamespace(transaction_hash=None)
    with pytest.raises(LiveGateError):
        deposit_nep141(NoHash(), 'usdc.near', 'dep', Decimal('1'))
