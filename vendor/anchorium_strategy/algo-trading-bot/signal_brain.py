"""
signal_brain.py — Signal Generation Layer (The Brain)
=====================================================
Contains vectorized indicator calculations (VWAP, ATR-14, VWAP Slope, EMA-9, EMA-21)
and strict, high-probability VWAP Pullback Buy Signal detection equipped with a
Dual-Layer Trend Regime Filter and Anti-Spam State Lock.

Strict 3-Condition Pullback Model (Dynamic ATR Band)
─────────────────────────────────────────────────────
1. Displacement Check : Candle N-1 must close firmly ABOVE VWAP_N-1.
2. Dynamic ATR Touch  : Candle N Low ≤ VWAP_N + (0.2 × ATR_14).
3. Rejection / Rebound: Candle N Close firmly ABOVE VWAP_N.

Dual-Layer Trend Regime Filter (Regime Gatekeeper)
────────────────────────────────────────────────────
1. Gate 1: VWAP Slope (10-period) must be positive (vwap_slope > 0).
2. Gate 2: Fast vs Slow Momentum: EMA(9) >= EMA(21) (bullish moving average structure).

Anti-Spam State Lock
────────────────────
After a Buy_Signal fires, signal generation is locked until price
displaces away from VWAP for at least 2 consecutive candles — preventing
the same pullback zone from firing repeated entries on adjacent
candles without a meaningful price re-acceleration first.

Scale-In (Pyramiding) Support
─────────────────────────────
Once the Anti-Spam lock resets, a new valid signal is emitted and the
Execution Layer treats it as a candidate scale-in (subject to its own
MAX_TRANCHES, Working Capital, and Risk Budget caps).
"""

import logging
from typing import Any, Optional, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    from config import (
        MIN_CANDLES_BEFORE_TRADE,
        MIN_ATR_PCT,
        ATR_BAND_MULT,
        VOL_SURGE_MULT,
        BREAKOUT_CLOSE_MULT,
        VOL_MA_PERIOD,
        EMA_FAST,
        EMA_SLOW,
        VWAP_SLOPE_LOOKBACK,
        ATR_PERIOD,
        SHORT_BAND_MULT,
        SHORT_CLOSE_MULT,
        KNIFE_UP_MULT,
        BIAS_VWAP_GAP_PCT,
        BIAS_VOTES_NEEDED,
    )
