"""A fake NEAR AI Cloud for tests (httpx.MockTransport). It signs like the real one (EIP-191 over
"<model>:<sha256 request>:<sha256 response>") with a TEST-ONLY key and returns TDX-v4-shaped quotes whose report_data
binds the signing address and the request nonce, so the real verifier code runs end to end without any network."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlparse

import coincurve
import httpx
from Crypto.Hash import keccak

MODEL = 'Qwen/test-model'
ALLOW = '{"verdict": "ALLOW", "risk_score": 12, "reason_code": "WITHIN_LIMITS", "explanation": "Slippage is low."}'
VETO = 'Sure: {"verdict": "VETO", "risk_score": 72, "reason_code": "thin margin!", "explanation": "Too tight."}'


def _k(data: bytes) -> bytes:
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


def fake_key(label: str) -> coincurve.PrivateKey:
    """Deterministic, test-only secp256k1 keys. Never used outside tests."""
    return coincurve.PrivateKey(hashlib.sha256(f'anchorium-test-only:{label}'.encode()).digest())


def address(key: coincurve.PrivateKey) -> str:
    return '0x' + _k(key.public_key.format(compressed=False)[1:])[-20:].hex()


def sign(key: coincurve.PrivateKey, text: str) -> str:
    msg = text.encode()
    sig = key.sign_recoverable(_k(b'\x19Ethereum Signed Message:\n' + str(len(msg)).encode() + msg), hasher=None)
    return '0x' + (sig[:64] + bytes([sig[64] + 27])).hex()


def tdx_quote(addr: str, nonce: str) -> str:
    header = (4).to_bytes(2, 'little') + (2).to_bytes(2, 'little') + (0x81).to_bytes(4, 'little') + bytes(40)
    body = bytearray(584)
    body[520:584] = bytes.fromhex(addr[2:]).ljust(32, b'\0') + bytes.fromhex(nonce)
    return (header + bytes(body) + bytes(64)).hex()


class FakeNearAI:
    def __init__(self, reply: str = ALLOW, *, kind: str = 'provider_tee', chat_status: int = 200,
                 chat_text: str = '{"error": {"message": "boom"}}', wrong_signer: bool = False, bad_binding: bool = False,
                 price: tuple[str, str] = ('0.00000015', '0.00000055')):
        self.reply, self.kind, self.chat_status, self.chat_text = reply, kind, chat_status, chat_text
        self.wrong_signer, self.bad_binding, self.price = wrong_signer, bad_binding, price
        self.model_key, self.gw_key = fake_key('model'), fake_key('gateway')
        self.chat_calls = 0
        self.requests: list[bytes] = []
        self.responses: dict[str, bytes] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        assert request.headers['Authorization'] == 'Bearer test-key'
        if url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': MODEL, 'pricing': {
                'prompt': self.price[0], 'completion': self.price[1]}}]})
        if url.path == '/v1/attestation/report':
            nonce = q['nonce']
            bound = 'ab' * 32 if self.bad_binding else nonce
            maddr, gaddr = address(self.model_key), address(self.gw_key)
            return httpx.Response(200, json={
                'model_attestations': [{'model_name': q['model'], 'signing_address': maddr, 'request_nonce': nonce,
                                        'intel_quote': tdx_quote(maddr, bound), 'nvidia_payload': '{"nonce": "x"}',
                                        'info': {'compose_hash': 'c0ffee'}}],
                'gateway_attestation': {'signing_address': gaddr, 'request_nonce': nonce,
                                        'intel_quote': tdx_quote(gaddr, nonce)}})
        if url.path == '/v1/chat/completions':
            self.chat_calls += 1
            self.requests.append(request.content)
            if self.chat_status != 200:
                return httpx.Response(self.chat_status, text=self.chat_text)
            chat_id = f'chatcmpl-{self.chat_calls}'
            raw = json.dumps({'id': chat_id, 'choices': [{'message': {'role': 'assistant', 'content': self.reply}}],
                              'usage': {'prompt_tokens': 400, 'completion_tokens': 60}}).encode()
            self.responses[chat_id] = request.content + b'\n' + raw
            return httpx.Response(200, content=raw, headers={'content-type': 'application/json'})
        if url.path.startswith('/v1/signature/'):
            chat_id = url.path.rsplit('/', 1)[1]
            req, _, resp = self.responses[chat_id].partition(b'\n')
            rh, sh = hashlib.sha256(req).hexdigest(), hashlib.sha256(resp).hexdigest()
            text = f'{q["model"]}:{rh}:{sh}' if self.kind == 'provider_tee' else f'{rh}:{sh}'
            key = fake_key('attacker') if self.wrong_signer else (
                self.model_key if self.kind == 'provider_tee' else self.gw_key)
            return httpx.Response(200, json={'text': text, 'signature': sign(key, text), 'signing_address': address(key),
                                             'signing_algo': 'ecdsa', 'signature_kind': self.kind})
        return httpx.Response(404, json={'error': 'unknown path'})
