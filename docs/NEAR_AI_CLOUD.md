# NEAR AI Cloud: TEE-attested, signed risk reviews

Anchorium's AI risk co-pilot runs on **NEAR AI Cloud** only. The model runs inside an Intel TDX confidential VM with
NVIDIA TEE GPUs. Every review the agent acts on comes back with a TEE signature over the exact request and response
bytes, so the review can be checked by anyone, offline, long after the trade.

The co-pilot is **veto-only**. A VETO turns the agent's own vote into "no"; an ALLOW changes nothing, because the guard
contract still re-checks every rule and has the final word. The model may be stricter than the deterministic rules,
never looser (`anchorium/ai/copilot.py`).

## Why a TEE matters here

A trading agent's "AI said it was fine" is worthless if the operator can edit the AI's answer afterwards. With NEAR AI
Cloud the answer is signed inside the TEE by a key that never leaves it, and the TEE proves (Intel TDX quote + NVIDIA
GPU attestation) that this key was generated inside genuine hardware. Anchorium then puts a hash of the whole signed
receipt on-chain, next to the guard decision it explains.

## Model

`GET /v1/models` on 2026-11-13 listed 64 models with prices. Models owned by `nearai` run on NEAR AI's own TEE
infrastructure; the others are routed third-party models without TEE attestation of the model itself. Anchorium uses
the cheapest NEAR-hosted chat model that is not a reasoning model:

| | |
| --- | --- |
| Model | `Qwen/Qwen3-VL-30B-A3B-Instruct` (fp8), `NEAR_AI_MODEL` |
| Price (from `/v1/models`) | $0.15 per million input tokens, $0.55 per million output tokens |
| Worst-case review | ~800 prompt tokens + 300 output tokens ≈ $0.0003 |
| Why not `z-ai/glm-5.3-flash` | cheaper per token, but a reasoning model: its thinking tokens make the cost and the output length unpredictable |

## The flow (`anchorium/ai/near_ai.py`)

```
1. nonce = 32 random bytes
2. GET /v1/attestation/report?model=..&signing_algo=ecdsa&nonce=..         free, non-billable
   -> model TEE: signing_address, Intel TDX quote, NVIDIA GPU evidence;  gateway TEE: the same
3. POST /v1/chat/completions   (raw httpx; we keep the exact request and response bytes)   billable
4. GET /v1/signature/{chat_id}?model=..&signing_algo=ecdsa                   free
   -> text "<model>:<sha256(request)>:<sha256(response)>" + EIP-191 signature + signer address
5. verify (below); store the receipt; rationale_hash = sha256(canonical JSON of the receipt)
6. the agent journals rationale_hash with record_decision on the guard contract
```

## What `make verify-ai` checks (offline, zero keys, zero cost)

| Step | Check |
| --- | --- |
| request / response hash | sha256 of the stored bytes equals the hashes in the receipt |
| review from signed bytes | verdict, explanation and chat id are read from the signed response body, not typed in |
| signed text | the TEE-signed payload contains both hashes |
| signature | the EIP-191 signer recovered from the signature equals the attested signing address |
| model / gateway TEE binding | each Intel TDX v4 quote's `report_data` = that TEE's signing address + **our** nonce |
| fresh nonce | the attestation was fetched with this review's nonce, right before the call |
| rationale_hash | sha256 of the canonical receipt; `verify-ai <decision id>` compares it with the guard |
| `--full` (online) | sends the GPU evidence to NVIDIA NRAS: overall result `true` and NVIDIA saw our nonce |

Not checked offline: the Intel quote's certificate chain (paste the quote into https://proof.t16z.com/ or use
`dcap-qvl`) and the NRAS JWT's NVIDIA signature. The tool says so rather than claiming more.

```sh
make verify-ai                          # every committed fixture under fixtures/near_ai/
make verify-ai ARGS="--full"            # + NVIDIA NRAS (online, free)
make verify-ai ARGS=<rationale_hash>    # one receipt (fixtures/near_ai/receipts/ or ~/.anchorium/receipts/)
make verify-ai ARGS=<decision id>       # read the decision from the guard, find its receipt, compare the hash
```

## Zero-spend guardrails (in code)

- Only `/chat/completions` is billable. Attestation, model list and signature lookups are free.
- Before every billable call: refuse if this run already made `NEAR_AI_MAX_CALLS_PER_RUN` calls (default 10), or if
  the lifetime spend plus this call's worst case (`max_tokens` x output price + request bytes x input price) would
  exceed `NEAR_AI_BUDGET_USD` (default $0.05). The ledger is `~/.anchorium/near_ai_usage.json`, outside the repo;
  `make near-ai-usage` prints it.
- `max_tokens` = `NEAR_AI_MAX_TOKENS` (default 300), `temperature` 0, `stream` false.
- HTTP 401/402/403/429/5xx, a timeout or an "insufficient credits" reply falls back to the rules reviewer, labelled
  with the reason. At most one retry, and only on 5xx/timeout.
- Identical requests are cached and never paid twice.
- Every test mocks NEAR AI Cloud (`tests/near_ai_fake.py`), and `tests/conftest.py` fails any test that tries to
  reach a `near.ai` host.
- The judge path (`make demo`, `make paypal-demo` without a key, `make verify-ai`, the dashboard) makes no billable call.

## Evidence recorded so far (2026-11-13)

| What | Result | Check it |
| --- | --- | --- |
| Attestation of `Qwen/Qwen3-VL-30B-A3B-Instruct`, fetched 2026-11-13 08:41:48 UTC with a fresh nonce | model node `0x2665cce9…` and gateway `0xb98a789c…` both bind their key + our nonce in TDX v4 quotes; NVIDIA NRAS: overall result `true`, nonce matched | `make verify-ai ARGS="--full"` on `fixtures/near_ai/attestation-qwen-qwen3-vl-30b-a3b-instruct-2026-11-13.json` |
| Signed reviews (`/chat/completions`) | **not recorded yet**: on 2026-11-13 the account answered HTTP 402 `"No spending limit configured. Please contact support to set up credits."`. Nothing was spent (`make near-ai-usage`: $0). | — |

Until credits are enabled, the co-pilot runs the deterministic rules reviewer and labels it
`rules (out of credits)`; it never presents rules output as AI output. The signed-review path is implemented and
tested end to end against a fake NEAR AI Cloud that signs with a test-only key (`tests/test_near_ai.py`: valid
receipts pass; a tampered response, a wrong signer, an unbound key or a wrong nonce fail).

When credits exist: `make paypal-demo` (with `NEAR_AI_API_KEY` in `.env`) produces signed reviews under
`~/.anchorium/receipts/`; copy the ones to publish into `fixtures/near_ai/receipts/` and `make verify-ai` checks them.
