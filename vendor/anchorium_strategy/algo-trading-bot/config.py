# ─────────────────────────────────────────────────────────────
# config.py  —  All tunable constants for the algo-trading-bot
# DO NOT hardcode these values anywhere else in the codebase.
# ─────────────────────────────────────────────────────────────

from venue_config import PROJECT_NAME, TRADING_VENUE, EXECUTION_MODE, ALLOW_LIVE_ORDERS

# ── UNIVERSE & CANDIDATE POOL ─────────────────────────────────
# Legacy default fallback (kept for offline tests & backward compatibility)
SYMBOL = "ENGINERSIN"          # Engineers India Ltd (Default fallback)
EXCHANGE = "NSE"               # unchanged
TOKEN = "4907"                 # Angel One instrument token for ENGINERSIN
SEGMENT = 1                   # unchanged (NSE equity)

# Configured pool of liquid candidate instruments evaluated by run_premarket_screener()
CANDIDATE_UNIVERSE = [
    # Stronger volatility/liquidity candidates
    {"symbol": "ENGINERSIN", "trading_symbol": "ENGINERSIN-EQ",
     "token": "4907", "exchange": "NSE"},

    {"symbol": "JIOFIN", "trading_symbol": "JIOFIN-EQ",
     "token": "18143", "exchange": "NSE"},

    {"symbol": "POWERGRID", "trading_symbol": "POWERGRID-EQ",
     "token": "14977", "exchange": "NSE"},

    {"symbol": "GAIL", "trading_symbol": "GAIL-EQ",
     "token": "4717", "exchange": "NSE"},

    {"symbol": "NTPC", "trading_symbol": "NTPC-EQ",
     "token": "11630", "exchange": "NSE"},

    {"symbol": "ITC", "trading_symbol": "ITC-EQ",
     "token": "1660", "exchange": "NSE"},

    {"symbol": "WIPRO", "trading_symbol": "WIPRO-EQ",
     "token": "3787", "exchange": "NSE"},

    {"symbol": "MRPL", "trading_symbol": "MRPL-EQ",
     "token": "2283", "exchange": "NSE"},

    {"symbol": "PETRONET", "trading_symbol": "PETRONET-EQ",
     "token": "11351", "exchange": "NSE"},

    {"symbol": "RECLTD", "trading_symbol": "RECLTD-EQ",
     "token": "15355", "exchange": "NSE"},

    {"symbol": "RVNL", "trading_symbol": "RVNL-EQ",
     "token": "9552", "exchange": "NSE"},

    # Newer/high-beta candidates (Tokens dynamically resolved from OpenAPIScripMaster.json)
    {"symbol": "ETERNAL", "trading_symbol": "ETERNAL-EQ",
     "token": "5097", "exchange": "NSE"},

    {"symbol": "SWIGGY", "trading_symbol": "SWIGGY-EQ",
     "token": "27066", "exchange": "NSE"},

    {"symbol": "MEESHO", "trading_symbol": "MEESHO-EQ",
     "token": "760229", "exchange": "NSE"},

    {"symbol": "PINELABS", "trading_symbol": "PINELABS-EQ",
     "token": "759820", "exchange": "NSE"},
]

# ── PRE-MARKET SCREENER CONFIG ────────────────────────────────
RUN_PREMARKET_SCREENER = True     # Master Directive: Active by default; set False only for manual single-symbol override
SCREEN_MIN_LTP = 150.0            # Minimum Last Traded Price (₹)
SCREEN_MAX_LTP = 350.0            # Maximum Last Traded Price (₹)
SCREEN_MIN_ATR_PCT = 0.0040       # Minimum ATR% (0.40% of LTP)
SCREEN_MAX_SPREAD_PCT = 0.0015    # Maximum normalized spread (0.15%)
SCREEN_MIN_DAILY_VOLUME = 5000000 # Minimum previous session volume (shares)
SCREEN_ATR_PERIOD = 14            # ATR period for 1-minute historical candles
SCREEN_EMA_FAST_PERIOD = 9        # Fast EMA period for trend alignment
SCREEN_EMA_SLOW_PERIOD = 21       # Slow EMA period for trend alignment
SCREEN_API_REQUEST_INTERVAL = 0.5 # Sequential pacing delay (seconds)
SCREEN_API_MAX_RETRIES = 3        # Retries for rate limits / broker errors
SCREEN_API_BACKOFF_BASE = 1.0     # Base backoff multiplier (seconds)
SCREEN_MIN_CANDLE_COUNT = 300     # Minimum 1-minute bars required in completed session
SCREEN_TOP_N = 3                  # Top N qualifiers for Watchlist (Default: 3, Min: 1)
WATCHLIST_MODE = True             # Enable Phase 5 multi-symbol execution architecture
WATCHLIST_SIZE = 3                # Watchlist maximum capacity
EXCLUSION_LIST = []               # Excluded symbols list

