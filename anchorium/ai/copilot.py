"""AI risk co-pilot: NEAR AI Cloud reviews every trade and every PayPal top-up inside a TEE, and explains decisions.

The co-pilot can only make Anchorium **stricter**:
* trade review  -> ALLOW or VETO. A VETO turns the agent's own vote into "no" (`agent_allowed=false`). An ALLOW
  changes nothing: the guard contract still re-checks every rule and has the final word.
* top-up review -> ALLOW or VETO before the PayPal capture. On VETO the approved order is never captured, so the
  buyer is not charged.

Model: NEAR AI Cloud only (`NEAR_AI_API_KEY`, model `NEAR_AI_MODEL`), a private model running in an Intel TDX +
NVIDIA TEE. Every review comes back with a TEE signature over the exact request and response bytes, which
`anchorium.ai.near_ai` verifies against the attested signing key; the whole signed receipt is hashed and that hash
goes into the contract's `rationale_hash` field (`python -m anchorium verify-ai` re-checks it). Without a key, when
the spend guardrails refuse a call, or when the call or its signature fails, a deterministic rules reviewer answers
instead, labelled `rules (<reason>)`. It is never shown as AI output.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from anchorium.ai.near_ai import NearAIClient, NearAIUnavailable, SignedReview

VERDICTS = ('ALLOW', 'VETO')

#: Top-up rules (deterministic, always applied; the model may veto more, never less).
MAX_BALANCE_TRADES = 25         # never hold more than 25 max-size trades of budget in the agent
MAX_TOPUPS_PER_HOUR = 3

TRADE_SYSTEM = """You are the risk co-pilot of Anchorium, an autonomous crypto trading agent.
An on-chain guard contract enforces hard limits and has the final word. You can only make the agent stricter:
answer VETO to block a trade that the guard would allow, or ALLOW to leave the decision to the guard.
The input has precomputed facts (`facts`): price_vs_vwap, atr_pct_of_price, slippage_pct_of_cap, budget_covers_trade,
notional_within_cap. They were computed exactly by code: trust them, never recompute or contradict them.
The strategy is trend-following: a buy with price ABOVE VWAP is normal and expected, never a reason to veto.
A notional at or below the per-trade cap is fine; never veto because it is close to the cap.
VETO only when the trade looks unwise even though it fits the limits: worst-case slippage close to the cap
(80% of it or more), price BELOW VWAP on a buy, very high volatility (ATR above 2% of price), a budget that
cannot cover the trade, or a quote whose guaranteed output is far below the expected output. Otherwise ALLOW.
Reply with JSON only, no prose around it:
{"verdict": "ALLOW" | "VETO", "risk_score": <integer 0-100>, "reason_code": "<UPPER_SNAKE, max 24 chars>",
 "explanation": "<plain English for a non-expert, max 60 words, cite the numbers you used>"}"""

TOPUP_SYSTEM = """You are the funding co-pilot of Anchorium, an autonomous crypto trading agent funded with PayPal.
A user wants to add money to the agent's trading budget. Your job is to protect the user from over-funding a bot.
Hard rules (already checked in code; never contradict them): the balance after the top-up must stay at or below
25 max-size trades, and at most 3 top-ups per hour. You may VETO for other good reasons; you may not ALLOW what the
rules veto. Explain what the money covers in plain English (number of max-size trades, daily loss cap).
Reply with JSON only:
{"verdict": "ALLOW" | "VETO", "risk_score": <integer 0-100>, "reason_code": "<UPPER_SNAKE, max 24 chars>",
 "explanation": "<plain English, max 60 words>"}"""

EXPLAIN_SYSTEM = """You explain decisions from Anchorium's on-chain risk journal to a non-expert in 2-3 short
sentences. Say whether the trade was allowed, which rule decided it, and what that means for their money.
If verdict_override is true, say clearly that the agent claimed the trade was fine and the contract overruled it.
Use only the facts given. No markdown."""


@dataclass(frozen=True)
class Review:
    kind: str            # TRADE / TOPUP
    verdict: str         # ALLOW / VETO
    risk_score: int
    reason_code: str
    explanation: str
    provider: str        # "near-ai-cloud:<model> (TEE, signature verified)" or "rules (<reason>)"
    #: The TEE-signed NEAR AI receipt behind this review, when a model answered (see anchorium.ai.near_ai).
    receipt: dict[str, Any] | None = field(default=None, compare=False, repr=False)

    @property
    def vetoed(self) -> bool:
        return self.verdict == 'VETO'

    def canonical(self) -> str:
        """The rules review itself, or the signed receipt when a model answered: what `rationale_hash` commits to."""
        if self.receipt is not None:
            return json.dumps(self.receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        return json.dumps({k: v for k, v in asdict(self).items() if k != 'receipt'}, sort_keys=True,
                          separators=(',', ':'))

    @property
    def rationale_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if k != 'receipt'}
        return {**out, 'rationale_hash': self.rationale_hash, 'tee_signed': self.receipt is not None}


# ----------------------------------------------------------------------------------------------- rules fallback

def rules_trade_review(ctx: dict[str, Any]) -> Review:
    """Deterministic reviewer used without an API key or when the model fails. Conservative by design."""
    def d(key, default='0'):
        v = ctx.get(key)
        return Decimal(str(v)) if v not in (None, '') else Decimal(default)
    slip, cap = d('worst_case_slippage_bps'), d('policy_max_slippage_bps', '150')
    close, vwap, atr = d('close'), d('vwap'), d('atr_14')
    notional, budget = d('notional_usd'), ctx.get('paypal_budget_usd')
    if budget is not None and Decimal(str(budget)) < notional and ctx.get('side') == 'BUY':
        return Review('TRADE', 'VETO', 90, 'BUDGET_SHORT',
                      f'The PayPal-funded budget (${budget}) cannot cover this ${notional} buy.', 'rules')
    if cap > 0 and slip >= cap * Decimal('0.8'):
        return Review('TRADE', 'VETO', 75, 'SLIPPAGE_NEAR_CAP',
                      f'Worst-case slippage is {slip} bps, at least 80% of the {cap} bps cap: too little margin.',
                      'rules')
    if ctx.get('side') == 'BUY' and close and vwap and close < vwap:
        return Review('TRADE', 'VETO', 65, 'BELOW_VWAP',
                      f'Price {close} is below VWAP {vwap:.4f}, so this buy goes against the session trend.', 'rules')
    if close and atr and atr / close > Decimal('0.02'):
        return Review('TRADE', 'VETO', 70, 'HIGH_VOLATILITY',
                      f'ATR is {atr / close * 100:.1f}% of price: too volatile for a $2 round trip.', 'rules')
    score = int(min(60, slip / cap * 50)) if cap else 30
    return Review('TRADE', 'ALLOW', score, 'WITHIN_LIMITS',
                  f'Slippage {slip} bps of a {cap} bps cap, price {close} vs VWAP {vwap:.4f}: nothing beyond the '
                  'guard limits stands out. The contract still has the final word.', 'rules')


def rules_topup_review(ctx: dict[str, Any]) -> Review:
    amount, balance = Decimal(str(ctx['amount_usd'])), Decimal(str(ctx['balance_usd']))
    per_trade = Decimal(str(ctx['max_trade_usd']))
    ceiling = per_trade * MAX_BALANCE_TRADES
    after = balance + amount
    trades = int(after // per_trade) if per_trade else 0
    if int(ctx.get('topups_last_hour', 0)) >= MAX_TOPUPS_PER_HOUR:
        return Review('TOPUP', 'VETO', 80, 'TOO_MANY_TOPUPS',
                      f'{ctx["topups_last_hour"]} top-ups in the last hour already; wait before adding more.', 'rules')
    if after > ceiling:
        return Review('TOPUP', 'VETO', 85, 'OVERFUNDING',
                      f'${after} would exceed {MAX_BALANCE_TRADES} max-size trades (${ceiling}). A bot should '
                      'never hold more than it can use; top up less.', 'rules')
    return Review('TOPUP', 'ALLOW', 20, 'WITHIN_LIMITS',
                  f'${after} covers {trades} trades of ${per_trade}; the guard still caps losses at '
                  f'${ctx.get("daily_loss_cap_usd", "?")} per day and auto-pauses beyond it.', 'rules')


def rules_explain(decision: dict[str, Any]) -> str:
    verdict = 'allowed' if decision.get('allowed') else f'rejected ({decision.get("reason")})'
    text = (f'Decision #{decision.get("id")}: a {decision.get("mode")} {decision.get("side")} of '
            f'{decision.get("asset")} for ${int(decision.get("notional_usd_cents") or 0) / 100:.2f} was {verdict} by '
            'the guard contract.')
    if decision.get('agent_allowed') and not decision.get('allowed'):
        text += ' The agent claimed it was fine; the contract overruled it, so nothing was sent.'
    return text


# ----------------------------------------------------------------------------------------------- the co-pilot

class Copilot:
    def __init__(self, client: NearAIClient | None = None, log_path: str | Path | None = None,
                 no_client_reason: str = 'no NEAR_AI_API_KEY'):
        self.client = client
        self.no_client_reason = no_client_reason
        self.log_path = Path(log_path) if log_path else None
        self.last_error: str | None = None

    @classmethod
    def from_env(cls, env: Any, log_path: str | Path | None = None) -> Copilot:
        if env.get('NEAR_AI_API_KEY'):
            return cls(NearAIClient.from_env(env), log_path=log_path)
        return cls(None, log_path=log_path)

    @property
    def api_key(self) -> bool:  # kept for callers that only ask "is a model configured?"
        return self.client is not None

    @property
    def provider(self) -> str:
        if self.client is None:
            return f'rules ({self.no_client_reason})'
        return f'near-ai-cloud:{self.client.model} (TEE, signature verified)'

    def _review(self, kind: str, system: str, ctx: dict[str, Any], fallback) -> Review:
        rules = fallback(ctx)
        review = Review(rules.kind, rules.verdict, rules.risk_score, rules.reason_code, rules.explanation,
                        f'rules ({self.no_client_reason})')
        if self.client is not None:
            try:
                signed = self.client.review(kind, system, ctx)
                if not signed.verified:
                    raise NearAIUnavailable('TEE signature not verified')
                review = _from_signed(signed, self.provider)
                self.last_error = None
            except (NearAIUnavailable, ValueError, KeyError) as exc:
                reason = exc.reason if isinstance(exc, NearAIUnavailable) else f'{type(exc).__name__}'
                self.last_error = f'{type(exc).__name__}: {exc}'[:200]
                review = Review(rules.kind, rules.verdict, rules.risk_score, rules.reason_code, rules.explanation,
                                f'rules ({reason})')
        if rules.vetoed and not review.vetoed:  # the model may be stricter than the rules, never looser
            review = Review(kind, 'VETO', max(rules.risk_score, review.risk_score), rules.reason_code,
                            f'{rules.explanation} (Hard rule; the model had said: {review.explanation})',
                            review.provider, review.receipt)
        self._persist(review, ctx)
        return review

    def review_trade(self, ctx: dict[str, Any]) -> Review:
        return self._review('TRADE', TRADE_SYSTEM, ctx, rules_trade_review)

    def review_topup(self, ctx: dict[str, Any]) -> Review:
        return self._review('TOPUP', TOPUP_SYSTEM, ctx, rules_topup_review)

    def explain_decision(self, decision: dict[str, Any], verdict_override: bool | None = None) -> dict[str, Any]:
        facts = {k: decision.get(k) for k in ('id', 'asset', 'side', 'mode', 'notional_usd_cents', 'slippage_bps',
                                              'agent_allowed', 'agent_reason', 'allowed', 'reason', 'rationale_hash')}
        facts['verdict_override'] = bool(decision.get('agent_allowed') and not decision.get('allowed')) \
            if verdict_override is None else verdict_override
        if self.client is not None:
            try:
                signed = self.client.review('EXPLAIN', EXPLAIN_SYSTEM, facts)
                if signed.verified:
                    return {'provider': self.provider, 'text': signed.explanation, 'tee_signed': True,
                            'rationale_hash': signed.rationale_hash, 'chat_id': signed.chat_id}
                self.last_error = 'TEE signature not verified'
            except (NearAIUnavailable, ValueError, KeyError) as exc:
                self.last_error = f'{type(exc).__name__}: {exc}'[:200]
        reason = (self.last_error or '').split(': ', 1)[-1] if self.client is not None else self.no_client_reason
        return {'provider': f'rules ({reason})', 'text': rules_explain(facts), 'tee_signed': False}

    def _persist(self, review: Review, ctx: dict[str, Any]) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a') as f:
            f.write(json.dumps({'review': review.to_dict(), 'canonical': review.canonical(), 'context': ctx},
                               default=str) + '\n')


def _from_signed(signed: SignedReview, provider: str) -> Review:
    return Review(signed.kind, signed.verdict, signed.risk, signed.reason_code, signed.explanation, provider,
                  signed.to_receipt())


def trade_context(*, signal: Any, asset: str, side: str, notional_cents: int, policy: dict[str, Any],
                  slippage_bps: int, quote: Any, in_symbol: str, out_symbol: str, amount_in: Any, amount_out: Any,
                  min_amount_out: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    s = signal.to_dict() if hasattr(signal, 'to_dict') else dict(signal or {})
    ctx = {'asset': asset, 'side': side, 'notional_usd': f'{notional_cents / 100:.2f}',
            'signal_type': s.get('signal_type'), 'close': s.get('close'), 'vwap': s.get('vwap'),
            'atr_14': s.get('atr_14'), 'ema_9': s.get('ema_9'), 'ema_21': s.get('ema_21'),
            'worst_case_slippage_bps': slippage_bps, 'policy_max_slippage_bps': policy.get('max_slippage_bps'),
            'policy_max_trade_usd': f'{int(policy.get("max_notional_per_trade_usd_cents") or 0) / 100:.2f}',
            'quote': {'in': f'{amount_in} {in_symbol}', 'expected_out': f'{amount_out} {out_symbol}',
                      'guaranteed_min_out': f'{min_amount_out} {out_symbol}',
                      'app_fee_bps': getattr(quote, 'app_fee_bps', None)},
            **(extra or {})}
    ctx['facts'] = derived_facts(ctx)
    return ctx


def derived_facts(ctx: dict[str, Any]) -> dict[str, Any]:
    """Exact comparisons computed in code, so the model never has to do arithmetic (small models get it wrong)."""
    def dec(key):
        try:
            v = Decimal(str(ctx.get(key)))
            return v if v.is_finite() else None
        except Exception:  # noqa: BLE001
            return None
    close, vwap, atr = dec('close'), dec('vwap'), dec('atr_14')
    slip, cap = dec('worst_case_slippage_bps'), dec('policy_max_slippage_bps')
    notional, max_trade, budget = dec('notional_usd'), dec('policy_max_trade_usd'), dec('paypal_budget_usd')
    facts: dict[str, Any] = {}
    if close and vwap:
        facts['price_vs_vwap'] = 'ABOVE' if close > vwap else ('BELOW' if close < vwap else 'EQUAL')
        facts['price_vs_vwap_pct'] = f'{(close - vwap) / vwap * 100:+.3f}%'
    if close and atr is not None:
        facts['atr_pct_of_price'] = f'{atr / close * 100:.3f}%'
    if cap and slip is not None:
        facts['slippage_pct_of_cap'] = f'{slip / cap * 100:.0f}%'
    if notional is not None and max_trade:
        facts['notional_within_cap'] = notional <= max_trade
    if notional is not None and budget is not None:
        facts['budget_covers_trade'] = budget >= notional
    return facts
