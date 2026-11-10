"""One provider-neutral input shape for the unchanged signal brain."""
from exchange.base_exchange import ExchangeError, decimal
from exchange.market_health import BAR_INTERVAL_MS


def candles_to_frame(candles):
    import pandas as pd
    if not candles:
        raise ExchangeError('NO_COMPLETED_CANDLES')
    symbol, interval = candles[0].symbol, candles[0].interval
    step = BAR_INTERVAL_MS.get(interval)
    for c in candles:
        if c.symbol != symbol or c.interval != interval or step is None:
            raise ExchangeError('STRATEGY_CANDLE_IDENTITY_MISMATCH')
        if (type(c.timestamp_ms) is not int or c.timestamp_ms % step or
                c.close_timestamp_ms not in {c.timestamp_ms+step-1, c.timestamp_ms+step}):
            raise ExchangeError('INVALID_CANDLE_BOUNDARIES')
        o,h,l,close = (decimal(v, positive=True) for v in (c.open,c.high,c.low,c.close))
        if h < max(o,l,close) or l > min(o,h,close):
            raise ExchangeError('INVALID_CANDLE_PRICES')
        decimal(c.volume, nonnegative=True)
    if any(b.timestamp_ms-a.timestamp_ms != step for a,b in zip(candles,candles[1:])):
        raise ExchangeError('STRATEGY_CANDLE_GAP_OR_DUPLICATE')
    return pd.DataFrame({
        'Timestamp': pd.to_datetime([c.timestamp_ms for c in candles],unit='ms',utc=True),
        'Open':[float(c.open) for c in candles], 'High':[float(c.high) for c in candles],
        'Low':[float(c.low) for c in candles], 'Close':[float(c.close) for c in candles],
        'Volume':[float(c.volume) for c in candles]})
