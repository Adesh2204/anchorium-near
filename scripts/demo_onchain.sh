#!/usr/bin/env bash
# Path B: your own free testnet accounts and guard, then a dry-run cycle journaled on-chain.
#
#   OWNER=<pick-a-new-name>.testnet make demo-onchain
#
# Creates OWNER (testnet faucet, free), guard.OWNER and agent.OWNER, deploys the committed wasm, grants the agent
# a function-call key, then runs `python -m anchorium demo --onchain` against YOUR contract.
# Costs nothing: testnet only, and 1Click is used with dry quotes only. Keys go to ~/.near-credentials.
set -euo pipefail
: "${OWNER:?set OWNER to a new testnet account name, e.g. OWNER=judge-$RANDOM.testnet}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
"$HERE/near/deploy_testnet.sh"
echo
export GUARD_CONTRACT_ID="guard.$OWNER" AGENT_ACCOUNT_ID="agent.$OWNER" DRY_RUN=1 LIVE=0
exec "$ROOT/.venv/bin/python" -m anchorium demo --onchain "$@"
