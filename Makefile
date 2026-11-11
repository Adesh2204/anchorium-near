# Anchorium: NEAR HACKATHON entry. Run `make help`.
# The pinned numpy 2.5.2 needs Python >= 3.12: prefer an explicit 3.13/3.12 over an older default python3.
PY      ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)
VENV    := .venv
VPY     := $(VENV)/bin/python
CONTRACT_DIR := contracts/anchorium-guard

.PHONY: help setup freeze test test-py contract-build contract-test contract-deploy demo demo-onchain status agent dashboard

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
	$(VPY) -m pytest -q

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
