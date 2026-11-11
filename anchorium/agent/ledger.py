"""Two-sided spot inventory (USDC + wNEAR): FIFO lots, Indian VDA tax reserve per disposal, round economics.

Units: token amounts are integers in base units (USDC 6 decimals, wNEAR 24). Prices, INR values and tax are
Decimal under a 50-digit context. No floats anywhere in this module.

Tax model (a reserve for the owner's ITR, not tax advice):
* Every swap disposes of the asset sold (USDC is a VDA too). proceeds_inr = market value in INR of what the swap
  delivered; the asset received is acquired at that same INR value (its FIFO cost basis).
* gain_inr = proceeds_inr - FIFO cost_inr of the units sold; tax_reserve = 31.2% x max(gain_inr, 0).
  Losses are never offset against gains (per disposal, per round, per session).

Round P&L (both directions) = change in total holdings (USDC + wNEAR) valued at the current price, minus actual
gas, converted at the live FX rate. A bull round spends USDC and later sells exactly the wNEAR it bought; a bear
round sells wNEAR and later spends exactly the USDC it received. So a closed bull round changes only USDC and a
closed bear round changes only wNEAR.

Close rule (profit-only): net_after_tax_inr = round_pnl_inr - sum(tax_reserve of the round's disposals)
must be >= PROFIT_TARGET_INR. Never a close below that.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Any

USDC, WNEAR = 'USDC', 'wNEAR'
DECIMALS = {USDC: 6, WNEAR: 24}
TAX_RATE = Decimal('0.312')  # 30% + 4% cess (Section 115BBH)
DEFAULT_PROFIT_TARGET_INR = Decimal('1.00')
PREC = 50


def units(asset: str, amount: Decimal) -> int:
    """Human amount -> base units, rounded down."""
    with localcontext() as ctx:
        ctx.prec = PREC
        return int((Decimal(amount) * (Decimal(10) ** DECIMALS[asset])).to_integral_value(rounding=ROUND_DOWN))


def human(asset: str, base_units: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PREC
        return Decimal(int(base_units)) / (Decimal(10) ** DECIMALS[asset])


def value_usd(asset: str, base_units: int, near_usd: Decimal) -> Decimal:
    """USD value of an amount: USDC at 1, wNEAR at the given NEAR price."""
    with localcontext() as ctx:
        ctx.prec = PREC
        return human(asset, base_units) * (Decimal(1) if asset == USDC else Decimal(near_usd))


def tax_on(gain_inr: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PREC
        return TAX_RATE * max(Decimal(gain_inr), Decimal(0))


@dataclass
class Lot:
    asset: str
    units: int
    cost_inr: Decimal
    acquired_ms: int
    tx: str

    def to_dict(self) -> dict[str, Any]:
        return {'asset': self.asset, 'units': str(self.units), 'cost_inr': str(self.cost_inr),
                'acquired_ms': self.acquired_ms, 'tx': self.tx}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Lot:
        return cls(d['asset'], int(d['units']), Decimal(d['cost_inr']), int(d['acquired_ms']), d['tx'])


@dataclass(frozen=True)
class Disposal:
    asset: str
    units: int
    proceeds_inr: Decimal
    cost_inr: Decimal
    gain_inr: Decimal
    tax_reserve_inr: Decimal
    at_ms: int
    tx: str
    lots_used: tuple[tuple[str, int, str], ...]  # (lot tx, units taken, cost taken)

    def to_dict(self) -> dict[str, Any]:
        return {'asset': self.asset, 'units': str(self.units), 'proceeds_inr': str(self.proceeds_inr),
                'cost_inr': str(self.cost_inr), 'gain_inr': str(self.gain_inr),
                'tax_reserve_inr': str(self.tax_reserve_inr), 'at_ms': self.at_ms, 'tx': self.tx,
                'lots_used': [list(x) for x in self.lots_used]}


class InsufficientInventory(ValueError):
    pass


@dataclass
class Ledger:
    """FIFO lots per asset. `dispose` consumes the oldest lots first; a partly used lot keeps the rest of its
    cost pro rata (the taken cost is rounded so that taken + remaining == original exactly)."""
    lots: dict[str, deque[Lot]] = field(default_factory=lambda: {USDC: deque(), WNEAR: deque()})
    disposals: list[Disposal] = field(default_factory=list)

    def balance(self, asset: str) -> int:
        return sum(lot.units for lot in self.lots[asset])

    def acquire(self, asset: str, base_units: int, cost_inr: Decimal, at_ms: int, tx: str) -> None:
        if base_units <= 0:
            raise ValueError('acquired amount must be positive')
        if cost_inr < 0:
            raise ValueError('cost basis cannot be negative')
        self.lots[asset].append(Lot(asset, int(base_units), Decimal(cost_inr), at_ms, tx))

    def _fifo(self, asset: str, base_units: int) -> tuple[Decimal, list[tuple[str, int, str]]]:
        """FIFO cost of `base_units` (oldest lots first) without changing anything."""
        if base_units <= 0:
            raise ValueError('disposed amount must be positive')
        if base_units > self.balance(asset):
            raise InsufficientInventory(f'{asset}: dispose {base_units} > balance {self.balance(asset)}')
        with localcontext() as ctx:
            ctx.prec = PREC
            need, cost, used = int(base_units), Decimal(0), []
            for lot in self.lots[asset]:
                take = min(need, lot.units)
                taken_cost = lot.cost_inr if take == lot.units else lot.cost_inr * take / lot.units
                cost += taken_cost
                used.append((lot.tx, take, str(taken_cost)))
                need -= take
                if not need:
                    break
        return cost, used

    def _disposal(self, asset: str, base_units: int, proceeds_inr: Decimal, at_ms: int, tx: str) -> Disposal:
        cost, used = self._fifo(asset, base_units)
        with localcontext() as ctx:
            ctx.prec = PREC
            gain = Decimal(proceeds_inr) - cost
            return Disposal(asset, int(base_units), Decimal(proceeds_inr), cost, gain, tax_on(gain), at_ms, tx,
                            tuple(used))

    def dispose(self, asset: str, base_units: int, proceeds_inr: Decimal, at_ms: int, tx: str) -> Disposal:
        d = self._disposal(asset, base_units, proceeds_inr, at_ms, tx)
        with localcontext() as ctx:
            ctx.prec = PREC
            q = self.lots[asset]
            for _, take, taken_cost in d.lots_used:  # consume exactly what _fifo priced
                lot = q[0]
                if take == lot.units:
                    q.popleft()
                else:
                    lot.cost_inr -= Decimal(taken_cost)
                    lot.units -= take
        self.disposals.append(d)
        return d

    def peek_dispose(self, asset: str, base_units: int, proceeds_inr: Decimal, at_ms: int = 0) -> Disposal:
        """What `dispose` would return, without changing the ledger."""
        return self._disposal(asset, base_units, proceeds_inr, at_ms, 'peek')

    def to_dict(self) -> dict[str, Any]:
        return {'lots': {a: [lot.to_dict() for lot in q] for a, q in self.lots.items()},
                'disposals': [d.to_dict() for d in self.disposals]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Ledger:
        led = cls()
        for a, rows in d['lots'].items():
            led.lots[a] = deque(Lot.from_dict(r) for r in rows)
        led.disposals = [Disposal(r['asset'], int(r['units']), Decimal(r['proceeds_inr']), Decimal(r['cost_inr']),
                                  Decimal(r['gain_inr']), Decimal(r['tax_reserve_inr']), int(r['at_ms']), r['tx'],
                                  tuple((x[0], int(x[1]), x[2]) for x in r['lots_used']))
                         for r in d['disposals']]
        return led


# ----- swaps and rounds -----

BULL_ROUND, BEAR_ROUND = 'BULL', 'BEAR'
#: The asset a round spends at open (and gets back at close).
ROUND_BASE = {BULL_ROUND: USDC, BEAR_ROUND: WNEAR}
ROUND_OTHER = {BULL_ROUND: WNEAR, BEAR_ROUND: USDC}


@dataclass(frozen=True)
class Swap:
    """One executed (or simulated) leg. `received` is what actually arrived (worst-case guaranteed in paper)."""
    sold_asset: str
    sold_units: int
    bought_asset: str
    bought_units: int
    near_usd: Decimal  # market (Hyperliquid) NEAR price at execution: used for INR values
    usd_inr: Decimal
    gas_near: Decimal  # actual NEAR spent on the deposit (gas + any storage registration)
    at_ms: int
    tx: str

    def received_inr(self) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = PREC
            return value_usd(self.bought_asset, self.bought_units, self.near_usd) * self.usd_inr

    def gas_usd(self) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = PREC
            return self.gas_near * self.near_usd

    def to_dict(self) -> dict[str, Any]:
        return {'sold_asset': self.sold_asset, 'sold_units': str(self.sold_units), 'bought_asset': self.bought_asset,
                'bought_units': str(self.bought_units), 'near_usd': str(self.near_usd), 'usd_inr': str(self.usd_inr),
                'gas_near': str(self.gas_near), 'at_ms': self.at_ms, 'tx': self.tx}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Swap:
        return cls(d['sold_asset'], int(d['sold_units']), d['bought_asset'], int(d['bought_units']),
                   Decimal(d['near_usd']), Decimal(d['usd_inr']), Decimal(d['gas_near']), int(d['at_ms']), d['tx'])


def apply_swap(ledger: Ledger, swap: Swap) -> Disposal:
    """Books a swap: dispose of the sold units at the INR value received, acquire the bought units at that value."""
    proceeds = swap.received_inr()
    d = ledger.dispose(swap.sold_asset, swap.sold_units, proceeds, swap.at_ms, swap.tx)
    ledger.acquire(swap.bought_asset, swap.bought_units, proceeds, swap.at_ms, swap.tx)
    return d


@dataclass(frozen=True)
class CloseCheck:
    """The profit-only test for closing a round with a given (worst-case) close quote."""
    kind: str
    pnl_usd: Decimal
    pnl_inr: Decimal
    gas_usd: Decimal
    tax_open_inr: Decimal
    tax_close_inr: Decimal
    net_after_tax_inr: Decimal
    target_inr: Decimal

    @property
    def clears(self) -> bool:
        return self.net_after_tax_inr >= self.target_inr

    def to_dict(self) -> dict[str, Any]:
        return {k: str(getattr(self, k)) for k in ('kind', 'pnl_usd', 'pnl_inr', 'gas_usd', 'tax_open_inr',
                                                   'tax_close_inr', 'net_after_tax_inr', 'target_inr')} | {
            'clears': self.clears}


def round_pnl_usd(kind: str, open_swap: Swap, close_received_units: int, near_usd_now: Decimal,
                  close_gas_near: Decimal) -> tuple[Decimal, Decimal]:
    """(round P&L in USD, total gas in USD) for closing `open_swap` with a quote delivering `close_received_units`
    of the round's base asset. Holdings change only in the base asset; valued at the current price."""
    with localcontext() as ctx:
        ctx.prec = PREC
        base = ROUND_BASE[kind]
        if open_swap.sold_asset != base:
            raise ValueError(f'{kind} round must open by selling {base}')
        delta_units = int(close_received_units) - open_swap.sold_units
        gas_usd = (open_swap.gas_near + Decimal(close_gas_near)) * Decimal(near_usd_now)
        return value_usd(base, delta_units, near_usd_now) - gas_usd, gas_usd


