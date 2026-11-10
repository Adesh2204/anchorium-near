"""Generic candle-to-brain adapter. No venue calls or strategy parameter edits."""
from strategy_freeze import verify_strategy_freeze
from candle_normalizer import candles_to_frame
from session_manager import SessionManager


class FrozenStrategy:
    def __init__(self, market, session=None):
        self.market = market
        self.session = session or SessionManager()
        verify_strategy_freeze()

    def evaluate_candles(self, candles):
        verify_strategy_freeze()
        from signal_brain import generate_vwap_signal
        frame = candles_to_frame(candles)
        self.session.get_vwap_anchor(frame.Timestamp.iloc[-1].to_pydatetime())
        # The unchanged brain groups dates from input timestamps. UTC is a data
        # convention for the continuous venue; indicator formulas are untouched.
        return generate_vwap_signal(frame)

    def get_signals(self, symbol, start_ms, end_ms):
        verify_strategy_freeze()
        return self.evaluate_candles(self.market.get_candles(symbol, start_ms, end_ms, interval='1m'))
