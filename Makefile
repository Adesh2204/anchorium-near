# Anchorium: NEAR HACKATHON entry. Run `make help`.
# The pinned numpy 2.5.2 needs Python >= 3.12: prefer an explicit 3.13/3.12 over an older default python3.
PY      ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)
VENV    := .venv
VPY     := $(VENV)/bin/python
CONTRACT_DIR := contracts/anchorium-guard
VAULT_DIR    := contracts/anchorium-vault


.PHONY: paypal-demo verify-ai near-ai-usage near-ai-attest vault-test vault-build vault-demo
.PHONY: help setup freeze test test-py contract-build contract-test contract-deploy demo demo-onchain status agent dashboard autonomous autonomous-status live-preflight media-setup media-gallery

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup: ## Create .venv and install the Python package (Python >= 3.12)
	@$(PY) -c 'import sys; assert sys.version_info >= (3, 12), "Python >= 3.12 required (numpy 2.5.2), found " + sys.version.split()[0] + "; run: make setup PY=/path/to/python3.12"'
	$(PY) -m venv $(VENV)
	$(VPY) -m pip install -q --upgrade pip
	$(VPY) -m pip install -q -e '.[dev]'
	@echo "setup OK: $$($(VPY) --version)"

freeze: ## Verify the vendored strategy is byte-identical to its source
	$(VPY) scripts/check_strategy_freeze.py

test-py: ## Python tests (no network)
	$(VPY) -m pytest -q $(PYTEST_ARGS)

test: freeze test-py contract-test ## Freeze check + Python tests + contract tests

contract-build: ## Build the guard wasm (cargo-near) and refresh res/ + its SHA256SUMS
	cd $(CONTRACT_DIR) && cargo near build non-reproducible-wasm
	cp $(CONTRACT_DIR)/target/near/anchorium_guard.wasm $(CONTRACT_DIR)/res/
	cd $(CONTRACT_DIR)/res && shasum -a 256 anchorium_guard.wasm

contract-test: ## Contract unit + sandbox tests
	cd $(CONTRACT_DIR) && cargo test

contract-deploy: ## Deploy the guard to NEAR testnet (needs near-cli-rs + a testnet account)
	scripts/near/deploy_testnet.sh

demo: ## Path A: zero keys/funds. REPLAY signals -> live testnet guard -> real 1Click dry quotes
	$(VPY) -m anchorium demo

demo-onchain: ## Path B: OWNER=<new>.testnet -> your own free accounts + guard, journaled on-chain
	scripts/demo_onchain.sh

status: ## Policy, stats and latest decisions read from the contract (keyless)
	$(VPY) -m anchorium status

agent: ## Agent loop on live public candles (DRY_RUN; journals with the agent key)
	$(VPY) -m anchorium run

dashboard: ## Read-only dashboard on http://127.0.0.1:8000 (reads the testnet contract)
	$(VPY) -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000

autonomous: ## Version A autonomous rounds (DRY_RUN unless .env has LIVE=1; LIVE asks you to type GO LIVE)
	$$(command -v caffeinate >/dev/null && echo caffeinate -i) $(VPY) -m anchorium autonomous $(ARGS)

autonomous-status: ## Version A session status, a fresh valuation of an open round, the last 5 events
	$(VPY) -m anchorium autonomous-status $(ARGS)

live-preflight: ## PASS/FAIL checks before a LIVE version A session (read-only)
	$(VPY) -m anchorium live-preflight

media-setup: ## Media toolchain venv (Playwright, Pillow, qrcode); also needs vhs, ffmpeg, pngquant, oxipng + fonts
	python3.12 -m venv submission/media/.venv && submission/media/.venv/bin/pip install -q -r submission/media/requirements.txt && submission/media/.venv/bin/python -m playwright install chromium

media-gallery: ## Render the 11 gallery PNGs + contact sheet from the real captures in submission/media/raw/
	submission/media/.venv/bin/python submission/media/web/render.py

paypal-demo: ## Fiat on-ramp: PayPal sandbox funds the paper budget; NEAR AI Cloud reviews; the NEAR guard decides
	$(VPY) -m anchorium paypal-demo $(ARGS)

verify-ai: ## Offline check of the NEAR AI Cloud evidence (TEE attestation, signatures, on-chain hash); zero keys, zero cost
	$(VPY) -m anchorium verify-ai $(ARGS)

near-ai-usage: ## NEAR AI Cloud spend ledger (every billable call, estimated USD)
	$(VPY) -m anchorium near-ai-usage

near-ai-attest: ## Record a fresh NEAR AI Cloud TEE attestation into fixtures/near_ai/ (free endpoint; needs NEAR_AI_API_KEY)
	$(VPY) -m anchorium near-ai-attest

vault-test: ## Vault contract: unit tests + sandbox test (vault + committed guard wasm + mock USDC on a local NEAR node)
	cd $(VAULT_DIR) && cargo test

vault-build: ## Build the vault wasm (cargo-near) and refresh res/ + its SHA256SUMS
	cd $(VAULT_DIR) && cargo near build non-reproducible-wasm
	cp $(VAULT_DIR)/target/near/anchorium_vault.wasm $(VAULT_DIR)/res/
	cd $(VAULT_DIR)/res && shasum -a 256 anchorium_vault.wasm

vault-demo: ## Vault on NEAR testnet (free): OWNER=<new>.testnet -> deploy guard + vault + mock USDC, then the flow
	@test -n "$(OWNER)" || { echo "usage: OWNER=<new-name>.testnet make vault-demo"; exit 1; }
	scripts/near/deploy_vault_testnet.sh
	$(VPY) -m anchorium vault-demo --owner $(OWNER)
