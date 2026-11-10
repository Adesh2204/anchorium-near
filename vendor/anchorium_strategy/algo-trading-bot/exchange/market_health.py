"""Per-symbol public-data gates. No strategy, file fallback or exchange writes."""
from collections import Counter
from copy import deepcopy
import threading

from .base_exchange import ExchangeError, decimal

# Strategy bar intervals; one history never mixes intervals.
BAR_INTERVAL_MS = {'1m': 60000, '1h': 3600000}


def bar_step(candles):
    return BAR_INTERVAL_MS.get(candles[0].interval, 60000) if candles else 60000


def validate_history(symbol, candles, now_ms, required):
    """Validate actual completed UTC bars before they can reach the frozen brain."""
    if not symbol.startswith(('par:', 'pas:')):
        raise ExchangeError('MARKET_IDENTITY_INVALID')
    interval = candles[0].interval if candles else '1m'
    step = BAR_INTERVAL_MS.get(interval)
    for c in candles:
        if c.symbol != symbol or c.interval != interval or step is None:
            raise ExchangeError('STRATEGY_CANDLE_IDENTITY_MISMATCH')
        if (type(c.timestamp_ms) is not int or c.timestamp_ms % step or
                c.close_timestamp_ms not in {c.timestamp_ms+step-1,c.timestamp_ms+step}):
            raise ExchangeError('INVALID_CANDLE_BOUNDARIES')
        if c.timestamp_ms+step > now_ms:
            raise ExchangeError('FUTURE_OR_OPEN_COMPLETED_CANDLE')
        if c.received_timestamp_ms is not None and c.received_timestamp_ms<c.timestamp_ms+step:
            raise ExchangeError('POST_CLOSE_CANDLE_OBSERVATION_REQUIRED')
        o,h,l,p=(decimal(v,positive=True) for v in (c.open,c.high,c.low,c.close))
        if h < max(o,l,p) or l > min(o,h,p):
            raise ExchangeError('INVALID_CANDLE_PRICES')
        decimal(c.volume,nonnegative=True)
    if any(b.timestamp_ms-a.timestamp_ms!=step for a,b in zip(candles,candles[1:])):
        raise ExchangeError('STRATEGY_CANDLE_GAP_OR_DUPLICATE')
    if len(candles)<required:
        raise ExchangeError('INSUFFICIENT_COMPLETED_HISTORY')