# ── PHASE 5 DYNAMIC STANDBY & ORCHESTRATION ───────────────────
STANDBY_ATR_THRESHOLD = 0.0035    # 0.35% ATR floor below which symbol enters STANDBY
STANDBY_CONFIRMATION_COUNT = 2    # Consecutive low readings required to enter STANDBY
STANDBY_RECOVERY_THRESHOLD = 0.0040 # 0.40% ATR required to recover back to ACTIVE
STANDBY_RECOVERY_CONFIRMATION_COUNT = 2 # Consecutive healthy readings required to recover
WATCHLIST_EVALUATION_INTERVAL = 1.0 # Pacing interval for watchlist evaluation (seconds)
MAX_OPEN_POSITIONS = 1            # STRICT GLOBAL PORTFOLIO LIMIT: At most 1 open position across all symbols

# ── INTRADAY ATR FIREWALL ─────────────────────────────────────
INTRADAY_ATR_MIN_PCT = 0.0040     # 0.40% minimum intraday ATR floor
ATR_RECHECK_INTERVAL_SECONDS = 60 # Informational cadence: one evaluation per distinct completed 1-minute bar
ATR_LOW_CONFIRMATION_COUNT = 2    # Consecutive low readings required to enter STANDBY
ATR_RECOVERY_CONFIRMATION_COUNT = 2 # Consecutive healthy readings required to recover

# ── FEED INTEGRITY & WATCHDOG ─────────────────────────────────
STALE_TICK_TIMEOUT_SECONDS = 15.0 # Max seconds without genuine tick before STALE
FEED_RECONNECT_WARMUP_TICKS = 3   # Fresh ticks required after reconnect before HEALTHY
MAX_QUEUE_SIZE = 10000            # Thread-safe ingestion queue size


# ── CAPITAL (absolute ₹ — intentionally fixed, user config) ───
TOTAL_CAPITAL = 20000.00      # ₹20,000 real money
WORKING_CAPITAL_PCT = 0.50       # 50% of total
DAILY_LOSS_LIMIT_PCT = 0.01      # Pilot: 1% of total capital
RISK_PER_SETUP_PCT = 0.01        # 1% of working capital per setup

WORKING_CAPITAL = 10000.00      # 50% of total
BACKUP_CAPITAL = 10000.00       # 50% reserve
RISK_PER_TRADE = 100.00        # 1% of working capital
DAILY_LOSS_LIMIT = 200.00        # Planned pilot daily loss allowance

# ── BROKERAGE CONFIG ──────────────────────────────
# Always assume FULL standard brokerage. The ₹0 new-user offer is
# capped at ₹500 cumulative — a bot burning 18 orders/day exhausts
# it in days. Treat any remaining discount as a bonus, never a gate input.
# Rates verified against angelone.in/exchange-transaction-charges (2026-09-16).
BROKERAGE_PCT = 0.001        # 0.1% of order value
BROKERAGE_CAP = 20.0         # ₹20 max per order
BROKERAGE_MIN = 5.0          # ₹5 min per order

# ── SLIPPAGE ──────────────────────────────────────
# Assume entry fills slightly worse than signal price.
# Calibrate from historical fill data as more days are logged.
SLIPPAGE_HAIRCUT_PCT = 0.0005  # 0.05% — conservative default

# ── SIGNAL BRAIN: PRICE-RELATIVE PARAMS ───────────────────────
MIN_CANDLES_BEFORE_TRADE = 15     # Warmup period (unitless, keep)
MIN_ATR_PCT = 0.0006              # ATR floor as % of close price (0.06%)
ATR_BAND_MULT = 1.50              # Pullback band = VWAP ± mult × ATR
VWAP_SLOPE_LOOKBACK = 10          # Candles for VWAP slope (unitless)
EMA_FAST = 9                      # Fast EMA period (unitless)
EMA_SLOW = 21                     # Slow EMA period (unitless)
ATR_PERIOD = 14                   # ATR period (unitless)

