"""Version A money maths: one long NEAR round (USDC -> wNEAR -> USDC). Decimal and integer base units only.

Definitions (owner's spec, USD; INR = USD x the live FX rate):
    entry_cost    = USDC actually spent on the buy
    proceeds      = worst-case USDC of the sell quote (`minAmountOut`; 1Click fee and slippage already deducted)
    gas_total     = actual entry gas + estimated exit gas (the receipt uses the actual exit gas)
    taxable_gain  = max(proceeds - entry_cost, 0)       (conservative: gas is NOT deducted for tax)
    tax_reserve   = 0.312 x taxable_gain                 (30% + 4% cess; losses are not taxed and not offset)
    net_received  = proceeds - entry_cost - gas_total
    net_after_tax = net_received - tax_reserve

Exit rules, in priority order: PROFIT_EXIT (net_after_tax in INR >= target), STOP_LOSS (net_received <= -5% of
entry_cost), TIME_EXIT (at the cut-off). "Gas" here is every NEAR the deposit costs: the transaction fee plus the
storage registration of the new 1Click deposit address (0.00125 NEAR on USDC and wNEAR alike).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

USDC_DECIMALS = 6
WNEAR_DECIMALS = 24
TAX_RATE = Decimal('0.312')
STOP_LOSS_FRACTION = Decimal('0.05')
PREC = 50

PROFIT_EXIT, STOP_LOSS, TIME_EXIT, HOLD = 'PROFIT_EXIT', 'STOP_LOSS', 'TIME_EXIT', 'HOLD'

#: Mainnet NEAR cost of one deposit leg, used only as an ESTIMATE (paper fills, the exit-gas estimate before the
#: first live leg). Measured: wrap.near ft_transfer 0.0002170906627479 NEAR (tx AyNvWU5T..., 2026-11-11) and the
#: deposit address's storage registration, storage_balance_bounds.min = 0.00125 NEAR on both USDC and wrap.near.
FT_TRANSFER_GAS_NEAR = Decimal('0.0002170906627479')
STORAGE_REGISTRATION_NEAR = Decimal('0.00125')
LEG_NEAR_ESTIMATE = FT_TRANSFER_GAS_NEAR + STORAGE_REGISTRATION_NEAR


def _ctx():
    return localcontext(prec=PREC)


def to_units(amount: Decimal, decimals: int) -> int:
    """Human amount -> base units, rounded DOWN (never claim more than is there)."""
    with _ctx():
        return int((Decimal(amount) * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_DOWN))


def from_units(units: int | str, decimals: int) -> Decimal:
    with _ctx():
        return Decimal(int(units)) / (Decimal(10) ** decimals)


def usdc(units: int | str) -> Decimal:
    return from_units(units, USDC_DECIMALS)


def wnear(units: int | str) -> Decimal:
    return from_units(units, WNEAR_DECIMALS)


@dataclass(frozen=True)
class ExitCheck:
    entry_cost: Decimal   # USD
    proceeds: Decimal     # USD (worst case)
    gas_total: Decimal    # USD
    usd_inr: Decimal
    target_inr: Decimal
    now_ms: int
    time_exit_ms: int

    @property
    def taxable_gain(self) -> Decimal:
        with _ctx():
            return max(self.proceeds - self.entry_cost, Decimal(0))

    @property
    def tax_reserve(self) -> Decimal:
        with _ctx():
            return TAX_RATE * self.taxable_gain

    @property
    def net_received(self) -> Decimal:
        with _ctx():
            return self.proceeds - self.entry_cost - self.gas_total

    @property
    def net_after_tax(self) -> Decimal:
        with _ctx():
            return self.net_received - self.tax_reserve

    @property
    def net_received_inr(self) -> Decimal:
        with _ctx():
            return self.net_received * self.usd_inr

    @property
    def net_after_tax_inr(self) -> Decimal:
        with _ctx():
            return self.net_after_tax * self.usd_inr

    @property
    def stop_level(self) -> Decimal:
        """net_received at or below this (USD) triggers STOP_LOSS."""
        with _ctx():
            return -STOP_LOSS_FRACTION * self.entry_cost

    @property
    def profit_hit(self) -> bool:
        return self.net_after_tax_inr >= self.target_inr

    @property
    def stop_hit(self) -> bool:
        return self.net_received <= self.stop_level

    @property
    def time_hit(self) -> bool:
        return self.now_ms >= self.time_exit_ms

    @property
    def decision(self) -> str:
        """Priority A) PROFIT_EXIT, B) STOP_LOSS, C) TIME_EXIT."""
        if self.profit_hit:
            return PROFIT_EXIT
        if self.stop_hit:
            return STOP_LOSS
        if self.time_hit:
            return TIME_EXIT
        return HOLD

    def to_target_inr(self) -> Decimal:
        """How much more after-tax INR the profit exit needs (<= 0 once it passes)."""
        with _ctx():
            return self.target_inr - self.net_after_tax_inr

    def to_stop_inr(self) -> Decimal:
        """How far (INR of net_received) the round is above the stop (<= 0 once it triggers)."""
        with _ctx():
            return (self.net_received - self.stop_level) * self.usd_inr

    def to_dict(self) -> dict[str, Any]:
        return {'entry_cost': str(self.entry_cost), 'proceeds': str(self.proceeds), 'gas_total': str(self.gas_total),
                'usd_inr': str(self.usd_inr), 'taxable_gain': str(self.taxable_gain),
                'tax_reserve': str(self.tax_reserve), 'net_received': str(self.net_received),
                'net_after_tax': str(self.net_after_tax), 'net_after_tax_inr': str(self.net_after_tax_inr),
                'target_inr': str(self.target_inr), 'stop_level': str(self.stop_level), 'decision': self.decision}


def check(entry_cost_units: int, proceeds_units: int, gas_total_usd: Decimal, usd_inr: Decimal,
          target_inr: Decimal, now_ms: int, time_exit_ms: int) -> ExitCheck:
    if entry_cost_units <= 0:
        raise ValueError('entry cost must be positive')
    if proceeds_units < 0 or gas_total_usd < 0 or usd_inr <= 0:
        raise ValueError('negative proceeds/gas or non-positive FX')
    return ExitCheck(usdc(entry_cost_units), usdc(proceeds_units), Decimal(gas_total_usd), Decimal(usd_inr),
                     Decimal(target_inr), now_ms, time_exit_ms)


def profit_floor_units(entry_cost_units: int, gas_total_usd: Decimal, usd_inr: Decimal, target_inr: Decimal) -> int:
    """The smallest worst-case proceeds (USDC base units) for which PROFIT_EXIT passes. Used to re-check the rule on
    the fresh quote right before the sell. With a gain: net_after_tax = 0.688 (P - E) - G >= T / fx."""
    if target_inr <= 0:
        raise ValueError('profit target must be positive')
    with _ctx():
        e = usdc(entry_cost_units)
        p = e + (Decimal(target_inr) / Decimal(usd_inr) + Decimal(gas_total_usd)) / (1 - TAX_RATE)
        units = int((p * (Decimal(10) ** USDC_DECIMALS)).to_integral_value(rounding=ROUND_CEILING))
    # guard against any rounding edge: walk to the exact boundary of the real rule
    def ok(u: int) -> bool:
        return check(entry_cost_units, u, gas_total_usd, usd_inr, target_inr, 0, 1).profit_hit
    while not ok(units):
        units += 1
    while units > 0 and ok(units - 1):
        units -= 1
    return units


# ----- receipt -----

def _alloc(values: list[Decimal], total: Decimal, quantum: Decimal) -> list[Decimal]:
    """Rounds `values` to `quantum` so that they sum exactly to `total` rounded DOWN (largest remainder), so the
    displayed net is never above the exact net."""
    with _ctx():
        target = total.quantize(quantum, rounding=ROUND_FLOOR)
        rounded = [v.quantize(quantum, rounding=ROUND_HALF_EVEN) for v in values]
        diff = int(((target - sum(rounded, Decimal(0))) / quantum).to_integral_value())
        order = sorted(range(len(values)), key=lambda i: (values[i] - rounded[i]) * (1 if diff > 0 else -1),
                       reverse=True)
        step = quantum if diff > 0 else -quantum
        for k in range(abs(diff)):
            rounded[order[k % len(order)]] += step
        return rounded


@dataclass(frozen=True)
class Leg:
    """One executed leg as booked: units in/out, the Hyperliquid NEAR mid at the quote, the 1Click fee and the NEAR
    the deposit cost."""
    usdc_units: int
    wnear_units: int
    near_usd: Decimal
    app_fee_bps: int
    gas_near: Decimal
    at_ms: int
    tx_url: str
    journal_url: str


@dataclass(frozen=True)
class Receipt:
    n: int
    reason: str
    buy: Leg
    sell: Leg
    usd_inr: Decimal
    fx_source: str
    # exact values (USD)
    gross: Decimal
    fees: Decimal
    slippage: Decimal
    gas: Decimal
    received: Decimal
    tax: Decimal
    after_tax: Decimal

    @classmethod
    def build(cls, n: int, reason: str, buy: Leg, sell: Leg, usd_inr: Decimal, fx_source: str) -> Receipt:
        with _ctx():
            e, q, p = usdc(buy.usdc_units), wnear(buy.wnear_units), usdc(sell.usdc_units)
            if sell.wnear_units != buy.wnear_units:
                raise ValueError('a round sells exactly the wNEAR it bought')
            po, pc = buy.near_usd, sell.near_usd
            fee_buy = e * buy.app_fee_bps / 10_000
            fee_sell = q * pc * sell.app_fee_bps / 10_000
            slip_buy = e - q * po - fee_buy
            slip_sell = q * pc - p - fee_sell
            gross = q * (pc - po)
            gas = buy.gas_near * po + sell.gas_near * pc
            received = p - e - gas
            tax = TAX_RATE * max(p - e, Decimal(0))
            r = cls(n, reason, buy, sell, Decimal(usd_inr), fx_source, gross, fee_buy + fee_sell,
                    slip_buy + slip_sell, gas, received, tax, received - tax)
            if r.gross - r.fees - r.slippage - r.gas != r.received:
                raise AssertionError('receipt components do not sum to the net received')
            return r

    def lines(self) -> dict[str, tuple[Decimal, Decimal]]:
        """Displayed (USD 6 dp, INR 2 dp) values that add up exactly. Components are rounded with the largest
        remainder method to the net rounded DOWN; the tax is rounded UP and the after-tax line is RECEIVED - TAX as
        displayed, so neither the net nor the after-tax line is ever shown above its exact value."""
        with _ctx():
            out = {}
            for unit, q, fx in (('usd', Decimal('0.000001'), Decimal(1)), ('inr', Decimal('0.01'), self.usd_inr)):
                comps = [self.gross * fx, -self.fees * fx, -self.slippage * fx, -self.gas * fx]
                g, f, s, gs = _alloc(comps, self.received * fx, q)
                rec = g + f + s + gs
                tax = (self.tax * fx).quantize(q, rounding=ROUND_CEILING)
                out[unit] = (g, f, s, gs, rec, -tax, rec - tax)
            names = ('gross', 'fees', 'slippage', 'gas', 'received', 'tax', 'after_tax')
            return {k: (out['usd'][i], out['inr'][i]) for i, k in enumerate(names)}

    def render(self, *, wallet_usdc: Decimal, wallet_inr: Decimal, session_after_tax_inr: Decimal,
               ist: Any) -> str:
        from datetime import datetime  # noqa: PLC0415
        t = lambda ms: datetime.fromtimestamp(ms / 1000, ist).strftime('%Y-%m-%d %H:%M:%S IST')  # noqa: E731
        L = self.lines()
        def row(label: str, key: str) -> str:
            usd, inr = L[key]
            return f'  {label:<32}{usd:+.6f}    {inr:+.2f}'
        rule = '  ' + '-' * 64
        q = wnear(self.buy.wnear_units)
        return '\n'.join([
            f'  ===== ROUND #{self.n}  CLOSED: {self.reason} =====',
            f'  Buy : {t(self.buy.at_ms)}  {usdc(self.buy.usdc_units):.6f} USDC -> {q:.6f} wNEAR @ ${self.buy.near_usd}'
            f'   tx {self.buy.tx_url}',
            f'  Sell: {t(self.sell.at_ms)}  {q:.6f} wNEAR -> {usdc(self.sell.usdc_units):.6f} USDC @ ${self.sell.near_usd}'
            f'   tx {self.sell.tx_url}',
            rule,
            f'  {"":<32}{"USD":>9}    {"INR":>6}',
            row('Gross price move', 'gross'), row('1Click fees (2 legs)', 'fees'),
            row('Slippage (vs market price)', 'slippage'), row('Gas (actual, mainnet)', 'gas'),
            rule,
            row('RECEIVED IN WALLET (net)', 'received'), row('Tax to pay later at ITR (31.2%)', 'tax'),
            row('YOURS AFTER TAX', 'after_tax'),
            rule,
            f'  Wallet now: {wallet_usdc:.6f} USDC = INR {wallet_inr:.2f} | Session after tax: '
            f'{"+" if session_after_tax_inr >= 0 else "-"}INR {abs(session_after_tax_inr):.2f}',
            f'  FX: {self.usd_inr} INR/USD ({self.fx_source}) | Journal: {self.buy.journal_url} {self.sell.journal_url}',
        ])

    def to_dict(self) -> dict[str, Any]:
        exact = {k: str(getattr(self, k)) for k in ('gross', 'fees', 'slippage', 'gas', 'received', 'tax',
                                                    'after_tax')}
        shown = {k: [str(u), str(i)] for k, (u, i) in self.lines().items()}
        return {'n': self.n, 'reason': self.reason, 'usd_inr': str(self.usd_inr), 'fx_source': self.fx_source,
                'exact_usd': exact, 'displayed_usd_inr': shown,
                'buy': {k: str(v) for k, v in self.buy.__dict__.items()},
                'sell': {k: str(v) for k, v in self.sell.__dict__.items()}}