class MarketHealthManager:
    STATES=('HEALTHY','STALE_CANDLE','STALE_BOOK','STALE_CONTEXT','INSUFFICIENT_HISTORY',
            'INVALID_DATA','REST_RECOVERY_PENDING','QUARANTINED','RECOVERING','UNKNOWN')

    def __init__(self,symbols,*,history_bars=300,max_candle_age_ms=120000,
                 max_book_age_ms=5000,max_context_age_ms=60000,retry_ms=30000):
        self.symbols=tuple(symbols)
        if (not self.symbols or any(not s.startswith(('par:', 'pas:')) for s in self.symbols)
                or len({s.split(':')[0] for s in self.symbols})!=1):
            raise ExchangeError('MARKET_IDENTITY_INVALID')
        self.history_bars=history_bars
        self.max_candle_age_ms=max_candle_age_ms
        self.max_book_age_ms=max_book_age_ms
        self.max_context_age_ms=max_context_age_ms
        self.retry_ms=retry_ms
        self._lock=threading.RLock()
        self.records={s:dict(symbol=s,state='UNKNOWN',data_state='UNKNOWN',healthy=False,
            quarantined=True,reasons=('MARKET_HEALTH_UNKNOWN',),evaluated_at_ms=None,
            recovery_attempts=0,restorations=0,quarantines=0,next_retry_at_ms=0) for s in self.symbols}
        self.pending=set()
        self.events=[]

    def _transition(self,symbol,state,now,reasons):
        r=self.records[symbol]
        if (r['state'],tuple(r['reasons']))!=(state,tuple(reasons)):
            old=r['state']
            if state=='HEALTHY' and old in {'QUARANTINED','RECOVERING','REST_RECOVERY_PENDING'}:
                r['restorations']+=1
            if state=='QUARANTINED' and old!='QUARANTINED':r['quarantines']+=1
            self.events.append(dict(symbol=symbol,from_state=old,state=state,
                                    timestamp_ms=now,reasons=tuple(reasons)))
            self.events=self.events[-2000:]
        r.update(state=state,reasons=tuple(reasons))

    def evaluate(self,symbol,candles,book,context,context_ms,now_ms,*,errors=None,source='PUBLIC_STREAM',observations=None):
        if symbol not in self.records:raise ExchangeError('MARKET_IDENTITY_INVALID')
        reasons=[];components={};clocks=dict(last_candle_time=candles[-1].timestamp_ms+bar_step(candles) if candles else None,
            last_orderbook_time=book.timestamp_ms if book else None,last_mark_time=context_ms,
            last_tick_time=book.timestamp_ms if book else None)
        if observations is not None:clocks.update(observations)
        try:validate_history(symbol,candles,now_ms,self.history_bars);components['history_health']='HEALTHY'
        except (ExchangeError,ValueError,TypeError) as exc:
            reasons.append(str(exc));components['history_health']='INSUFFICIENT_HISTORY' if 'INSUFFICIENT' in str(exc) else 'INVALID_DATA'
        for key,component,limit,state in [('last_candle_time','candle_health',self.max_candle_age_ms,'STALE_CANDLE'),
                ('last_orderbook_time','book_health',self.max_book_age_ms,'STALE_BOOK'),
                ('last_tick_time','tick_health',self.max_book_age_ms,'STALE_BOOK'),
                ('last_mark_time','context_health',self.max_context_age_ms,'STALE_CONTEXT')]:
            value=clocks[key];age=None if value is None else now_ms-value
            valid=age is not None and 0<=age<=limit
            components[component]='HEALTHY' if valid else state
            if not valid:reasons.append(key.upper()+'_STALE')
        try:
            if book is None or book.symbol!=symbol or not book.bids or not book.asks:raise ExchangeError('INVALID_ORDERBOOK')
            bid=decimal(book.bids[0].price,positive=True);ask=decimal(book.asks[0].price,positive=True)
            if bid>ask:raise ExchangeError('INVALID_ORDERBOOK_PRICES')
            for side in (book.bids,book.asks):
                for level in side:decimal(level.quantity,positive=True);decimal(level.price,positive=True)
            if list(book.bids)!=sorted(book.bids,key=lambda x:x.price,reverse=True) or list(book.asks)!=sorted(book.asks,key=lambda x:x.price):
                raise ExchangeError('INVALID_ORDERBOOK_PRICES')
            # Numeric spread validity; the unchanged 15 bps filter belongs to the scanner.
            spread=(ask-bid)/((ask+bid)/2)*10000
        except (ExchangeError,TypeError,ValueError) as exc:
            reasons.append(str(exc));components['book_health']='INVALID_DATA';spread=None
        try:
            if not isinstance(context,dict):raise ExchangeError('INVALID_ASSET_CONTEXT')
            for key in ('markPx','oraclePx'):decimal((context or {}).get(key),positive=True)
            decimal((context or {}).get('funding'))
        except (ExchangeError,TypeError,ValueError) as exc:
            reasons.append('INVALID_ASSET_CONTEXT');components['context_health']='INVALID_DATA'
        for component,error in (errors or {}).items():
            reasons.append('MARKET_REPAIR_FAILED:'+str(error));components[component]='INVALID_DATA'
        with self._lock:
            r=self.records[symbol]
            if symbol in self.pending:reasons.append('REST_RECOVERY_PENDING')
            reasons=tuple(dict.fromkeys(reasons))
            if not reasons:data_state='HEALTHY';state='HEALTHY'
            else:
                data_state=next((x for x in ['INVALID_DATA','INSUFFICIENT_HISTORY','STALE_CANDLE','STALE_BOOK','STALE_CONTEXT'] if x in components.values()),'REST_RECOVERY_PENDING')
                state='RECOVERING' if symbol in self.pending else 'QUARANTINED'
            self._transition(symbol,state,now_ms,reasons)
            if not reasons:r['consecutive_recovery_attempts']=0
            r.update(healthy=not reasons,quarantined=bool(reasons),data_state=data_state,
                overall_market_health=state,source=source,evaluated_at_ms=now_ms,
                clocks=clocks,ages_ms={k:None if v is None else now_ms-v for k,v in clocks.items()},
                completed_candles=len(candles),spread_bps=None if spread is None else str(spread),**components)
            return deepcopy(r)

    def begin_recovery(self,symbols,now_ms):
        with self._lock:
            for s in symbols:
                r=self.records[s];self.pending.add(s);r['recovery_attempts']+=1
                r['consecutive_recovery_attempts']=r.get('consecutive_recovery_attempts',0)+1
                r.update(healthy=False,quarantined=True)
                self._transition(s,'RECOVERING',now_ms,tuple(r['reasons'])+('REST_RECOVERY_PENDING',))

    def finish_recovery(self,symbols,now_ms,*,delay_hints=None):
        with self._lock:
            for s in symbols:
                self.pending.discard(s)
                r=self.records[s]
                # Failed/unchanged inputs back off to at most five minutes.
                delay=min(300000,self.retry_ms*2**min(4,max(0,r.get('consecutive_recovery_attempts',1)-1)))
                hint=(delay_hints or {}).get(s)
                if hint is not None:delay=min(300000,max(delay,int(hint)))
                r['next_retry_at_ms']=now_ms+delay

    def due(self,now_ms):
        with self._lock:
            return tuple(s for s,r in self.records.items() if not r['healthy'] and s not in self.pending and
                         r['evaluated_at_ms'] is not None and now_ms>=r['next_retry_at_ms'])

    def snapshot(self):
        with self._lock:return deepcopy(self.records)

    def report(self):
        with self._lock:
            return dict(markets=deepcopy(self.records),states=dict(Counter(r['state'] for r in self.records.values())),
                pending=sorted(self.pending),transitions=deepcopy(self.events),
                restored=sum(r['restorations'] for r in self.records.values()),
                quarantine_transitions=sum(r['quarantines'] for r in self.records.values()))