# ── BREAKOUT SIGNAL PARAMS ────────────────────────────────────
BREAKOUT_CLOSE_MULT = 0.50        # Close must be > VWAP + mult × ATR
VOL_SURGE_MULT = 1.50             # Volume must be > mult × 10-bar avg
VOL_MA_PERIOD = 10                # Rolling window for volume average

# ── EXECUTION ENGINE: ATR-RELATIVE TRADE MANAGEMENT ──────────
SL_ATR_MULT = 2.00                # Stop-loss distance in ATR units (Wick-Immune buffer)
TP_ATR_MULT = 4.00                # Profit target distance in ATR units (1:2 Risk-Reward)
BE_ATR_MULT = 1.00                # Breakeven trigger (profit ≥ mult × ATR)
TRAIL_ATR_MULT = 2.00             # Trailing stop gap in ATR units
REENTRY_ATR_MULT = 1.50           # Anti-spam: price must be > VWAP + mult×ATR
REENTRY_COOLDOWN_CANDLES = 10     # Post-SL cooldown in candles (unitless)

# ── ADAPTIVE FRICTION GATE ────────────────────────────────────
MIN_NET_RR = 1.2                  # Legacy alias (used by fallback imports)
MIN_NET_RR_NORMAL = 1.2           # R:R floor in normal volatility (was 1.5)
MIN_NET_RR_LOW_VOL = 0.8          # R:R floor when ATR < LOW_VOL_ATR_PCT
LOW_VOL_ATR_PCT = 0.0010          # Below this = "low vol regime"
MIN_NET_PROFIT = 5.0              # Absolute floor in rupees for minimum net gain
MIN_NET_PROFIT_RS = 5.0           # Alias for MIN_NET_PROFIT (explicit name)
# MIN_NET_PROFIT_MULTIPLIER = 2.0
# DEPRECATED — removed from all logic paths 2026-09-07.
# Replaced by MIN_NET_PROFIT_RS inside friction_gate_check().
# Do not uncomment or reference in any logic.

# ── PYRAMIDING ────────────────────────────────────────────────
TRANCHE_1_PCT = 0.33              # 33% of working capital
TRANCHE_2_PCT = 0.33              # 33% of working capital
TRANCHE_3_PCT = 0.34              # 34% of working capital
TRANCHE_FRACTIONS = [TRANCHE_1_PCT, TRANCHE_2_PCT, TRANCHE_3_PCT]
MAX_TRANCHES = 3
PYRAMID_TRIGGER_ATR_MULT = 2.00   # 2.0x ATR above base/previous tranche
TRANCHE_1_CAPITAL = 3300.00       # Nominal base tranche capital (33% of ₹10,000 working cap)

# ── SHORT SIGNAL PARAMS ──────────────────────────────────────
SHORT_BAND_MULT = 1.50            # Same as ATR_BAND_MULT (symmetric)
SHORT_CLOSE_MULT = 0.50           # Breakdown: close < VWAP - mult × ATR
KNIFE_UP_MULT = 1.00              # Knife-up filter for short signals

# ── DAILY BIAS DETECTOR ──────────────────────────────────────
BIAS_VWAP_GAP_PCT = 0.0005        # Threshold for VWAP position check
BIAS_VOTES_NEEDED = 2             # Min confluence votes to declare bias

# ── TIMING ────────────────────────────────────────────────────
EOD_SQUAREOFF_TIME = "15:00"      # Flatten before cash RMS; confirm broker flat
ENTRY_CUTOFF_TIME = "14:30"
MARKET_OPEN_TIME = "09:15"        # HH:MM IST

# ── TRADING MODE ─────────────────────────────────────────────
# LIVE_MODE = False → Paper trading (default, safe)
# LIVE_MODE is retained for legacy offline research fixtures only.
# It cannot enable Angel One execution in this repository. All exchange writes
# pass order_guard, which blocks every venue until its migration is implemented.
LIVE_MODE = False

