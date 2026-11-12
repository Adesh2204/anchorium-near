"""The autonomous loop for one signal: guard check -> 1Click quote -> slippage gate -> on-chain journal ->
(LIVE only) deposit + status tracking -> on-chain outcome.

Spot, long-only mapping (the signal comes from perpetual-futures market data; execution is spot):
* LONG signal                      -> BUY:  USDC -> asset
* SHORT signal while a position is open on the guard -> SELL: asset -> USDC (the agent's exit rule)
* SHORT signal without a position  -> skipped locally (a spot account cannot short)

The exit rule (sell on the next SHORT signal from the same unchanged brain) is new agent logic. The original
trader's stop/target management lives in legacy modules that are not vendored (see vendor/.../SOURCE.md).
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from anchorium.agent.checkpoint import TERMINAL, CheckpointStore
from anchorium.agent.signals import Signal
from anchorium.near import live as live_gates
from anchorium.near.guard_client import GuardReader, GuardWriter, account_url, tx_url
from anchorium.near.intents import (FUNDS_LOCATION, OneClickClient, OneClickError, QuoteRequest,
                                    worst_case_slippage_bps)

USDC = 'nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1'
DRY_RUN_PLACEHOLDER_RECIPIENT = '0' * 64  # valid implicit account; dry quotes never execute
#: Local skips: the guard view already says no and journaling would only spend the daily decision cap.
LOCAL_SKIP_REASONS = ('POSITION_ALREADY_OPEN', 'COOLDOWN', 'MAX_OPEN_POSITIONS')


@dataclass
class AgentConfig:
    network: str = 'testnet'
    order_usd: Decimal = Decimal('2.00')
    quote_tolerance_bps: int = 10
    execution: live_gates.ExecutionConfig = field(
        default_factory=lambda: live_gates.ExecutionConfig(live=False, max_trade_usd=Decimal('0')))
    trader_account: str | None = None  # mainnet account that signs LIVE deposits and receives outputs
    session_label: str | None = None  # optional: mixed into signal hashes for a distinct demo session
    #: Paper fills at the quote's guaranteed `minAmountOut` instead of `amountOut` (version A: worst case only).
    paper_fill_worst_case: bool = False
    #: Autonomous mode: the session total counts BUY deposits only (exits are bounded by the entry they close).
    live_total_entries_only: bool = False

    @property
    def mode(self) -> str:
        return self.execution.mode


def emit(event: dict[str, Any], stream=sys.stdout) -> None:
    stream.write(json.dumps(event, separators=(',', ':'), default=str) + '\n')
    stream.flush()


class Agent:
    def __init__(self, config: AgentConfig, reader: GuardReader, oneclick: OneClickClient, store: CheckpointStore,
                 writer: GuardWriter | None = None, trader_near=None,
                 confirm: Callable[..., None] = live_gates.require_typed_confirmation,
                 log: Callable[[dict[str, Any]], None] = emit):
        if config.execution.live and (writer is None or trader_near is None or not config.trader_account):
            raise live_gates.LiveGateError('LIVE needs the agent key, TRADER_ACCOUNT_ID and its mainnet key')
        self.config, self.reader, self.oneclick, self.store = config, reader, oneclick, store
        self.writer, self.trader_near, self.confirm, self.log = writer, trader_near, confirm, log

    # ----- helpers -----

    def _hash(self, signal: Signal) -> str:
        mode = self.config.mode if not self.config.session_label else f'{self.config.mode}:{self.config.session_label}'
        return signal.signal_hash(mode)

    def _open_position(self, asset: str) -> dict[str, Any] | None:
        return next((p for p in self.reader.positions() if p['asset'] == asset and p['state'] == 'OPEN'), None)

    def _near_yocto(self) -> str | None:
        """The trader's NEAR balance (yocto), read around each LIVE deposit to measure its real NEAR cost (gas plus
        any storage registration batched into the transfer). A failed read never blocks the trade."""
        try:
            return str(int(self.trader_near.account(self.config.trader_account).amount))
        except Exception:  # noqa: BLE001
            return None

    def _recipient(self) -> str:
        return self.config.trader_account or DRY_RUN_PLACEHOLDER_RECIPIENT

    # ----- main entry -----

    def process(self, signal: Signal, *, compromised: bool = False,
                min_proceeds_units: int | None = None) -> dict[str, Any]:
        """Runs one signal through the full flow. `compromised=True` simulates a faulty agent for the demo: it
        skips its own checks, asks for 2.5x the cap and claims the trade is allowed. The contract must reject it.

        `min_proceeds_units` (SELL only, USDC base units): the agent declines the exit before journaling unless the
        quote's guaranteed output (`minAmountOut`) is at least this much. Used by the paper session's profit target."""
        h = self._hash(signal) if not compromised else signal.signal_hash(f'{self.config.mode}:COMPROMISED')
        row = self.store.get(h) or self.store.create(h, self.config.mode, signal.coin, {'signal': signal.to_dict()})
        if row['state'] in TERMINAL or row['state'] == 'NEEDS_REVIEW':
            self.log({'event': 'already_processed', 'signal_hash': h, 'state': row['state'],
                      **{k: row['data'].get(k) for k in ('decision_id', 'decision_tx_url')}})
            return row
        if row['state'] in ('DECISION_PENDING', 'DECISION_RECORDED', 'DEPOSITED', 'OUTCOME_PENDING'):
            return self._resume(h, row)

        self.log({'event': 'signal', 'signal_hash': h, 'mode': self.config.mode, **signal.to_dict()})
        asset = signal.spot_asset
        self.oneclick.tokens(refresh=True)  # the slippage gate needs current reference prices, not a startup cache
        policy = self.reader.policy()
        entry_cost_cents = None
        if signal.direction == 'LONG':
            side, origin, destination = 'BUY', USDC, asset
            usdc = self.oneclick.token(USDC)
            order_usd = self.config.order_usd * (Decimal('2.5') if compromised else 1)
            amount = usdc.to_units(order_usd)
        else:
            position = self._open_position(asset)
            if position is None:
                return self._skip(h, 'SHORT_SIGNAL_WITHOUT_SPOT_POSITION')
            side, origin, destination, amount = 'SELL', asset, USDC, int(position['amount'])
            entry_cost_cents = position.get('cost_usd_cents')
            if self.config.execution.live:  # never try to sell more than the account actually holds
                token_contract = self.oneclick.token(asset).contract_address
                amount = min(amount, int(self.trader_near.ft_balance(token_contract, self.config.trader_account)))
                if amount <= 0:
                    return self._skip(h, 'NO_TOKEN_BALANCE_TO_SELL')
        origin_t, dest_t = self.oneclick.token(origin), self.oneclick.token(destination)
        notional_cents = int(origin_t.from_units(amount) * origin_t.price_usd * 100)

        verdict = self.reader.check_trade(asset, side, notional_cents, None)
        self.log({'event': 'guard_check', 'signal_hash': h, 'asset': asset, 'side': side,
                  'notional_usd_cents': notional_cents, **verdict})
        if not verdict['allowed'] and verdict['reason'] in LOCAL_SKIP_REASONS and not compromised:
            return self._skip(h, f'GUARD_PRECHECK:{verdict["reason"]}')

        quote, slippage, agent_ok, agent_reason = None, 0, verdict['allowed'], 'OK'
        if not verdict['allowed']:
            agent_reason = f'GUARD_PRECHECK:{verdict["reason"]}'
        else:
            try:
                quote = self.oneclick.quote(QuoteRequest(
                    origin_asset=origin, destination_asset=destination, amount=amount,
                    recipient=self._recipient(), refund_to=self._recipient(),
                    slippage_tolerance_bps=self.config.quote_tolerance_bps, dry=not self.config.execution.live))
                slippage = worst_case_slippage_bps(quote, origin_t, dest_t)
                if side == 'SELL':
                    notional_cents = int(dest_t.from_units(quote.amount_out) * dest_t.price_usd * 100)
                    if min_proceeds_units is not None and quote.min_amount_out < min_proceeds_units:
                        self.log({'event': 'below_profit_floor', 'signal_hash': h,
                                  'min_amount_out': str(dest_t.from_units(quote.min_amount_out)),
                                  'required': str(dest_t.from_units(min_proceeds_units))})
                        return self._skip(h, 'BELOW_PROFIT_FLOOR')
                if slippage > policy['max_slippage_bps']:
                    agent_ok, agent_reason = False, f'SLIPPAGE_ABOVE_MAX:{slippage}'
                self.log({'event': 'quote', 'signal_hash': h, 'dry': quote.dry, 'quote_id': quote.quote_id,
                          'amount_in': str(origin_t.from_units(quote.amount_in)), 'in_symbol': origin_t.symbol,
                          'amount_out': str(dest_t.from_units(quote.amount_out)), 'out_symbol': dest_t.symbol,
                          'min_amount_out': str(dest_t.from_units(quote.min_amount_out)),
                          'worst_case_slippage_bps': slippage, 'policy_max_slippage_bps': policy['max_slippage_bps'],
                          'app_fee_bps': quote.app_fee_bps, 'deposit_address': quote.deposit_address})
            except OneClickError as exc:
                agent_ok, agent_reason = False, f'QUOTE_FAILED:{str(exc)[:100]}'
                self.log({'event': 'quote_failed', 'signal_hash': h, 'error': str(exc)})
        if compromised:
            agent_ok, agent_reason = True, 'COMPROMISED_AGENT_SIMULATION'

        # LIVE quotes carry the deposit address so anyone can rebuild the intent timeline from the chain alone
        # (GET /v0/status?depositAddress=...). Format: "<correlationId>:<depositAddress>".
        quote_id = 'none' if not quote else (
            f'{quote.quote_id}:{quote.deposit_address}' if quote.deposit_address else quote.quote_id)
        args = {'signal_hash': h, 'asset': asset, 'side': side, 'mode': self.config.mode,
                'notional_usd_cents': notional_cents, 'slippage_bps': slippage,
                'quote_id': quote_id, 'agent_allowed': agent_ok,
                'agent_reason': agent_reason[:128], 'rationale_hash': None}
        quote_data = quote.raw if quote else None
        self.store.advance(h, 'DECISION_PENDING', side=side, decision_args=args, quote=quote_data,
                           ref_prices_usd={'in': str(origin_t.price_usd), 'out': str(dest_t.price_usd)},
                           entry_cost_cents=entry_cost_cents)
        if self.writer is None:
            self.log({'event': 'would_journal', 'signal_hash': h, 'contract': self.reader.contract_id,
                      'method': 'record_decision', 'args': args,
                      'note': 'Path A has no agent key: this exact call is what the agent would sign'})
            return self.store.advance(h, 'SKIPPED', skip_reason='NO_AGENT_KEY_PATH_A')
        return self._resume(h, self.store.require(h))

    # ----- resumable steps -----

    def _resume(self, h: str, row: dict[str, Any]) -> dict[str, Any]:
        data = row['data']
        if self.writer is None:
            raise RuntimeError('resuming a journaled leg needs the agent key')
        if row['state'] == 'DECISION_PENDING':
            written = self.writer.record_decision(**data['decision_args'])
            decision = written.result
            row = self.store.advance(h, 'DECISION_RECORDED', decision=decision, decision_id=decision['id'],
                                     decision_tx=written.tx_hash,
                                     decision_tx_url=tx_url(self.reader.network, written.tx_hash))
            self.log({'event': 'decision_journaled', 'signal_hash': h, 'decision_id': decision['id'],
                      'allowed': decision['allowed'], 'reason': decision['reason'],
                      'agent_allowed': decision['agent_allowed'],
                      'verdict_override': any('"verdict_override"' in line for line in written.logs),
                      'explorer': tx_url(self.reader.network, written.tx_hash)})
            data = row['data']
        if row['state'] == 'DECISION_RECORDED':
            if not data['decision']['allowed']:
                return self.store.advance(h, 'DONE', result='REJECTED')
            if not self.config.execution.live:
                q = data['quote']['quote']
                out = q['minAmountOut'] if self.config.paper_fill_worst_case else q['amountOut']
                return self._record_outcome(h, 'DRY_RUN', q['amountIn'], out, f'dry:{data["quote"]["correlationId"]}')
            return self._live_settle(h, row)
        if row['state'] == 'DEPOSITED':
            return self._track(h, row)
        if row['state'] == 'OUTCOME_PENDING':
            o = data['outcome_args']
            return self._record_outcome(h, o['status'], o['amount_in'], o['amount_out'], o['tx_ref'])
        return row

    def _record_outcome(self, h: str, status: str, amount_in: str, amount_out: str, tx_ref: str) -> dict[str, Any]:
        if self.writer is None:
            raise RuntimeError('recording an outcome needs the agent key')
        row = self.store.require(h)
        outcome_args = {'decision_id': row['data']['decision_id'], 'status': status, 'amount_in': str(amount_in),
                        'amount_out': str(amount_out), 'tx_ref': tx_ref[:128]}
        self.store.advance(h, 'OUTCOME_PENDING', outcome_args=outcome_args)
        written = self.writer.record_outcome(**outcome_args)
        outcome = written.result['outcome']
        self.log({'event': 'outcome_journaled', 'signal_hash': h, 'decision_id': outcome_args['decision_id'],
                  'status': status, 'realized_pnl_usd_cents': outcome.get('realized_pnl_usd_cents'),
                  'paper': status == 'DRY_RUN', 'auto_paused': any('"auto_paused"' in line for line in written.logs),
                  'explorer': tx_url(self.reader.network, written.tx_hash)})
        return self.store.advance(h, 'DONE', result=status, outcome=outcome, outcome_tx=written.tx_hash)

    def _live_settle(self, h: str, row: dict[str, Any]) -> dict[str, Any]:
        data = row['data']
        quote = data['quote']
        q, args = quote['quote'], data['decision_args']
        origin_asset = quote['quoteRequest']['originAsset']
        origin_t = self.oneclick.token(origin_asset)
        amount_human = origin_t.from_units(q['amountIn'])
        notional_usd = Decimal(args['notional_usd_cents']) / 100
        spent = Decimal(self.store.live_spent_cents(
            side='BUY' if self.config.live_total_entries_only else None)) / 100
        dest_t = self.oneclick.token(quote['quoteRequest'].get('destinationAsset') or args['asset'])
        details = {
            'guard decision': f'#{data["decision_id"]} ALLOWED on {self.reader.contract_id} ({data["decision_tx_url"]})',
            'token in': f'{amount_human} {origin_t.symbol} ({origin_t.contract_address})',
            'notional': f'${notional_usd:.2f} (cap ${self.config.execution.max_trade_usd}, spent so far ${spent})',
            'quote': f'{quote["correlationId"]}: {amount_human} {origin_t.symbol} -> '
                     f'{dest_t.from_units(q["amountOut"])} {dest_t.symbol}',
            'guaranteed minimum': f'{dest_t.from_units(q["minAmountOut"])} {dest_t.symbol}',
            'worst-case slippage': f'{args["slippage_bps"]} bps (policy max checked on-chain)',
            'deposit address': q['depositAddress'],
            'quote deadline': q.get('deadline') or quote['quoteRequest'].get('deadline'),
            'recipient / refund': self.config.trader_account,
        }
        try:
            cost = data.get('entry_cost_cents')
            live_gates.check_live_trade(self.config.execution, notional_usd, spent, guard_allowed=True,
                                        entry_cost_usd=None if row['side'] == 'BUY' or cost is None
                                        else Decimal(cost) / 100,
                                        count_in_total=row['side'] == 'BUY' or not self.config.live_total_entries_only)
            self.confirm(live_gates.confirmation_phrase(notional_usd, origin_t.symbol), details)
            deadline = details['quote deadline']
            if deadline and datetime.fromisoformat(deadline.replace('Z', '+00:00')) <= datetime.now(timezone.utc):
                raise live_gates.LiveGateError('QUOTE_EXPIRED: the quote deadline passed before confirmation')
        except live_gates.LiveGateError as exc:
            # Nothing was sent. Journal FAILED so the guard frees the PENDING_ENTRY slot instead of blocking entries.
            self.log({'event': 'live_aborted', 'signal_hash': h, 'reason': str(exc)})
            return self._record_outcome(h, 'FAILED', q['amountIn'], '0', f'aborted-before-deposit:{str(exc)[:80]}')
        self.store.advance(h, 'DEPOSIT_PENDING', live_notional_cents=args['notional_usd_cents'],
                           near_yocto_before=self._near_yocto())
        if not origin_t.contract_address:
            raise live_gates.LiveGateError(f'no token contract for {origin_t.asset_id}')
        tx_hash = live_gates.deposit_nep141(self.trader_near, origin_t.contract_address, q['depositAddress'],
                                            amount_human)
        row = self.store.advance(h, 'DEPOSITED', deposit_tx=tx_hash, deposit_address=q['depositAddress'],
                                 deposit_tx_url=tx_url('mainnet', tx_hash))
        self.log({'event': 'deposit_sent', 'signal_hash': h, 'deposit_address': q['depositAddress'],
                  'amount': str(amount_human), 'symbol': origin_t.symbol, 'explorer': tx_url('mainnet', tx_hash)})
        try:
            self.oneclick.submit_deposit(q['depositAddress'], tx_hash, near_sender=self.config.trader_account)
        except OneClickError as exc:  # optional speed-up only
            self.log({'event': 'deposit_submit_failed', 'signal_hash': h, 'error': str(exc)})
        return self._track(h, row)

    def _track(self, h: str, row: dict[str, Any]) -> dict[str, Any]:
        data = row['data']
        timeline = list(data.get('timeline') or [])

        def on_update(update):
            timeline.append({'status': update.status, 'observed_at': update.observed_at,
                             'funds_location': update.funds_location})
            self.store.advance(h, 'DEPOSITED', timeline=timeline)
            self.log({'event': 'intent_status', 'signal_hash': h, 'status': update.status,
                      'funds_location': update.funds_location})
        tracking = self.oneclick.track(data['deposit_address'], memo=data['quote']['quote'].get('depositMemo'),
                                       on_update=on_update)
        if tracking.outcome_status is None:
            self.log({'event': 'intent_status', 'signal_hash': h, 'status': 'TIMEOUT',
                      'funds_location': FUNDS_LOCATION['TIMEOUT']})
            return self.store.require(h)  # stays DEPOSITED; the next run resumes tracking
        self.store.advance(h, 'DEPOSITED', near_yocto_after=self._near_yocto())
        details = tracking.timeline[-1].swap_details
        q = data['quote']['quote']
        amount_in = details.get('amountIn') or q['amountIn']
        if tracking.outcome_status == 'SUCCESS':
            amount_out = details.get('amountOut')
            if not amount_out:  # never journal a guessed settlement amount
                return self.store.advance(h, 'NEEDS_REVIEW', review_reason='SUCCESS without swapDetails.amountOut')
        else:
            amount_out = '0'  # refunded / failed: nothing was received
        return self._record_outcome(h, tracking.outcome_status, amount_in, amount_out, data['deposit_tx'])

    def _skip(self, h: str, reason: str) -> dict[str, Any]:
        self.log({'event': 'skipped', 'signal_hash': h, 'reason': reason})
        return self.store.advance(h, 'SKIPPED', skip_reason=reason)


def explorer_summary(reader: GuardReader, count: int = 5) -> dict[str, Any]:
    return {'contract': reader.contract_id, 'explorer': account_url(reader.network, reader.contract_id),
            'latest_decisions': [{k: d.get(k) for k in ('id', 'asset', 'side', 'mode', 'allowed', 'reason')}
                                 for d in reader.latest_decisions(count)]}
