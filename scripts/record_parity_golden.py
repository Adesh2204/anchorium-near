"""Record what the ORIGINAL strategy (in its own source tree) outputs on each REPLAY case.

The vendored copy is byte-identical, so this is a behavioural cross-check of the same claim: the vendored
FrozenStrategy, fed the same live-window candles, must reproduce these values exactly
(tests/test_signal_parity.py).

Reproduce (read-only; it never modifies the source repository):

    git -C <Autonomous-Trading-Bot-Crypto clone> archive 68af4eb | tar -x -C /tmp/src68
    .venv/bin/python scripts/record_parity_golden.py /tmp/src68 fixtures/replay/hyperliquid_1m.json \
        fixtures/replay/parity_golden_68af4eb.json

The source tree's own strategy_freeze check (all 8 methods, 32 parameters) runs inside FrozenStrategy.
"""
import json
import subprocess
import sys
from pathlib import Path

CHILD = r'''
import json, sys
from decimal import Decimal
src, fixture = sys.argv[1], sys.argv[2]
sys.path.insert(0, src + "/algo-trading-bot")
import os; os.environ["ANCHORIUM_PUBLIC_ONLY"] = "true"
from exchange.base_exchange import Candle
from frozen_strategy import FrozenStrategy
from strategy_freeze import verify_strategy_freeze
LOOKBACK_MS = 26 * 3600 * 1000
doc = json.load(open(fixture))
out = {"source_freeze": verify_strategy_freeze(), "cases": {}}
for case in doc["cases"]:
    rows = doc["responses"][case["coin"]]["rows"]
    now = case["now_ms"]
    rows = sorted((r for r in rows if now - LOOKBACK_MS <= int(r["t"]) < now and int(r["T"]) < now), key=lambda r: int(r["t"]))
    candles = [Candle(symbol=case["coin"], interval="1m", timestamp_ms=int(r["t"]), close_timestamp_ms=int(r["T"]),
               open=Decimal(r["o"]), high=Decimal(r["h"]), low=Decimal(r["l"]), close=Decimal(r["c"]),
               volume=Decimal(r["v"])) for r in rows]
    start = len(candles) - 1
    while start > 0 and candles[start].timestamp_ms - candles[start-1].timestamp_ms == 60000:
        start -= 1
    candles = candles[start:]
    last = FrozenStrategy(market=None).evaluate_candles(candles).iloc[-1]
    out["cases"][case["name"]] = {"bars": len(candles), "signal_type": last["Signal_Type"],
        **{k: repr(float(last[k])) for k in ("VWAP", "ATR_14", "EMA_9", "EMA_21", "VWAP_Slope")}}
print(json.dumps(out))
'''


def main(source_dir, fixture, out_path):
    commit = '68af4eb4334043aea16e2650658d7df20fe68aaf'
    result = subprocess.run([sys.executable, '-I', '-c', CHILD, source_dir, fixture], capture_output=True, text=True,
                            check=True)
    golden = json.loads(result.stdout)
    golden.update({'source_repository': 'https://github.com/Adesh2204/Autonomous-Trading-Bot-Crypto',
                   'source_commit': commit, 'fixture': Path(fixture).name})
    Path(out_path).write_text(json.dumps(golden, indent=2, sort_keys=True) + '\n')
    for name, row in golden['cases'].items():
        print(name, row['signal_type'], 'bars', row['bars'])
    print('source freeze:', golden['source_freeze'])


if __name__ == '__main__':
    main(*sys.argv[1:4])
