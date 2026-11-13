"""NEAR AI Cloud: TEE-attested, signed inference for Anchorium's risk reviews, with zero-spend guardrails.

Flow for one review (docs.near.ai/cloud/verification):
  1. fresh 32-byte nonce -> GET /v1/attestation/report?model=..&signing_algo=ecdsa&nonce=..  (free, non-billable)
     -> the model TEE's `signing_address`, its Intel TDX quote (whose report_data binds that address + our nonce),
        the NVIDIA GPU evidence, and the same for the gateway TEE.
  2. POST /v1/chat/completions with exact bytes we keep; the exact response bytes are kept too   (billable)
  3. GET /v1/signature/{chat_id}?model=..&signing_algo=ecdsa                                    (free)
     -> `text` = "<model>:<sha256(request)>:<sha256(response)>" (provider_tee) or "<req>:<resp>" (gateway),
        an EIP-191 `signature` over it, and the signer address.
  4. verify offline: both hashes, the recovered signer == the attested address, the TDX report_data binding.
The whole receipt is hashed (canonical JSON) and that `rationale_hash` goes on-chain with the guard decision.

Spend is bounded in code, not by promise: a per-run call cap, a lifetime USD budget kept in a ledger outside the
repo, a worst-case cost check before every billable call, no retry loops (one retry on 5xx/timeout only), and a
cache so the same request is never paid for twice. Any refusal or error falls back to the deterministic rules.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

BASE_URL = 'https://cloud-api.near.ai/v1'
HOME = Path(os.environ.get('ANCHORIUM_HOME') or Path.home() / '.anchorium')
TDX_QUOTE_VERSION = 4
TDX_TEE_TYPE = 0x81
REPORT_DATA_OFFSET = 48 + 520            # TDX quote v4: 48-byte header, report_data at byte 520 of the TD report
UNAVAILABLE_STATUS = {401: 'unauthorized', 402: 'out of credits', 403: 'forbidden', 429: 'rate limited'}


class NearAIUnavailable(RuntimeError):
    """The model was not called or did not answer; `reason` labels the rules fallback."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ----------------------------------------------------------------------------------------------- guardrails

@dataclass(frozen=True)
class Guardrails:
    max_calls_per_run: int = 10
    budget_usd: Decimal = Decimal('0.05')
    max_tokens: int = 300

    @classmethod
    def from_env(cls, env: Any) -> Guardrails:
        return cls(int(env.get('NEAR_AI_MAX_CALLS_PER_RUN') or 10), Decimal(env.get('NEAR_AI_BUDGET_USD') or '0.05'),
                   int(env.get('NEAR_AI_MAX_TOKENS') or 300))


@dataclass(frozen=True)
class Price:
    """USD per token, as published by GET /v1/models (`pricing.prompt` / `pricing.completion`)."""
    prompt: Decimal
    completion: Decimal

    def cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        return self.prompt * prompt_tokens + self.completion * completion_tokens


