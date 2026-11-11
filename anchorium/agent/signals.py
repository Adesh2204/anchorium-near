"""Read-only adapter: completed 1-minute candles -> the unchanged vendored FrozenStrategy -> Signal events.

Nothing here changes the strategy. It only:
* fetches public candles (Hyperliquid mainnet `/info` `candleSnapshot`, no signer, no account) or reads a
  recorded fixture of the same responses (REPLAY);
* keeps completed bars only (close time < now) in the vendored `Candle` type;
* calls `FrozenStrategy.evaluate_candles` and reads `Signal_Type` on the latest bar.

The signal is derived from perpetual-futures market data; execution (elsewhere) is spot, through NEAR Intents.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / 'vendor' / 'anchorium_strategy' / 'algo-trading-bot'
HYPERLIQUID_INFO_URL = 'https://api.hyperliquid.xyz/info'
LOOKBACK_MS = 26 * 3600 * 1000  # covers the current UTC session plus EMA/ATR warm-up
SIGNAL_SOURCE = 'hyperliquid-mainnet-public-1m'

#: Strategy coin -> NEAR Intents spot asset on NEAR (verified in GET /v0/tokens, 2026-11-10).
SPOT_ASSET = {
    'BTC': 'nep141:nbtc.bridge.near',
    'ETH': 'nep141:eth.bridge.near',
    'NEAR': 'nep141:wrap.near',
}
LONG_TYPES = ('PULLBACK_LONG', 'BREAKOUT_LONG')
SHORT_TYPES = ('PULLBACK_SHORT', 'BREAKDOWN_SHORT')


def _vendor():
    """Imports the vendored strategy (byte-identical; see vendor/anchorium_strategy/SOURCE.md)."""
    os.environ.setdefault('ANCHORIUM_PUBLIC_ONLY', 'true')  # vendored venue_config: skip any dotenv file
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    from exchange.base_exchange import Candle  # noqa: PLC0415  (vendored module)
    from frozen_strategy import FrozenStrategy  # noqa: PLC0415
    return Candle, FrozenStrategy


@dataclass(frozen=True)
class Signal:
    coin: str
    direction: str  # LONG | SHORT
    signal_type: str
    bar_open_ms: int
    bar_close_ms: int
    close: str
    vwap: str
    atr_14: str
    ema_9: str
    ema_21: str
    source: str = SIGNAL_SOURCE

    @property
    def spot_asset(self) -> str:
        return SPOT_ASSET[self.coin]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def signal_hash(self, mode: str) -> str:
        """Deterministic id: the same bar + type + mode always maps to the same on-chain decision."""
        canonical = json.dumps({**self.to_dict(), 'mode': mode}, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode()).hexdigest()


def rows_to_candles(rows: list[dict[str, Any]], coin: str, now_ms: int) -> list[Any]:
    """Hyperliquid candleSnapshot rows -> vendored Candle objects, completed bars only, sorted, de-duplicated."""
    Candle, _ = _vendor()
    by_open = {}
    for r in rows:
        if r.get('s') != coin or r.get('i') != '1m':
            raise ValueError(f'unexpected candle identity {r.get("s")}/{r.get("i")} for {coin}')
        if int(r['T']) < now_ms:
            by_open[int(r['t'])] = r
    return [Candle(symbol=coin, interval='1m', timestamp_ms=int(r['t']), close_timestamp_ms=int(r['T']),
                   open=Decimal(r['o']), high=Decimal(r['h']), low=Decimal(r['l']), close=Decimal(r['c']),
                   volume=Decimal(r['v']))
            for _, r in sorted(by_open.items())]


def latest_contiguous(candles: list[Any]) -> list[Any]:
    """The newest gap-free run of bars (the vendored normalizer rejects gaps; we never fill them)."""
    start = len(candles) - 1
    while start > 0 and candles[start].timestamp_ms - candles[start - 1].timestamp_ms == 60_000:
        start -= 1
    return candles[start:]


def evaluate_frame(candles: list[Any]):
    _, FrozenStrategy = _vendor()
    return FrozenStrategy(market=None).evaluate_candles(candles)


def signal_from_frame(frame, coin: str, candles: list[Any]) -> Signal | None:
    """The signal on the LATEST bar only (what a live agent sees when that bar closes)."""
    last = frame.iloc[-1]
    kind = last.get('Signal_Type')
    if kind not in LONG_TYPES + SHORT_TYPES:
        return None
    bar = candles[-1]
    fmt = lambda v: format(Decimal(str(float(v))), 'f')  # noqa: E731
    return Signal(coin=coin, direction='LONG' if kind in LONG_TYPES else 'SHORT', signal_type=str(kind),
                  bar_open_ms=bar.timestamp_ms, bar_close_ms=bar.close_timestamp_ms, close=format(bar.close, 'f'),
                  vwap=fmt(last['VWAP']), atr_14=fmt(last['ATR_14']), ema_9=fmt(last['EMA_9']),
                  ema_21=fmt(last['EMA_21']))


def window(rows: list[dict[str, Any]], now_ms: int, lookback_ms: int = LOOKBACK_MS) -> list[dict[str, Any]]:
    """The exact input a live agent would have at `now_ms`: bars opened in [now - lookback, now)."""
    return [r for r in rows if now_ms - lookback_ms <= int(r['t']) < now_ms]


def evaluate_latest(rows: list[dict[str, Any]], coin: str, now_ms: int) -> tuple[Signal | None, dict[str, Any]]:
    """Live and REPLAY share this path, so a replayed bar is evaluated on exactly the live input window."""
    candles = latest_contiguous(rows_to_candles(window(rows, now_ms), coin, now_ms))
    if len(candles) < 30:
        return None, {'coin': coin, 'bars': len(candles), 'status': 'NOT_ENOUGH_CONTIGUOUS_BARS'}
    frame = evaluate_frame(candles)
    signal = signal_from_frame(frame, coin, candles)
    last = frame.iloc[-1]
    return signal, {'coin': coin, 'bars': len(candles), 'last_bar_open_ms': candles[-1].timestamp_ms,
                    'close': format(candles[-1].close, 'f'), 'vwap': float(last['VWAP']),
                    'atr_14': float(last['ATR_14']), 'status': 'SIGNAL' if signal else 'NO_SIGNAL'}


class HyperliquidCandles:
    """Public, read-only candle source. No signer, no account, no order endpoints."""

    def __init__(self, url: str = HYPERLIQUID_INFO_URL, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(timeout=20.0, transport=transport, headers={'Content-Type': 'application/json'})
        self.url = url

    def fetch(self, coin: str, now_ms: int | None = None, lookback_ms: int = LOOKBACK_MS) -> list[dict[str, Any]]:
        now_ms = now_ms or int(time.time() * 1000)
        body = {'type': 'candleSnapshot',
                'req': {'coin': coin, 'interval': '1m', 'startTime': now_ms - lookback_ms, 'endTime': now_ms}}
        response = self._http.post(self.url, json=body)
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError(f'unexpected candleSnapshot response for {coin}')
        return rows


@dataclass(frozen=True)
class ReplayCase:
    name: str
    coin: str
    now_ms: int  # replay clock: just after the signal bar closed
    expected_signal_type: str
    rows: list[dict[str, Any]]


def load_replay(path: Path) -> list[ReplayCase]:
    data = json.loads(Path(path).read_text())
    return [ReplayCase(name=c['name'], coin=c['coin'], now_ms=c['now_ms'],
                       expected_signal_type=c['expected_signal_type'], rows=data['responses'][c['coin']]['rows'])
            for c in data['cases']]


def latest_bar_signal(feed: HyperliquidCandles, coin: str, signal_type: str, source: str,
                      now_ms: int | None = None) -> Signal:
    """A non-strategy SHORT (exit) signal at the latest completed bar: a human-ordered or timer exit. It is
    journaled with its own `signal_type` and `source`, never presented as a strategy signal."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    candles = latest_contiguous(rows_to_candles(window(feed.fetch(coin, now), now), coin, now))
    last = evaluate_frame(candles).iloc[-1]
    bar = candles[-1]
    return Signal(coin=coin, direction='SHORT', signal_type=signal_type, bar_open_ms=bar.timestamp_ms,
                  bar_close_ms=bar.close_timestamp_ms, close=format(bar.close, 'f'), vwap=str(float(last['VWAP'])),
                  atr_14=str(float(last['ATR_14'])), ema_9=str(float(last['EMA_9'])),
                  ema_21=str(float(last['EMA_21'])), source=source)