def close_check(ledger: Ledger, kind: str, open_swap: Swap, open_tax_inr: Decimal, close_received_units: int,
                near_usd_now: Decimal, usd_inr_now: Decimal, close_gas_near: Decimal, target_inr: Decimal,
                at_ms: int = 0) -> CloseCheck:
    """Would closing now with this quote clear the profit target after tax? Does not change the ledger."""
    with localcontext() as ctx:
        ctx.prec = PREC
        pnl_usd, gas_usd = round_pnl_usd(kind, open_swap, close_received_units, near_usd_now, close_gas_near)
        pnl_inr = pnl_usd * Decimal(usd_inr_now)
        base = ROUND_BASE[kind]
        proceeds = value_usd(base, close_received_units, near_usd_now) * Decimal(usd_inr_now)
        d = ledger.peek_dispose(ROUND_OTHER[kind], open_swap.bought_units, proceeds, at_ms)
        net = pnl_inr - Decimal(open_tax_inr) - d.tax_reserve_inr
        return CloseCheck(kind, pnl_usd, pnl_inr, gas_usd, Decimal(open_tax_inr), d.tax_reserve_inr, net,
                          Decimal(target_inr))


def wallet_inr(ledger: Ledger, near_usd: Decimal, usd_inr: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PREC
        return (value_usd(USDC, ledger.balance(USDC), near_usd) +
                value_usd(WNEAR, ledger.balance(WNEAR), near_usd)) * Decimal(usd_inr)