# Execution infrastructure only; no signal/indicator parameters overridden.
MAX_NEW_SETUPS = 2
SCALE_IN_ENABLED = False
EXECUTION_ENVIRONMENT = "PAPER"
STRATEGY_VERSION = "vwap-1m-bff940ac5ed3"
INSTRUMENT_MAX_AGE_HOURS = 24
EXECUTION_QUOTE_MAX_AGE_SECONDS = 5.0
CLOSED_SIGNAL_MAX_AGE_SECONDS = 120.0
BEST_LEVEL_MAX_PARTICIPATION = 0.10
MAX_QUOTE_DISCONTINUITY_PCT = 0.005
CASH_BUFFER_RS = 20.0
NSE_TRANSACTION_RATE = 0.000030699
SEBI_TURNOVER_RATE = 0.000001
IPFT_RATE = 0.000000001
INTRADAY_STT_SELL_RATE = 0.00025
INTRADAY_STAMP_BUY_RATE = 0.00003
GST_RATE = 0.18
BROKER_ORDER_TIMEOUT_SECONDS = 3.0
BROKER_POLL_SECONDS = 0.20
LIVE_VALIDATION_APPROVED = False
LIVE_ACTIVATION_EVIDENCE_FILE = None
BROKER_CAPABILITY_FILE = None   # Dated account-specific verification; never inferred
INSTRUMENT_MASTER_FILE = None # JSON envelope: source_timestamp, records, intraday_symbols
MAX_DISCOVERY_CANDIDATES = 60
AUTO_DISCOVER_UNIVERSE = True
DISCOVERY_ALLOW_CONFIG_FALLBACK = False  # Retired: startup never uses a configured stock list
DISCOVERY_CACHE_DIR = None     # Default: data/cache/discovery/<completed session>/
DISCOVERY_HTTP_TIMEOUT_SECONDS = 15.0
DISCOVERY_MAX_DOWNLOAD_BYTES = 20_000_000
DISCOVERY_MIN_MASTER_RECORDS = 100
DISCOVERY_MIN_REFERENCE_RECORDS = 100
DISCOVERY_MIN_SNAPSHOT_RECORDS = 100
DISCOVERY_EQUITY_REFERENCE_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
DISCOVERY_BHAVCOPY_URL = "https://archives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{session}_F_0000.csv.zip"
# Alternate official endpoints serve the same datasets; they never change stock membership.
DISCOVERY_EQUITY_REFERENCE_ALTERNATES = ("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",)
DISCOVERY_BHAVCOPY_ALTERNATES = ("https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{session}_F_0000.csv.zip",)
DISCOVERY_HISTORY_INTERVAL_SECONDS = 1.1  # Shared across deep screening, retries and RVOL calls
DISCOVERY_HISTORY_RATE_BACKOFF_SECONDS = 5.0
DISCOVERY_HISTORY_RATE_BACKOFF_MAX_SECONDS = 30.0
DISCOVERY_HISTORY_MAX_CONSECUTIVE_RATE_LIMITS = 6
DISCOVERY_HISTORY_MAX_RUNTIME_SECONDS = 600.0
DISCOVERY_RVOL_MAX_RUNTIME_SECONDS = 60.0  # Optional profiles cannot delay completed stock selection indefinitely
OPPORTUNITY_RVOL_SESSIONS = 10
REPLAY_HALF_SPREAD_PCT = 0.00025 # Explicit candle-only research approximation

# PAPER runtime repair: infrastructure settings, not strategy threshold changes.
HISTORY_CACHE_DIR = None  # data/cache/history; shared by regular and read-only runs
HISTORY_REQUEST_INTERVAL_SECONDS = 1.5
HISTORY_REQUESTS_PER_MINUTE = 40
HISTORY_REQUESTS_PER_HOUR = 1000
HISTORY_RECOVERY_RETRY_SECONDS = 30.0
HISTORY_MAX_REPAIR_RANGES = 8  # fragmented history blocks safely, never causes a request storm
OPPORTUNITY_RVOL_MIN_SESSIONS = 3
RESERVE_WATCHLIST_SIZE = 3  # at most 3 primary + 3 reserve subscriptions
RESERVE_CONFIRMATION_CANDLES = 2
RESERVE_PROMOTION_COOLDOWN_SECONDS = 120.0
REJECTION_LOG_INTERVAL_SECONDS = 60.0
PAPER_FILL_SLIPPAGE_PCT = 0.0005  # independent simulated fill assumption, NOT the economic haircut
ORDER_LIMIT_ALLOWANCE_PCT = 0.0005  # independent price tolerance; limits never derived from economic haircut
SHADOW_RESEARCH_ENABLED = True  # observations only, no order access or credited P&L

# ── RECONCILIATION ───────────────────────────────────────────
RECONCILIATION_TOLERANCE_INTRADAY = 0.50   # ₹0.50 tolerance for per-trade gross P&L vs broker realised delta
RECONCILIATION_TOLERANCE_EOD = 1.00        # ₹1.00 tolerance for EOD account net P&L vs capital movement
RECONCILIATION_MAX_RETRIES = 3             # Max retries for delayed broker position state
RECONCILIATION_RETRY_INTERVAL = 0.5        # Seconds between position refresh retries
