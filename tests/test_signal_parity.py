"""The vendored strategy, through the adapter, reproduces the ORIGINAL source commit's outputs.

The golden indicator values were recorded on macOS arm64, where they match bit for bit. Other platforms' numpy/libm
can round the last bit differently (seen on Linux x86_64 CI), so there the check is a relative 1e-12 tolerance; the
signal type itself must always match exactly."""
import json
import math
import platform
from pathlib import Path

import httpx
import pytest

from anchorium.agent import signals

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/replay/hyperliquid_1m.json'
GOLDEN = json.loads((ROOT / 'fixtures/replay/parity_golden_68af4eb.json').read_text())
CASES = signals.load_replay(FIXTURE)


def test_golden_was_recorded_from_the_source_commit_with_its_full_freeze():
    assert GOLDEN['source_commit'] == '68af4eb4334043aea16e2650658d7df20fe68aaf'
    assert GOLDEN['source_freeze'] == {'baseline': 'nse-working-baseline', 'file_count': 1, 'method_count': 8,
                                       'parameter_count': 32, 'passed': True}
    assert set(GOLDEN['cases']) == {c.name for c in CASES}


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_vendored_strategy_matches_source_commit(case):
    candles = signals.latest_contiguous(signals.rows_to_candles(signals.window(case.rows, case.now_ms), case.coin,
                                                                case.now_ms))
    last = signals.evaluate_frame(candles).iloc[-1]
    golden = GOLDEN['cases'][case.name]
    assert len(candles) == golden['bars']
    assert last['Signal_Type'] == golden['signal_type'] == case.expected_signal_type
    recording_platform = platform.system() == 'Darwin' and platform.machine() == 'arm64'
    for column in ('VWAP', 'ATR_14', 'EMA_9', 'EMA_21', 'VWAP_Slope'):
        value, expected = float(last[column]), float(golden[column])
        if recording_platform:
            assert repr(value) == golden[column], column  # bit-for-bit float equality
        else:
            assert math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-15), (column, value, expected)


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_replay_case_produces_the_signal_on_the_latest_bar(case):
    signal, info = signals.evaluate_latest(case.rows, case.coin, case.now_ms)
    assert signal is not None and info['status'] == 'SIGNAL'
    assert signal.signal_type == case.expected_signal_type
    assert signal.direction == ('LONG' if case.expected_signal_type.endswith('LONG') else 'SHORT')
    assert signal.bar_close_ms == case.now_ms - 1
    assert signal.spot_asset.startswith('nep141:')


def test_signal_hash_is_deterministic_and_mode_specific():
    case = CASES[0]
    a, _ = signals.evaluate_latest(case.rows, case.coin, case.now_ms)
    b, _ = signals.evaluate_latest(case.rows, case.coin, case.now_ms)
    assert a.signal_hash('DRY_RUN') == b.signal_hash('DRY_RUN')
    assert a.signal_hash('DRY_RUN') != a.signal_hash('LIVE')
    assert len(a.signal_hash('DRY_RUN')) == 64


def test_incomplete_and_foreign_bars_are_dropped_or_rejected():
    case = CASES[0]
    rows = case.rows
    now = int(rows[-1]['T'])  # the last bar closes at `now`: it is not complete yet
    candles = signals.rows_to_candles(rows, case.coin, now)
    assert candles[-1].timestamp_ms == int(rows[-2]['t'])
    with pytest.raises(ValueError, match='unexpected candle identity'):
        signals.rows_to_candles([{**rows[0], 's': 'DOGE'}], case.coin, now)


def test_gap_is_never_filled():
    case = CASES[0]
    candles = signals.rows_to_candles(case.rows, case.coin, case.now_ms)
    gapped = candles[:100] + candles[101:]
    assert len(signals.latest_contiguous(gapped)) == len(candles) - 101


def test_hyperliquid_request_is_read_only_candle_snapshot():
    seen = {}

    def handler(request):
        seen['url'], seen['body'] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json=[])
    signals.HyperliquidCandles(transport=httpx.MockTransport(handler)).fetch('BTC', now_ms=10_000_000, lookback_ms=60_000)
    assert seen['url'] == 'https://api.hyperliquid.xyz/info'
    assert seen['body'] == {'type': 'candleSnapshot',
                            'req': {'coin': 'BTC', 'interval': '1m', 'startTime': 9_940_000, 'endTime': 10_000_000}}