class UsageLedger:
    """Every billable call, persisted outside the repo (default ~/.anchorium/near_ai_usage.json)."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else HOME / 'near_ai_usage.json'

    def calls(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return list(json.loads(self.path.read_text()).get('calls', []))

    def total_usd(self) -> Decimal:
        return sum((Decimal(c['est_usd']) for c in self.calls()), Decimal(0))

    def record(self, model: str, prompt_tokens: int, completion_tokens: int, est_usd: Decimal) -> None:
        rows = self.calls()
        rows.append({'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'model': model,
                     'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens, 'est_usd': str(est_usd)})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'calls': rows}, indent=1))
        tmp.replace(self.path)


# ----------------------------------------------------------------------------------------------- crypto helpers

def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _keccak(data: bytes) -> bytes:
    from Crypto.Hash import keccak  # noqa: PLC0415 - pycryptodome
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


def recover_eip191(text: str, signature: str) -> str:
    """The Ethereum address that produced `signature` (65 bytes, hex) over `text` with personal_sign (EIP-191)."""
    import coincurve  # noqa: PLC0415
    sig = bytes.fromhex(signature.removeprefix('0x'))
    if len(sig) != 65:
        raise ValueError(f'signature must be 65 bytes, got {len(sig)}')
    v = sig[64] - 27 if sig[64] >= 27 else sig[64]
    if v not in (0, 1):
        raise ValueError(f'bad recovery id {sig[64]}')
    msg = text.encode()
    digest = _keccak(b'\x19Ethereum Signed Message:\n' + str(len(msg)).encode() + msg)
    pub = coincurve.PublicKey.from_signature_and_message(sig[:64] + bytes([v]), digest, hasher=None)
    return '0x' + _keccak(pub.format(compressed=False)[1:])[-20:].hex()


def tdx_report_data(intel_quote_hex: str) -> tuple[int, int, bytes]:
    """(quote version, TEE type, 64-byte report_data) of an Intel TDX v4 quote. Structure only: the quote's
    certificate chain is not checked here (use dcap-qvl or https://proof.t16z.com/ for that)."""
    q = bytes.fromhex(intel_quote_hex)
    if len(q) < REPORT_DATA_OFFSET + 64:
        raise ValueError('intel_quote too short')
    return int.from_bytes(q[0:2], 'little'), int.from_bytes(q[4:8], 'little'), q[REPORT_DATA_OFFSET:REPORT_DATA_OFFSET + 64]


def expected_report_data(signing_address: str, nonce_hex: str) -> bytes:
    """NEAR AI binds `signing_address` (20 bytes, zero-padded to 32) and the 32-byte request nonce."""
    return bytes.fromhex(signing_address.removeprefix('0x')).ljust(32, b'\0') + bytes.fromhex(nonce_hex)


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


# ----------------------------------------------------------------------------------------------- receipts

@dataclass
class SignedReview:
    """A TEE-signed risk review and everything needed to verify it offline. `rationale_hash` (sha256 of the
    canonical JSON of `to_receipt()`) is what goes on-chain."""
    kind: str
    verdict: str
    reason_code: str
    risk: int
    explanation: str
    model: str
    chat_id: str
    request_sha256: str
    response_sha256: str
    signature: str
    signing_address: str
    signature_kind: str
    attestation_sha256: str
    nonce: str
    verified: bool
    created_at: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_receipt(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @property
    def rationale_hash(self) -> str:
        return sha256_hex(canonical(self.to_receipt()))

    @classmethod
    def from_receipt(cls, receipt: dict[str, Any]) -> SignedReview:
        return cls(**{k: receipt[k] for k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Check:
    step: str
    ok: bool
    detail: str


def verify_receipt(receipt: dict[str, Any]) -> list[Check]:
    """Offline verification of a stored receipt. No network, no key."""
    ev = receipt.get('evidence') or {}
    checks: list[Check] = []

    def add(step: str, ok: bool, detail: str) -> None:
        checks.append(Check(step, bool(ok), detail))

    req, resp = ev.get('request_body', ''), ev.get('response_body', '')
    add('request hash', sha256_hex(req) == receipt['request_sha256'], f'sha256(request bytes) = {sha256_hex(req)[:16]}...')
    add('response hash', sha256_hex(resp) == receipt['response_sha256'],
        f'sha256(response bytes) = {sha256_hex(resp)[:16]}...')
    try:
        body = json.loads(resp)
        parsed = _json_object(body['choices'][0]['message']['content'])
        verdict = 'INFO' if receipt['kind'] == 'EXPLAIN' else str(parsed.get('verdict', '')).upper()
        text_ok = ' '.join(str(parsed.get('explanation', '')).split())[:600] == receipt['explanation']
        same = verdict == receipt['verdict'] and text_ok and body.get('id') == receipt['chat_id']
        add('review from signed bytes', same, 'verdict, explanation and chat id are read from the signed response')
    except Exception as exc:  # noqa: BLE001
        add('review from signed bytes', False, f'cannot parse the response body: {exc}')
    text = ev.get('signed_text', '')
    parts = text.split(':')
    in_text = receipt['request_sha256'] in parts and receipt['response_sha256'] in parts
    add('signed text', in_text, f'{receipt["signature_kind"]} payload "{text[:40]}..." contains both hashes')
    expected = (ev.get('model_signing_address') if receipt['signature_kind'] == 'provider_tee'
                else ev.get('gateway_signing_address')) or ''
    try:
        signer = recover_eip191(text, receipt['signature'])
        add('signature', signer.lower() == expected.lower() == receipt['signing_address'].lower(),
            f'recovered {signer} vs attested {expected}')
    except Exception as exc:  # noqa: BLE001
        add('signature', False, f'recovery failed: {exc}')
    for who, addr_key, quote_key in (('model', 'model_signing_address', 'model_intel_quote'),
                                     ('gateway', 'gateway_signing_address', 'gateway_intel_quote')):
        try:
            version, tee, rd = tdx_report_data(ev[quote_key])
            ok = (version == TDX_QUOTE_VERSION and tee == TDX_TEE_TYPE
                  and rd == expected_report_data(ev[addr_key], receipt['nonce']))
            add(f'{who} TEE binding', ok, f'TDX v{version} quote report_data = {who} signing address + our nonce')
        except Exception as exc:  # noqa: BLE001
            add(f'{who} TEE binding', False, f'cannot read the TDX quote: {exc}')
    add('fresh nonce', ev.get('model_request_nonce') == receipt['nonce'] == ev.get('gateway_request_nonce'),
        f'attestation fetched with nonce {receipt["nonce"][:16]}... right before the call')
    return checks


def _reason_code(value: Any) -> str:
    return ''.join(ch for ch in str(value).upper() if ch.isalnum() or ch == '_')[:24] or 'UNSPECIFIED'


def _json_object(text: str) -> dict[str, Any]:
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('no JSON object in the model reply')
    return json.loads(text[start:end + 1])


# ----------------------------------------------------------------------------------------------- the client

class NearAIClient:
    """Raw-httpx NEAR AI Cloud client (not the OpenAI SDK): we must hash the exact bytes on the wire."""

    def __init__(self, api_key: str, model: str, *, base_url: str = BASE_URL, guard: Guardrails | None = None,
                 ledger: UsageLedger | None = None, cache_dir: str | Path | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 60.0,
                 sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise ValueError('NEAR_AI_API_KEY is empty')
        self.model, self.base_url = model, base_url.rstrip('/')
        self.guard, self.ledger = guard or Guardrails(), ledger or UsageLedger()
        self.cache_dir = Path(cache_dir) if cache_dir else HOME / 'receipts'
        self._http = httpx.Client(timeout=timeout, transport=transport,
                                  headers={'Authorization': f'Bearer {api_key}', 'accept': 'application/json'})
        self._sleep = sleep
        self._price: Price | None = None
        self.calls_this_run = 0

    @classmethod
    def from_env(cls, env: Any, **kw: Any) -> NearAIClient:
        return cls(env['NEAR_AI_API_KEY'], env.get('NEAR_AI_MODEL') or DEFAULT_MODEL,
                   base_url=env.get('NEAR_AI_BASE_URL') or BASE_URL, guard=Guardrails.from_env(env), **kw)

    # ----- free endpoints -----

    def price(self) -> Price:
        if self._price is None:
            r = self._http.get(f'{self.base_url}/models')
            if r.status_code != 200:
                raise NearAIUnavailable(f'models HTTP {r.status_code}')
            row = next((m for m in r.json().get('data', []) if m.get('id') == self.model), None)
            if row is None:
                raise NearAIUnavailable(f'model {self.model} not listed')
            p = row['pricing']
            self._price = Price(Decimal(str(p['prompt'])), Decimal(str(p['completion'])))
        return self._price

    def attestation(self, nonce: str) -> tuple[str, dict[str, Any]]:
        url = (f'{self.base_url}/attestation/report?model={quote(self.model, safe="")}'
               f'&signing_algo=ecdsa&nonce={nonce}')
        r = self._http.get(url)
        if r.status_code != 200:
            raise NearAIUnavailable(f'attestation HTTP {r.status_code}')
        return r.text, r.json()

    def signature(self, chat_id: str, tries: int = 6) -> dict[str, Any]:
        """Free. A model can run on several TEE nodes and the signature is cached on the one that answered, so a
        lookup may briefly say 'not found': retry a few times (this endpoint is not billed)."""
        url = f'{self.base_url}/signature/{chat_id}?model={quote(self.model, safe="")}&signing_algo=ecdsa'
        last = ''
        for i in range(tries):
            r = self._http.get(url)
            if r.status_code == 200 and r.json().get('signature'):
                return r.json()
            last = f'HTTP {r.status_code} {r.text[:80]}'
            self._sleep(1.0 + i)
        raise NearAIUnavailable(f'signature unavailable ({last})')

    # ----- the one billable endpoint -----

    def _budget_check(self, body: bytes) -> None:
        if self.calls_this_run >= self.guard.max_calls_per_run:
            raise NearAIUnavailable('budget cap: calls per run')
        worst = self.price().cost(len(body) // 2 + 64, self.guard.max_tokens)  # tokens <= bytes/2 + chat template
        if self.ledger.total_usd() + worst > self.guard.budget_usd:
            raise NearAIUnavailable('budget cap: USD')

    def chat(self, system: str, user: str) -> tuple[bytes, bytes, dict[str, Any], bool]:
        """(request bytes, response bytes, parsed response, from_cache). One retry on 5xx/timeout, never more."""
        body = {'model': self.model, 'messages': [{'role': 'system', 'content': system},
                                                  {'role': 'user', 'content': user}],
                'max_tokens': self.guard.max_tokens, 'temperature': 0, 'stream': False}
        raw = json.dumps(body, separators=(',', ':'), ensure_ascii=False).encode()
        cached = self._cache_get(raw)
        if cached:
            return raw, cached['response'].encode(), json.loads(cached['response']), True
        self._budget_check(raw)
        resp = None
        for attempt in range(2):
            try:
                resp = self._http.post(f'{self.base_url}/chat/completions', content=raw,
                                       headers={'content-type': 'application/json'})
            except httpx.TimeoutException:
                if attempt == 0:
                    continue
                raise NearAIUnavailable('timeout') from None
            if resp.status_code >= 500 and attempt == 0:
                continue
            break
        assert resp is not None
        self.calls_this_run += 1
        if resp.status_code != 200:
            text = resp.text.lower()
            reason = 'out of credits' if ('insufficient' in text or 'credit' in text) else \
                UNAVAILABLE_STATUS.get(resp.status_code, f'HTTP {resp.status_code}')
            raise NearAIUnavailable(reason)
        data = resp.json()
        usage = data.get('usage') or {}
        pt, ct = int(usage.get('prompt_tokens') or 0), int(usage.get('completion_tokens') or 0)
        self.ledger.record(self.model, pt, ct, self.price().cost(pt, ct))
        self._cache_put(raw, resp.content)
        return raw, resp.content, data, False

    def _cache_key(self, raw: bytes) -> Path:
        return self.cache_dir / 'cache' / f'{sha256_hex(self.model.encode() + b"|" + raw)}.json'

    def _cache_get(self, raw: bytes) -> dict[str, Any] | None:
        p = self._cache_key(raw)
        return json.loads(p.read_text()) if p.exists() else None

    def _cache_put(self, raw: bytes, response: bytes) -> None:
        p = self._cache_key(raw)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'response': response.decode()}))

    # ----- one signed review -----

    def review(self, kind: str, system: str, payload: dict[str, Any]) -> SignedReview:
        nonce = secrets.token_hex(32)
        att_text, att = self.attestation(nonce)
        gw = att.get('gateway_attestation') or {}
        raw_req, raw_resp, data, _cached = self.chat(system, json.dumps(payload, default=str, sort_keys=True))
        sig = self.signature(data['id'])
        # A model may be served by several TEE nodes: keep the attestation of the node whose key signed.
        nodes = att.get('model_attestations') or [{}]
        signer = str(sig.get('signing_address', '')).lower()
        model_att = next((a for a in nodes if str(a.get('signing_address', '')).lower() == signer), nodes[0])
        content = data['choices'][0]['message']['content'] or ''
        out = _json_object(content)
        verdict = 'INFO' if kind == 'EXPLAIN' else str(out.get('verdict', '')).upper()
        if verdict not in ('ALLOW', 'VETO', 'INFO'):
            raise NearAIUnavailable(f'bad verdict {verdict!r}')
        if not str(out.get('explanation', '')).strip():
            raise NearAIUnavailable('empty explanation')
        kind_sig = sig.get('signature_kind') or ('provider_tee' if sig['text'].count(':') >= 2 else 'gateway')
        evidence = {
            'request_body': raw_req.decode(), 'response_body': raw_resp.decode(), 'signed_text': sig['text'],
            'model_signing_address': model_att.get('signing_address', ''),
            'model_request_nonce': model_att.get('request_nonce', ''),
            'model_intel_quote': model_att.get('intel_quote', ''),
            'model_nvidia_payload': model_att.get('nvidia_payload', ''),
            'gateway_signing_address': gw.get('signing_address', ''),
            'gateway_request_nonce': gw.get('request_nonce', ''),
            'gateway_intel_quote': gw.get('intel_quote', ''),
            'model_compose_hash': (model_att.get('info') or {}).get('compose_hash', ''),
        }
        review = SignedReview(
            kind=kind, verdict=verdict, reason_code=_reason_code(out.get('reason_code') or kind if kind == 'EXPLAIN'
                                                                else out.get('reason_code') or verdict),
            risk=0 if kind == 'EXPLAIN' else max(0, min(100, int(out.get('risk_score', 50)))),
            explanation=' '.join(str(out.get('explanation', '')).split())[:600], model=self.model,
            chat_id=data['id'], request_sha256=sha256_hex(raw_req), response_sha256=sha256_hex(raw_resp),
            signature=sig['signature'], signing_address=sig.get('signing_address', ''), signature_kind=kind_sig,
            attestation_sha256=sha256_hex(att_text), nonce=nonce, verified=False,
            created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'), evidence=evidence)
        review.verified = all(c.ok for c in verify_receipt(review.to_receipt()))
        self._store(review)
        return review

    def _store(self, review: SignedReview) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / f'{review.rationale_hash}.json').write_text(json.dumps(review.to_receipt(), indent=1))


#: Cheapest NEAR-hosted (private, TEE) chat model on 2026-11-13 that is not a reasoning model; see docs/NEAR_AI_CLOUD.md.
DEFAULT_MODEL = 'Qwen/Qwen3-VL-30B-A3B-Instruct'


# ----------------------------------------------------------------------------------------------- attestation records

def record_attestation(client: NearAIClient) -> dict[str, Any]:
    """A free, non-billable attestation report fetched with a fresh nonce, kept byte-exact (sha256 reproducible)."""
    nonce = secrets.token_hex(32)
    raw, _ = client.attestation(nonce)
    return {'type': 'near-ai-attestation', 'model': client.model, 'nonce': nonce,
            'fetched_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'endpoint': f'{client.base_url}/attestation/report?model=<model>&signing_algo=ecdsa&nonce=<nonce>',
            'raw_sha256': sha256_hex(raw), 'raw': raw}


def verify_attestation_record(rec: dict[str, Any]) -> list[Check]:
    """Offline: the report is the bytes that were fetched, and every TEE (each model node and the gateway) binds
    its signing address and OUR nonce into its Intel TDX report data."""
    checks = [Check('report bytes', sha256_hex(rec['raw']) == rec['raw_sha256'], f'sha256 = {rec["raw_sha256"][:16]}...')]
    att = json.loads(rec['raw'])
    nodes = att.get('model_attestations') or []
    checks.append(Check('model TEE nodes', bool(nodes) and all(n.get('model_name') == rec['model'] for n in nodes),
                        f'{len(nodes)} node(s) serving {rec["model"]}'))
    for i, node in enumerate(nodes):
        try:
            version, tee, rd = tdx_report_data(node['intel_quote'])
            ok = (version == TDX_QUOTE_VERSION and tee == TDX_TEE_TYPE and node.get('request_nonce') == rec['nonce']
                  and rd == expected_report_data(node['signing_address'], rec['nonce']))
            checks.append(Check(f'model node {i} TEE binding', ok,
                                f'TDX v{version} report_data = {node["signing_address"]} + our nonce'))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check(f'model node {i} TEE binding', False, f'{type(exc).__name__}: {exc}'))
    gw = att.get('gateway_attestation') or {}
    try:
        version, tee, rd = tdx_report_data(gw['intel_quote'])
        ok = (version == TDX_QUOTE_VERSION and tee == TDX_TEE_TYPE and gw.get('request_nonce') == rec['nonce']
              and rd == expected_report_data(gw['signing_address'], rec['nonce']))
        checks.append(Check('gateway TEE binding', ok, f'TDX v{version} report_data = {gw["signing_address"]} + our nonce'))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check('gateway TEE binding', False, f'{type(exc).__name__}: {exc}'))
    return checks


NRAS_URL = 'https://nras.attestation.nvidia.com/v3/attest/gpu'


def nras_check(nvidia_payload: str, nonce: str, http: httpx.Client | None = None) -> Check:
    """Online (`verify-ai --full`): submit the GPU evidence to NVIDIA's attestation service and read its verdict.
    Checks the overall result and that NVIDIA saw our nonce. The JWT's NVIDIA signature itself is not re-verified."""
    import base64  # noqa: PLC0415
    http = http or httpx.Client(timeout=60)
    r = http.post(NRAS_URL, content=nvidia_payload if isinstance(nvidia_payload, str) else json.dumps(nvidia_payload),
                  headers={'content-type': 'application/json', 'accept': 'application/json'})
    if r.status_code != 200:
        return Check('NVIDIA NRAS (GPU)', False, f'HTTP {r.status_code} {r.text[:80]}')
    jwt = r.json()[0][1]
    body = jwt.split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    ok = claims.get('x-nvidia-overall-att-result') is True and claims.get('eat_nonce') == nonce
    return Check('NVIDIA NRAS (GPU)', ok, f'overall result {claims.get("x-nvidia-overall-att-result")}, '
                                         f'eat_nonce matches: {claims.get("eat_nonce") == nonce}')
