"""AI risk co-pilot: a language model reviews every trade and every PayPal top-up, and explains guard decisions.

The co-pilot can only make Anchorium **stricter**:
* trade review  -> ALLOW or VETO. A VETO turns the agent's own vote into "no" (`agent_allowed=false`). An ALLOW
  changes nothing: the guard contract still re-checks every rule and has the final word.
* top-up review -> ALLOW or VETO before the PayPal capture. On VETO the approved order is never captured, so the
  buyer is not charged.

Every review is hashed (sha256 of its canonical JSON) and the hash goes into the contract's `rationale_hash`
field, so the AI's reasoning is committed on-chain next to the decision. The full text is kept locally in
`var/ai_rationales.jsonl`, and anyone can re-hash it and compare.

Model: Anthropic Messages API (`ANTHROPIC_API_KEY`, model from `AI_MODEL`), or any OpenAI-compatible chat API
(`AI_BASE_URL` + `AI_API_KEY`, e.g. Google Gemini or Groq free tiers). Without a key, or when the call or
the JSON fails, a deterministic rules reviewer answers instead, labelled `provider="rules"`. It is never shown as
AI output.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages'
DEFAULT_MODEL = 'claude-haiku-5-5'
VERDICTS = ('ALLOW', 'VETO')

#: Top-up rules (deterministic, always applied; the model may veto more, never less).
MAX_BALANCE_TRADES = 25         # never hold more than 25 max-size trades of budget in the agent
MAX_TOPUPS_PER_HOUR = 3

TRADE_SYSTEM = """You are the risk co-pilot of Anchorium, an autonomous crypto trading agent.
An on-chain guard contract enforces hard limits and has the final word. You can only make the agent stricter:
answer VETO to block a trade that the guard would allow, or ALLOW to leave the decision to the guard.
The strategy is trend-following: a buy with price ABOVE VWAP is normal and expected, never a reason to veto.
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
    provider: str        # "anthropic:<model>" or "rules"

    @property
    def vetoed(self) -> bool:
        return self.verdict == 'VETO'

    def canonical(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(',', ':'))

    @property
    def rationale_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), 'rationale_hash': self.rationale_hash}


def _clean(review: dict[str, Any], kind: str, provider: str) -> Review:
    verdict = str(review.get('verdict', '')).upper()
    if verdict not in VERDICTS:
        raise ValueError(f'bad verdict {verdict!r}')
    score = max(0, min(100, int(review.get('risk_score', 50))))
    code = re.sub(r'[^A-Z0-9_]', '', str(review.get('reason_code') or verdict).upper())[:24] or verdict
    text = ' '.join(str(review.get('explanation', '')).split())[:600]
    if not text:
        raise ValueError('empty explanation')
    return Review(kind, verdict, score, code, text, provider)