except ImportError:
    MIN_CANDLES_BEFORE_TRADE = 15
    MIN_ATR_PCT = 0.0006
    ATR_BAND_MULT = 1.50
    VOL_SURGE_MULT = 1.50
    BREAKOUT_CLOSE_MULT = 0.50
    VOL_MA_PERIOD = 10
    EMA_FAST = 9
    EMA_SLOW = 21
    VWAP_SLOPE_LOOKBACK = 10
    ATR_PERIOD = 14
    SHORT_BAND_MULT = 1.50
    SHORT_CLOSE_MULT = 0.50
    KNIFE_UP_MULT = 1.00
    BIAS_VWAP_GAP_PCT = 0.0005
    BIAS_VOTES_NEEDED = 2


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Rolling 14-period Average True Range (ATR-14) across completed bars:
        TR_t = max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|)
        ATR_{14, t} = 1/14 * sum_{i=0}^{13} TR_{t-i}

    Parameters
    ----------
    df : pd.DataFrame — Must contain columns High/Low/Close.
    period : int      — ATR lookback (default 14 for 1-minute candles).

    Returns
    -------
    pd.Series of ATR values aligned with the input index.
    """
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)

    prev_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = true_range.rolling(period, min_periods=1).mean()
    return atr


def evaluate_regime_gate(
    potential_signal: str,
    vwap_slope: float,
    ema9: float,
    ema21: float,
    log: Optional[logging.Logger] = None,
) -> Optional[str]:
    """
    Dual-Layer Trend Regime Filter (VWAP Slope + EMA Crossover Gatekeeper).
    Enforces strict uptrend validation before granting permission to execute LONG setups.
    Also scaffolds inverse logic for a future adaptive SHORT regime.

    Parameters
    ----------
    potential_signal : str
        Candidate signal ('BUY' or 'SHORT').
    vwap_slope : float
        10-period VWAP slope: (VWAP_t - VWAP_{t-10}) / 10.0.
    ema9 : float
        9-period Exponential Moving Average of Close.
    ema21 : float
        21-period Exponential Moving Average of Close.
    log : logging.Logger, optional
        Custom logger instance.

    Returns
    -------
    str or None
        Validated signal ('BUY' / 'SHORT') or None if filtered out by regime gates.
    """
    _logger = log or logger

    if potential_signal == "BUY":
        # Gate A (VWAP Trajectory)
        if np.isnan(vwap_slope) or vwap_slope <= 0:
            _logger.debug(
                "[REGIME FILTER] Signal blocked: VWAP slope negative. "
                "Market is not in a buy-side regime."
            )
            return None

        # Gate B (EMA Alignment)
        if np.isnan(ema9) or np.isnan(ema21) or ema9 < ema21:
            _logger.debug(
                "[REGIME FILTER] Signal blocked: EMA9 < EMA21. "
                "Bearish trend structure detected."
            )
            return None

        # If both gates pass, the trend is up. We have permission to trade.
        return "BUY"

    if potential_signal == "SHORT":
        # Gate 1: VWAP Directionality (Downward sloping VWAP)
        if np.isnan(vwap_slope) or vwap_slope >= 0:
            _logger.info(
                f"[REGIME FILTER] Short blocked: VWAP slope is non-negative ({vwap_slope:.4f}). "
                "Market is not in a sell-side regime."
            )
            return None

        # Gate 2: Fast vs Slow Momentum (EMA 9 vs EMA 21)
        if np.isnan(ema9) or np.isnan(ema21) or ema9 > ema21:
            _logger.info(
                f"[REGIME FILTER] Short blocked: EMA(9) > EMA(21). "
                f"Bullish momentum detected (EMA9={ema9:.2f}, EMA21={ema21:.2f})."
            )
            return None

        return "SHORT"

    return None


def _get_session_key(df: pd.DataFrame) -> pd.Index:
    """Return a date-level session key grouped by trading day."""
    if "Timestamp" in df.columns:
        ts = pd.to_datetime(df["Timestamp"])
        return pd.Index(ts.dt.date.to_list(), name="session_key")
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Index(df.index.date, name="session_key")
    raise ValueError("DataFrame must contain a 'Timestamp' column or DatetimeIndex.")


def generate_vwap_signal(
    df: pd.DataFrame,
    min_candles_before_trade: int = MIN_CANDLES_BEFORE_TRADE,
    min_atr_pct: float = MIN_ATR_PCT,
    min_atr: Optional[float] = None,
    atr_band_mult: float = ATR_BAND_MULT,
    vol_surge_mult: float = VOL_SURGE_MULT,
) -> pd.DataFrame:
    """
    Calculate Intraday VWAP, rolling ATR-14, VWAP Slope (10-period), EMA(9), EMA(21),
    and detect high-probability "VWAP Pullback" and "VWAP Breakout" Buy Signals guarded by:
    1. VWAP Stabilization Gate (requires >= min_candles_before_trade session candles).
    2. Price-Relative Volatility Floor (requires ATR_14 >= Close * min_atr_pct).
    3. Deep Breakdown Filter for pullbacks (prior candle low >= VWAP - 1.0 * ATR_14).
    4. Dual-Layer Trend Regime Filter (VWAP_Slope > 0 & EMA_9 >= EMA_21).
    5. Anti-Spam State Lock (2 consecutive candle displacement required after signal).

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: Open, High, Low, Close, Volume.
        May span multiple sessions; VWAP resets at each session boundary.
    min_candles_before_trade : int, optional
        Minimum number of completed candles required in the active session
        before allowing trade entries (default: 15).
    min_atr_pct : float, optional
        Price-relative volatility floor fraction (default: 0.0006 = 0.06% of Close).
    min_atr : float, optional
        Explicit absolute volatility floor override in price units.
    atr_band_mult : float, optional
        ATR multiplier for the dynamic VWAP pullback zone (default: 1.50).
    vol_surge_mult : float, optional
        Volume multiplier over rolling 10-bar average for breakouts (default: 1.50).

    Returns
    -------
    pd.DataFrame
        Original DataFrame enriched with:
        • VWAP            (float) — Running session intraday VWAP.
        • ATR_14          (float) — Rolling 14-period ATR.
        • VWAP_Slope      (float) — 10-period VWAP Slope.
        • EMA_9           (float) — 9-period EMA of Close.
        • EMA_21          (float) — 21-period EMA of Close.
        • Vol_MA10        (float) — Rolling 10-period volume average.
        • Buy_Signal      (bool)  — True if Pullback Buy conditions converge.
        • Breakout_Signal (bool)  — True if Breakout Buy conditions converge.
        • Signal_Type     (str)   — 'PULLBACK_LONG' | 'BREAKOUT_LONG' | None.
    """
    # ── 0. Input Validation ─────────────────────────────────────────────
    required_cols = {"Open", "High", "Low", "Close", "Volume"}
    if not required_cols.issubset(df.columns):
        missing = required_cols - set(df.columns)
        raise ValueError(f"DataFrame is missing required columns: {missing}")

    if df.empty:
        df = df.copy()
        df["VWAP"] = np.nan
        df["ATR_14"] = np.nan
        df["VWAP_Slope"] = np.nan
        df["EMA_9"] = np.nan
        df["EMA_21"] = np.nan
        df["Vol_MA10"] = np.nan
        df["Buy_Signal"] = False
        df["Breakout_Signal"] = False
        df["Signal_Type"] = None
        return df

    out = df.copy()

    # ── 1. VWAP Calculation (Vectorized, daily-session reset) ──────────
    typical_price = (out["High"].astype(float) + out["Low"].astype(float) + out["Close"].astype(float)) / 3.0
    session_key = _get_session_key(out)

    cumulative_tp_vol = (typical_price * out["Volume"].astype(float)).groupby(session_key).cumsum()
    cumulative_vol = out["Volume"].astype(float).groupby(session_key).cumsum()

    out["VWAP"] = np.where(
        cumulative_vol == 0,
        np.nan,
        cumulative_tp_vol / cumulative_vol,
    )

    # ── 2. Rolling 14-period ATR (volatility proxy) ────────────────────
    out["ATR_14"] = compute_atr(out, period=14)

    # ── 3. Dual-Layer Trend Indicators (VWAP Slope + EMAs) ────────────
    # 10-period VWAP slope: (VWAP_t - VWAP_{t-10}) / 10.0
    vwap_shifted_10 = out["VWAP"].groupby(session_key).shift(10)
    session_first_vwap = out["VWAP"].groupby(session_key).transform("first")
    cum_count = out.groupby(session_key).cumcount()

    out["VWAP_Slope"] = np.where(
        cum_count >= 10,
        (out["VWAP"] - vwap_shifted_10) / 10.0,
        np.where(
            cum_count > 0,
            (out["VWAP"] - session_first_vwap) / np.maximum(cum_count, 1),
            0.0,
        ),
    )

    # 9-period and 21-period Exponential Moving Averages of Close price
    out["EMA_9"] = out.groupby(session_key)["Close"].transform(
        lambda s: s.astype(float).ewm(span=9, adjust=False).mean()
    )
    out["EMA_21"] = out.groupby(session_key)["Close"].transform(
        lambda s: s.astype(float).ewm(span=21, adjust=False).mean()
    )

    # ── 4. Volume Moving Average (10-period, min_periods=5) ───────────
    out["Vol_MA10"] = (
        out.groupby(session_key)["Volume"]
        .transform(lambda s: s.astype(float).rolling(10, min_periods=5).mean())
        .fillna(out["Volume"].astype(float))
    )

    # ── 5. Pullback Indicators ────────────────────────────────────────
    # Trend Displacement Check (Candle t-1 firmly above VWAP)
    close_above_vwap_tm1 = (out["Close"].astype(float) > out["VWAP"]).shift(1)

    # Dynamic ATR Penetration / Touch: Low_t <= VWAP_t + (atr_band_mult * ATR_14_t)
    dynamic_band = out["VWAP"] + atr_band_mult * out["ATR_14"]
    low_tests_vwap = out["Low"].astype(float) <= dynamic_band

    # Rebound & Close back above VWAP
    close_bounces = out["Close"].astype(float) > out["VWAP"]

    # Deep Breakdown Filter (Prevent falling knives)
    prior_low = out["Low"].astype(float).groupby(session_key).shift(1)
    deep_breakdown_threshold = out["VWAP"] - 1.0 * out["ATR_14"]
    prior_broke_support = prior_low < deep_breakdown_threshold

    # Raw Pullback signal
    raw_pullback_signal = (
        close_above_vwap_tm1.fillna(False).astype(bool)
        & low_tests_vwap.fillna(False).astype(bool)
        & close_bounces.fillna(False).astype(bool)
    )

    # ── 6. Breakout Indicators ────────────────────────────────────────
    # A. Close_t > VWAP_t + 0.5 * ATR_14_t (decisive close above VWAP)
    # B. Volume_t >= vol_surge_mult * Vol_MA10_t (volume surge confirmation)
    raw_breakout_signal = (
        (out["Close"].astype(float) > (out["VWAP"] + 0.5 * out["ATR_14"]))
        & (out["Volume"].astype(float) >= vol_surge_mult * out["Vol_MA10"])
    )

    # ── 8. Short Pullback Indicators ──────────────────────────────────
    # Mirror of long pullback: rally to VWAP that fails, resumes downtrend
    close_below_vwap_tm1 = (out["Close"].astype(float) < out["VWAP"]).shift(1)
    short_dynamic_band = out["VWAP"] - SHORT_BAND_MULT * out["ATR_14"]
    high_tests_vwap_short = out["High"].astype(float) >= short_dynamic_band
    close_below_vwap = out["Close"].astype(float) < out["VWAP"]

    # Knife-up filter: prior candle must not have spiked violently upward
    prior_high = out["High"].astype(float).groupby(session_key).shift(1)
    knife_up_threshold = out["VWAP"] + KNIFE_UP_MULT * out["ATR_14"]
    prior_spiked_up = prior_high > knife_up_threshold

    raw_pullback_short_signal = (
        close_below_vwap_tm1.fillna(False).astype(bool)
        & high_tests_vwap_short.fillna(False).astype(bool)
        & close_below_vwap.fillna(False).astype(bool)
    )

    # ── 9. Breakdown Short Indicators ─────────────────────────────────
    # Mirror of breakout long: decisive close below VWAP with volume surge
    raw_breakdown_short_signal = (
        (out["Close"].astype(float) < (out["VWAP"] - SHORT_CLOSE_MULT * out["ATR_14"]))
        & (out["Volume"].astype(float) >= vol_surge_mult * out["Vol_MA10"])
    )

    # ── 10. Gatekeepers, Filters & Anti-Spam State Lock ───────────────
    buy_signal_vals: list[bool] = [False] * len(out)
    breakout_signal_vals: list[bool] = [False] * len(out)
    sell_signal_vals: list[bool] = [False] * len(out)
    breakdown_sell_signal_vals: list[bool] = [False] * len(out)
    signal_type_vals: list[Optional[str]] = [None] * len(out)

    locked = False
    consecutive_displaced = 0

    raw_pullback_arr = raw_pullback_signal.to_numpy()
    raw_breakout_arr = raw_breakout_signal.to_numpy()
    raw_pullback_short_arr = raw_pullback_short_signal.to_numpy()
    raw_breakdown_short_arr = raw_breakdown_short_signal.to_numpy()
    low_arr = out["Low"].astype(float).to_numpy()
    high_arr = out["High"].astype(float).to_numpy()
    close_arr = out["Close"].astype(float).to_numpy()
    vwap_arr = out["VWAP"].astype(float).to_numpy()
    dynamic_band_arr = dynamic_band.to_numpy()
    short_dynamic_band_arr = short_dynamic_band.to_numpy()
    vwap_slope_arr = out["VWAP_Slope"].to_numpy()
    ema9_arr = out["EMA_9"].to_numpy()
    ema21_arr = out["EMA_21"].to_numpy()
    cum_count_arr = cum_count.to_numpy()
    prior_broke_support_arr = prior_broke_support.fillna(False).to_numpy()
    prior_spiked_up_arr = prior_spiked_up.fillna(False).to_numpy()
    atr_arr = out["ATR_14"].to_numpy()
    vol_arr = out["Volume"].astype(float).to_numpy()
    vol_ma10_arr = out["Vol_MA10"].astype(float).to_numpy()
    ts_arr = (
        out["Timestamp"].to_numpy()
        if "Timestamp" in out.columns
        else (out.index.to_numpy() if hasattr(out.index, "to_numpy") else None)
    )

    for i in range(len(out)):
        session_candle_count = int(cum_count_arr[i]) + 1
        current_close = float(close_arr[i])
        current_low = float(low_arr[i])
        current_high = float(high_arr[i])
        current_vwap = float(vwap_arr[i])
        current_atr_val = float(atr_arr[i])
        current_band = float(dynamic_band_arr[i])
        current_short_band = float(short_dynamic_band_arr[i])
        current_slope = float(vwap_slope_arr[i])
        current_ema9 = float(ema9_arr[i])
        current_ema21 = float(ema21_arr[i])
        current_vol = float(vol_arr[i])
        current_vol_ma10 = float(vol_ma10_arr[i])

        min_floor = min_atr if min_atr is not None else (current_close * min_atr_pct)

        # Displacement check for anti-spam unlock:
        # Long displacement: low is above the upper VWAP band
        # Short displacement: high is below the lower VWAP band
        is_displaced_long = bool(current_low > current_band)
        is_displaced_short = bool(current_high < current_short_band)
        is_displaced_away = is_displaced_long or is_displaced_short

        if locked:
            if is_displaced_away:
                consecutive_displaced += 1
                if consecutive_displaced >= 2:
                    locked = False
            else:
                consecutive_displaced = 0

            buy_signal_vals[i] = False
            breakout_signal_vals[i] = False
            sell_signal_vals[i] = False
            breakdown_sell_signal_vals[i] = False
        else:
            is_pullback_candidate = bool(raw_pullback_arr[i])
            is_breakout_candidate = bool(raw_breakout_arr[i])
            is_pullback_short_candidate = bool(raw_pullback_short_arr[i])
            is_breakdown_short_candidate = bool(raw_breakdown_short_arr[i])

            pullback_valid = False
            breakout_valid = False
            short_pullback_valid = False
            short_breakdown_valid = False

            # ── Evaluate LONG Pullback Setup ──────────────────────────
            if is_pullback_candidate:
                if session_candle_count < min_candles_before_trade:
                    logger.debug(
                        f"[FILTER] VWAP stabilizing. Candle count ({session_candle_count}) < {min_candles_before_trade}. "
                        "Skipping evaluation."
                    )
                elif np.isnan(current_atr_val) or current_atr_val < min_floor:
                    logger.debug(
                        f"[VOLATILITY GATE] Pullback blocked. Current ATR ({current_atr_val:.3f}) < "
                        f"Minimum required ({min_floor:.3f}). Market is too tight to overcome friction costs."
                    )
                elif bool(prior_broke_support_arr[i]):
                    logger.info(
                        "[FILTER] Signal blocked: Prior candle broke down too aggressively. Not a safe pullback."
                    )
                else:
                    validated = evaluate_regime_gate(
                        potential_signal="BUY",
                        vwap_slope=current_slope,
                        ema9=current_ema9,
                        ema21=current_ema21,
                        log=logger,
                    )
                    if validated == "BUY":
                        pullback_valid = True

            # ── Evaluate LONG Breakout Setup ──────────────────────────
            if is_breakout_candidate and not pullback_valid:
                if session_candle_count < min_candles_before_trade:
                    logger.debug(
                        f"[FILTER] VWAP stabilizing. Candle count ({session_candle_count}) < {min_candles_before_trade}. "
                        "Skipping evaluation."
                    )
                elif np.isnan(current_atr_val) or current_atr_val < min_floor:
                    logger.debug(
                        f"[VOLATILITY GATE] Breakout blocked. Current ATR ({current_atr_val:.3f}) < "
                        f"Minimum required ({min_floor:.3f}). Market is too tight to overcome friction costs."
                    )
                else:
                    validated = evaluate_regime_gate(
                        potential_signal="BUY",
                        vwap_slope=current_slope,
                        ema9=current_ema9,
                        ema21=current_ema21,
                        log=logger,
                    )
                    if validated == "BUY":
                        breakout_valid = True

            # ── Evaluate SHORT Pullback Setup ─────────────────────────
            if is_pullback_short_candidate and not (pullback_valid or breakout_valid):
                if session_candle_count < min_candles_before_trade:
                    pass  # Already logged above if long candidate also existed
                elif np.isnan(current_atr_val) or current_atr_val < min_floor:
                    logger.debug(
                        f"[VOLATILITY GATE] Short pullback blocked. ATR ({current_atr_val:.3f}) < "
                        f"floor ({min_floor:.3f})."
                    )
                elif bool(prior_spiked_up_arr[i]):
                    logger.debug(
                        "[FILTER] Short pullback blocked: Prior candle spiked up too aggressively."
                    )
                else:
                    validated = evaluate_regime_gate(
                        potential_signal="SHORT",
                        vwap_slope=current_slope,
                        ema9=current_ema9,
                        ema21=current_ema21,
                        log=logger,
                    )
                    if validated == "SHORT":
                        short_pullback_valid = True

            # ── Evaluate SHORT Breakdown Setup ────────────────────────
            if is_breakdown_short_candidate and not (pullback_valid or breakout_valid or short_pullback_valid):
                if session_candle_count < min_candles_before_trade:
                    pass
                elif np.isnan(current_atr_val) or current_atr_val < min_floor:
                    logger.debug(
                        f"[VOLATILITY GATE] Short breakdown blocked. ATR ({current_atr_val:.3f}) < "
                        f"floor ({min_floor:.3f})."
                    )
                else:
                    validated = evaluate_regime_gate(
                        potential_signal="SHORT",
                        vwap_slope=current_slope,
                        ema9=current_ema9,
                        ema21=current_ema21,
                        log=logger,
                    )
                    if validated == "SHORT":
                        short_breakdown_valid = True

            # ── Record validated signals & lock anti-spam ─────────────
            if pullback_valid:
                buy_signal_vals[i] = True
                signal_type_vals[i] = "PULLBACK_LONG"
                locked = True
                consecutive_displaced = 0
            elif breakout_valid:
                breakout_signal_vals[i] = True
                signal_type_vals[i] = "BREAKOUT_LONG"
                locked = True
                consecutive_displaced = 0
            elif short_pullback_valid:
                sell_signal_vals[i] = True
                signal_type_vals[i] = "PULLBACK_SHORT"
                locked = True
                consecutive_displaced = 0
            elif short_breakdown_valid:
                breakdown_sell_signal_vals[i] = True
                signal_type_vals[i] = "BREAKDOWN_SHORT"
                locked = True
                consecutive_displaced = 0

        # SIGNAL_DEBUG per-candle logging
        ts_display = ""
        if ts_arr is not None:
            ts_str = str(ts_arr[i])
            ts_display = ts_str[11:16] if len(ts_str) >= 16 else ts_str

        logger.debug(
            "[SIGNAL_DEBUG] C#%03d %s | P:%.2f VWAP:%.2f Δ:%+.3f | "
            "ATR:%.3f Floor:%.3f ATR_OK:%s | Band:%.3f ShortBand:%.3f | "
            "Slope:%+.5f EMA_Bull:%s | Vol:%.0f VolMA10:%.0f Surge:%s | "
            "BUY:%s BREAKOUT:%s SELL:%s BREAKDOWN_S:%s",
            i + 1,
            ts_display,
            current_close,
            current_vwap,
            current_close - current_vwap,
            current_atr_val,
            min_floor,
            bool(current_atr_val >= min_floor),
            current_band,
            current_short_band,
            current_slope,
            bool(current_ema9 >= current_ema21),
            current_vol,
            current_vol_ma10,
            bool(current_vol >= vol_surge_mult * current_vol_ma10),
            bool(buy_signal_vals[i]),
            bool(breakout_signal_vals[i]),
            bool(sell_signal_vals[i]),
            bool(breakdown_sell_signal_vals[i]),
        )

    out["Buy_Signal"] = buy_signal_vals
    out["Breakout_Signal"] = breakout_signal_vals
    out["Sell_Signal"] = sell_signal_vals
    out["Breakdown_Sell_Signal"] = breakdown_sell_signal_vals
    out["Signal_Type"] = signal_type_vals

    return out


def detect_daily_bias(df: pd.DataFrame) -> str:
    """
    Determine daily directional bias from 3 independent confluence checks.
    Called after MIN_CANDLES_BEFORE_TRADE candles have closed.

    Returns
    -------
    str
        "LONG_BIAS", "SHORT_BIAS", or "NEUTRAL"

    Uses 3 independent confluence checks and requires BIAS_VOTES_NEEDED
    (default 2) of 3 to agree before declaring bias.
    """
    if len(df) < MIN_CANDLES_BEFORE_TRADE:
        return "NEUTRAL"

    latest = df.iloc[-1]

    # Ensure required columns exist
    for col in ("Close", "VWAP", "VWAP_Slope", "EMA_9", "EMA_21"):
        if col not in df.columns:
            return "NEUTRAL"

    close_val = float(latest["Close"])
    vwap_val = float(latest["VWAP"])
    slope_val = float(latest["VWAP_Slope"])
    ema9_val = float(latest["EMA_9"])
    ema21_val = float(latest["EMA_21"])

    # ── CHECK 1: VWAP Position ─────────────────────────────
    vwap_gap_pct = (close_val - vwap_val) / vwap_val if vwap_val > 0 else 0.0

    if vwap_gap_pct > BIAS_VWAP_GAP_PCT:
        check1 = "LONG"
    elif vwap_gap_pct < -BIAS_VWAP_GAP_PCT:
        check1 = "SHORT"
    else:
        check1 = "NEUTRAL"

    # ── CHECK 2: VWAP Slope ────────────────────────────────
    if slope_val > 0:
        check2 = "LONG"
    elif slope_val < 0:
        check2 = "SHORT"
    else:
        check2 = "NEUTRAL"

    # ── CHECK 3: EMA Regime ────────────────────────────────
    if ema9_val > ema21_val:
        check3 = "LONG"
    elif ema9_val < ema21_val:
        check3 = "SHORT"
    else:
        check3 = "NEUTRAL"

    # ── CONFLUENCE VOTE ────────────────────────────────────
    votes = [check1, check2, check3]
    long_votes = votes.count("LONG")
    short_votes = votes.count("SHORT")

    if long_votes >= BIAS_VOTES_NEEDED:
        return "LONG_BIAS"
    if short_votes >= BIAS_VOTES_NEEDED:
        return "SHORT_BIAS"
    return "NEUTRAL"


def update_cpp_tick(
    cpp_engine: Any,
    price: float,
    volume: float,
    current_atr: Optional[float] = None,
) -> dict:
    """
    Feed live price, volume, and dynamic ATR into the native C++ VwapEngine / PyQuantEngine.
    If current_atr is not yet calculated (e.g. initial bars), safely passes 0.0 fallback.
    """
    atr_val = (
        float(current_atr)
        if (current_atr is not None and not np.isnan(current_atr) and current_atr > 0.0)
        else 0.0
    )
    if hasattr(cpp_engine, "update_tick"):
        cpp_engine.update_tick(price, volume, atr_val)
    elif hasattr(cpp_engine, "push_tick"):
        cpp_engine.push_tick({
            "price": price,
            "volume": volume,
            "current_atr": atr_val,
        })
    return cpp_engine.snapshot() if hasattr(cpp_engine, "snapshot") else {}
