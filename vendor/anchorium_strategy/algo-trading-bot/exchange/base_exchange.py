"""Strategy-facing contracts. No exchange SDK or legacy broker imports."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_FLOOR, ROUND_CEILING
from typing import Protocol

from safety_errors import SafetyError


class ExchangeError(SafetyError):
    pass


class VenueMismatch(ExchangeError):
    pass


def decimal(value, *, positive=False, nonnegative=False):
    if isinstance(value, bool):
        raise ExchangeError('INVALID_DECIMAL')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ExchangeError('INVALID_DECIMAL') from exc
    if (not result.is_finite() or (positive and result <= 0)
            or (nonnegative and result < 0)):
        raise ExchangeError('INVALID_DECIMAL')
    return result


def decimal_string(value):
    text = format(decimal(value), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


@dataclass(frozen=True)
class MarketSpec:
    symbol: str
    size_decimals: int
    max_leverage: int
    asset_id: int
    margin_tiers: tuple=()

    def __post_init__(self):
        if (not isinstance(self.symbol, str) or ':' not in self.symbol
                or isinstance(self.size_decimals, bool) or not isinstance(self.size_decimals, int)
                or not 0 <= self.size_decimals <= 6
                or isinstance(self.max_leverage, bool) or not isinstance(self.max_leverage, int)
                or self.max_leverage < 1 or isinstance(self.asset_id, bool)
                or not isinstance(self.asset_id, int) or self.asset_id < 0):
            raise ExchangeError('INVALID_MARKET_SPEC')
        tiers=self.margin_tiers or ((Decimal(0),self.max_leverage),)
        validated=[]
        for lower,leverage in tiers:
            bound=decimal(lower,nonnegative=True)
            if type(leverage) is not int or leverage<1:
                raise ExchangeError('INVALID_MARGIN_TIER')
            if validated and (bound<=validated[-1][0] or leverage>validated[-1][1]):
                raise ExchangeError('INVALID_MARGIN_TIER')
            validated.append((bound,leverage))
        if validated[0][0]!=0 or validated[0][1]!=self.max_leverage:
            raise ExchangeError('INVALID_MARGIN_TIER')
        object.__setattr__(self,'margin_tiers',tuple(validated))

    def maximum_leverage_at(self, notional):
        value=decimal(notional,nonnegative=True)
        return next(lev for bound,lev in reversed(self.margin_tiers) if value>=bound)

    def maintenance_margin(self, notional):
        value=decimal(notional,nonnegative=True)
        total=Decimal(0)
        for i,(bound,lev) in enumerate(self.margin_tiers):
            upper=self.margin_tiers[i+1][0] if i+1<len(self.margin_tiers) else value
            band=max(Decimal(0),min(value,upper)-bound)
            total+=band/(2*lev)
        return total

    @property
    def size_step(self):
        return Decimal(1).scaleb(-self.size_decimals)

    def normalize_size(self, quantity):
        try:
            result = decimal(quantity, positive=True).quantize(self.size_step, rounding=ROUND_DOWN)
        except InvalidOperation as exc:
            raise ExchangeError('INVALID_SIZE') from exc
        if result <= 0:
            raise ExchangeError('SIZE_BELOW_LOT')
        return result

    def normalize_price(self, price, side):
        if side not in {'BUY', 'SELL'}:
            raise ExchangeError('INVALID_SIDE')
        value = decimal(price, positive=True)
        # Integer prices are allowed; non-integers have at most five significant
        # figures and 6 - szDecimals decimal places. Preserve the caller's limit:
        # buy rounds down, sell rounds up. No strategy levels are recalculated.
        places = min(6 - self.size_decimals, max(0, 4 - value.adjusted()))
        try:
            result = value.quantize(Decimal(1).scaleb(-places),
                                    rounding=ROUND_FLOOR if side == 'BUY' else ROUND_CEILING)
        except InvalidOperation as exc:
            raise ExchangeError('INVALID_PRICE') from exc
        if result <= 0:
            raise ExchangeError('PRICE_BELOW_PRECISION')
        return result

    def normalize_order(self, side, quantity, price, *, reduce_only=False, tif='Gtc'):
        if type(reduce_only) is not bool or tif not in {'Gtc', 'Ioc', 'Alo'}:
            raise ExchangeError('INVALID_ORDER_OPTIONS')
        size = self.normalize_size(quantity)
        limit = self.normalize_price(price, side)
        if size * limit < Decimal('10'):
            raise ExchangeError('ORDER_BELOW_MINIMUM_NOTIONAL')
        return NormalizedOrder(self.symbol, side, size, limit, self.asset_id, reduce_only, tif)


@dataclass(frozen=True)
class NormalizedOrder:
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    asset_id: int
    reduce_only: bool = False
    tif: str = 'Gtc'

    def to_dict(self):
        return dict(symbol=self.symbol, side=self.side, quantity=decimal_string(self.quantity),
                    price=decimal_string(self.price), asset_id=self.asset_id,
                    reduce_only=self.reduce_only, tif=self.tif)


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    timestamp_ms: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    received_timestamp_ms: int | None = field(default=None,compare=False)
    processed_timestamp_ms: int | None = field(default=None,compare=False)


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp_ms: int
    bid: Decimal
    ask: Decimal
    bid_quantity: Decimal
    ask_quantity: Decimal
    mid: Decimal
    mark: Decimal
    oracle: Decimal
    hourly_funding: Decimal
    open_interest: Decimal | None = None
    daily_notional_volume: Decimal | None = None
    mark_timestamp_ms: int | None = None
    received_timestamp_ms: int | None = field(default=None,compare=False)
    processed_timestamp_ms: int | None = field(default=None,compare=False)

    def __post_init__(self):
        if type(self.timestamp_ms) is not int or self.timestamp_ms<0:
            raise ExchangeError('INVALID_QUOTE_TIMESTAMP')
        if self.mark_timestamp_ms is not None and (type(self.mark_timestamp_ms) is not int or self.mark_timestamp_ms<0):
            raise ExchangeError('INVALID_MARK_TIMESTAMP')
        for name in ('bid','ask','mid','mark','oracle'):
            object.__setattr__(self,name,decimal(getattr(self,name),positive=True))
        for name in ('bid_quantity','ask_quantity'):
            object.__setattr__(self,name,decimal(getattr(self,name),nonnegative=True))
        object.__setattr__(self,'hourly_funding',decimal(self.hourly_funding))
        for name in ('open_interest','daily_notional_volume'):
            if getattr(self,name) is not None:
                object.__setattr__(self,name,decimal(getattr(self,name),nonnegative=True))
        if self.ask<self.bid or self.mid!=(self.bid+self.ask)/2:
            raise ExchangeError('INVALID_QUOTE_PRICES')


@dataclass(frozen=True)
class Candle:
    symbol: str
    interval: str
    timestamp_ms: int
    close_timestamp_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    received_timestamp_ms: int | None = field(default=None,compare=False)
    processed_timestamp_ms: int | None = field(default=None,compare=False)


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: Decimal
    entry_price: Decimal | None
    unrealized_pnl: Decimal
    margin_used: Decimal
    liquidation_price: Decimal | None
    leverage: dict | None = None


@dataclass(frozen=True)
class Fill:
    symbol: str
    timestamp_ms: int
    trade_id: str
    order_id: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_token: str
    closed_pnl: Decimal | None = None
    start_position: Decimal | None = None
    source: str = 'UNSPECIFIED_FILL'
    exchange_ts: str | None = field(default=None,compare=False)
    received_ts: str | None = field(default=None,compare=False)
    processed_ts: str | None = field(default=None,compare=False)

    @property
    def identity(self):
        return self.symbol, self.timestamp_ms, self.trade_id, self.order_id


@dataclass(frozen=True)
class AccountEquity:
    reported_account_value: Decimal
    collateral_balance: Decimal
    margin_used: Decimal
    # Unified account states do not report a meaningful per-DEX withdrawal
    # balance. Unknown is distinct from zero and from per-coin trading capacity.
    withdrawable: Decimal | None
    valuation_source: str = 'clearinghouseState.marginSummary.accountValue'


class MarketData(Protocol):
    def get_candles(self, symbol, start_ms, end_ms, *, interval='1m', now_ms=None) -> list[Candle]: ...
    def get_quote(self, symbol) -> Quote: ...
    def get_orderbook(self, symbol) -> OrderBook: ...


class Execution(Protocol):
    def submit(self, request): ...
    def place_order(self, symbol, side, quantity, price, *, reduce_only=False, tif='Gtc'): ...
    def cancel_order(self, symbol, order_id): ...


class Account(Protocol):
    def get_position(self, symbol) -> Position | None: ...
    def get_fills(self) -> list[Fill]: ...
    def get_equity(self) -> AccountEquity: ...