def _json_object(text: str) -> dict[str, Any]:
    match = re.search(r'\{.*\}', text, re.S)
    if not match:
        raise ValueError('no JSON object in the model reply')
    return json.loads(match.group(0))


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
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL,
                 transport: httpx.BaseTransport | None = None, log_path: str | Path | None = None,
                 timeout: float = 30.0, base_url: str | None = None, fallback_models: list[str] | None = None):
        self.api_key, self.model = api_key or None, model
        #: OpenAI-compatible chat API (e.g. Gemini, Groq) instead of Anthropic when set.
        self.base_url = base_url.rstrip('/') if base_url else None
        #: Models tried in order when one is overloaded (HTTP 429/5xx or timeout): `model`, then these.
        self.fallback_models = [m.strip() for m in (fallback_models or []) if m.strip() and m.strip() != model]
        self.used_model = model
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self.log_path = Path(log_path) if log_path else None
        self.last_error: str | None = None

    @classmethod
    def from_env(cls, env: Any, log_path: str | Path | None = None) -> Copilot:
        if env.get('AI_BASE_URL'):
            return cls(env.get('AI_API_KEY') or None, env.get('AI_MODEL') or DEFAULT_MODEL, log_path=log_path,
                       base_url=env.get('AI_BASE_URL'),
                       fallback_models=(env.get('AI_FALLBACK_MODELS') or '').split(','))
        return cls(env.get('ANTHROPIC_API_KEY') or None, env.get('AI_MODEL') or DEFAULT_MODEL, log_path=log_path)

    @property
    def provider(self) -> str:
        if not self.api_key:
            return 'rules'
        if self.base_url:
            host = self.base_url.split('//', 1)[-1].split('/', 1)[0]
            return f'{host}:{self.used_model}'
        return f'anthropic:{self.model}'

    def _ask(self, system: str, payload: dict[str, Any], max_tokens: int = 300) -> str:
        if self.base_url:
            errors = []
            for model in [self.model, *self.fallback_models]:
                body = {'model': model, 'max_tokens': max(max_tokens, 2048), 'temperature': 0, 'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': json.dumps(payload, default=str)}]}
                try:
                    r = self._http.post(f'{self.base_url}/chat/completions', json=body, headers={
                        'Authorization': f'Bearer {self.api_key}', 'content-type': 'application/json'})
                except httpx.TimeoutException:
                    errors.append(f'{model}: timeout')
                    continue
                if r.status_code in (429, 500, 502, 503, 504):  # overloaded / rate-limited: try the next model
                    errors.append(f'{model}: HTTP {r.status_code}')
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f'AI HTTP {r.status_code} ({model}): {r.text[:160]}')
                self.used_model = model
                return r.json()['choices'][0]['message']['content'] or ''
            raise RuntimeError('all models unavailable: ' + '; '.join(errors))
        r = self._http.post(ANTHROPIC_URL, headers={
            'x-api-key': self.api_key or '', 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': self.model, 'max_tokens': max_tokens, 'temperature': 0, 'system': system,
                  'messages': [{'role': 'user', 'content': json.dumps(payload, default=str)}]})
        if r.status_code != 200:
            raise RuntimeError(f'Anthropic HTTP {r.status_code}: {r.text[:160]}')
        return ''.join(block.get('text', '') for block in r.json().get('content', []) if block.get('type') == 'text')

    def _review(self, kind: str, system: str, ctx: dict[str, Any], fallback) -> Review:
        rules = fallback(ctx)
        review = rules
        if self.api_key:
            try:
                review = _clean(_json_object(self._ask(system, ctx)), kind, self.provider)
                self.last_error = None
            except Exception as exc:  # noqa: BLE001 - any model failure falls back to the rules reviewer
                self.last_error = f'{type(exc).__name__}: {exc}'[:200]
                review = Review(rules.kind, rules.verdict, rules.risk_score, rules.reason_code, rules.explanation,
                                'rules (AI unavailable)')
        if rules.vetoed and not review.vetoed:  # the model may be stricter than the rules, never looser
            review = Review(kind, 'VETO', max(rules.risk_score, review.risk_score), rules.reason_code,
                            f'{rules.explanation} (Hard rule; the model had said: {review.explanation})',
                            review.provider)
        self._persist(review, ctx)
        return review

    def review_trade(self, ctx: dict[str, Any]) -> Review:
        return self._review('TRADE', TRADE_SYSTEM, ctx, rules_trade_review)

    def review_topup(self, ctx: dict[str, Any]) -> Review:
        return self._review('TOPUP', TOPUP_SYSTEM, ctx, rules_topup_review)

    def explain_decision(self, decision: dict[str, Any], verdict_override: bool | None = None) -> dict[str, str]:
        facts = {k: decision.get(k) for k in ('id', 'asset', 'side', 'mode', 'notional_usd_cents', 'slippage_bps',
                                              'agent_allowed', 'agent_reason', 'allowed', 'reason', 'rationale_hash')}
        facts['verdict_override'] = bool(decision.get('agent_allowed') and not decision.get('allowed')) \
            if verdict_override is None else verdict_override
        if self.api_key:
            try:
                return {'provider': self.provider, 'text': ' '.join(self._ask(EXPLAIN_SYSTEM, facts).split())}
            except Exception as exc:  # noqa: BLE001
                self.last_error = f'{type(exc).__name__}: {exc}'[:200]
        return {'provider': 'rules', 'text': rules_explain(facts)}

    def _persist(self, review: Review, ctx: dict[str, Any]) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a') as f:
            f.write(json.dumps({'review': review.to_dict(), 'canonical': review.canonical(), 'context': ctx},
                               default=str) + '\n')


def trade_context(*, signal: Any, asset: str, side: str, notional_cents: int, policy: dict[str, Any],
                  slippage_bps: int, quote: Any, in_symbol: str, out_symbol: str, amount_in: Any, amount_out: Any,
                  min_amount_out: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    s = signal.to_dict() if hasattr(signal, 'to_dict') else dict(signal or {})
    return {'asset': asset, 'side': side, 'notional_usd': f'{notional_cents / 100:.2f}',
            'signal_type': s.get('signal_type'), 'close': s.get('close'), 'vwap': s.get('vwap'),
            'atr_14': s.get('atr_14'), 'ema_9': s.get('ema_9'), 'ema_21': s.get('ema_21'),
            'worst_case_slippage_bps': slippage_bps, 'policy_max_slippage_bps': policy.get('max_slippage_bps'),
            'policy_max_trade_usd': f'{int(policy.get("max_notional_per_trade_usd_cents") or 0) / 100:.2f}',
            'quote': {'in': f'{amount_in} {in_symbol}', 'expected_out': f'{amount_out} {out_symbol}',
                      'guaranteed_min_out': f'{min_amount_out} {out_symbol}',
                      'app_fee_bps': getattr(quote, 'app_fee_bps', None)},
            **(extra or {})}
