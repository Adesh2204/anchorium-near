#!/usr/bin/env bash
# Deploy the Anchorium guard to NEAR testnet. Idempotent: existing accounts are reused.
#
#   OWNER=<name>.testnet scripts/near/deploy_testnet.sh
#
# Creates, if missing:
#   $OWNER                  faucet-sponsored (free) owner account: the human who sets the rules
#   guard.$OWNER            the contract account (funded by the owner; prefix via GUARD_PREFIX)
#   agent.$OWNER            the bot's account (funded by the owner)
# then deploys the wasm with `new(owner, agent, policy)` and grants agent.$OWNER a function-call key that can
# call only record_decision / record_outcome on guard.$OWNER.
#
# Keys are written by near-cli-rs to ~/.near-credentials (the legacy keychain) and never into this repository.
# Requires: near-cli-rs >= 0.31 and python3. The committed wasm is used, so Rust/cargo-near are not needed.
set -euo pipefail

: "${OWNER:?set OWNER, e.g. OWNER=yourname-anchorium.testnet}"
[[ "$OWNER" == *.testnet ]] || { echo "OWNER must be a .testnet account (this script never touches mainnet)"; exit 1; }
GUARD="${GUARD_PREFIX:-guard}.$OWNER"
AGENT="agent.$OWNER"
NETWORK=testnet
RPC="${NEAR_RPC_URL:-https://rpc.testnet.near.org}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
POLICY_FILE="${POLICY_FILE:-$HERE/policy.testnet.json}"
WASM="${WASM:-$ROOT/contracts/anchorium-guard/res/anchorium_guard.wasm}"  # committed build; see res/SHA256SUMS
GUARD_FUNDING="${GUARD_FUNDING:-6}"
AGENT_FUNDING="${AGENT_FUNDING:-1}"
KEY_ALLOWANCE="${KEY_ALLOWANCE:-0.9}"
GRANT_AGENT_KEY="${GRANT_AGENT_KEY:-1}"  # 0 for a read-only demo guard nobody can journal to

exists() {
  curl -s "$RPC" -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"query\",\"params\":{\"request_type\":\"view_account\",\"finality\":\"final\",\"account_id\":\"$1\"}}" \
    | python3 -c 'import sys,json; sys.exit(0 if "result" in json.load(sys.stdin) else 1)'
}

has_code() {
  curl -s "$RPC" -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"query\",\"params\":{\"request_type\":\"view_account\",\"finality\":\"final\",\"account_id\":\"$1\"}}" \
    | python3 -c 'import sys,json; r=json.load(sys.stdin).get("result",{}); sys.exit(0 if r.get("code_hash","1"*32)!="11111111111111111111111111111111" else 1)'
}

# Output filter: never echo key material, even if a CLI version prints it.
quiet() { grep -viE 'private|secret|seed phrase' || true; }

if exists "$OWNER"; then echo "owner  $OWNER exists"; else
  echo "owner  creating $OWNER via the testnet faucet"
  near account create-account sponsor-by-faucet-service "$OWNER" autogenerate-new-keypair \
    save-to-legacy-keychain network-config "$NETWORK" create </dev/null 2>&1 | quiet
fi

for pair in "$GUARD:$GUARD_FUNDING" "$AGENT:$AGENT_FUNDING"; do
  account="${pair%%:*}"; funding="${pair##*:}"
  if exists "$account"; then echo "account $account exists"; else
    echo "account creating $account ($funding NEAR from $OWNER)"
    near account create-account fund-myself "$account" "$funding NEAR" autogenerate-new-keypair \
      save-to-legacy-keychain sign-as "$OWNER" network-config "$NETWORK" sign-with-legacy-keychain send \
      </dev/null 2>&1 | quiet
  fi
done

if has_code "$GUARD"; then
  echo "deploy $GUARD already has code: skipping (redeploying would not re-run init)"
else
  [[ -f "$WASM" ]] || (cd "$ROOT/contracts/anchorium-guard" && cargo near build non-reproducible-wasm)
  ARGS="$(python3 -c 'import json,sys; print(json.dumps({"owner":sys.argv[1],"agent":sys.argv[2],"policy":json.load(open(sys.argv[3]))}))' "$OWNER" "$AGENT" "$POLICY_FILE")"
  echo "deploy $GUARD with new(owner=$OWNER, agent=$AGENT, policy=$(basename "$POLICY_FILE"))"
  near contract deploy "$GUARD" use-file "$WASM" with-init-call new json-args "$ARGS" \
    prepaid-gas '100 Tgas' attached-deposit '0 NEAR' network-config "$NETWORK" sign-with-legacy-keychain send \
    </dev/null 2>&1 | quiet
fi

has_fc_key() {
  curl -s "$RPC" -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"query\",\"params\":{\"request_type\":\"view_access_key_list\",\"finality\":\"final\",\"account_id\":\"$AGENT\"}}" \
    | python3 -c 'import sys,json; keys=json.load(sys.stdin)["result"]["keys"]; sys.exit(0 if any(isinstance(k["access_key"]["permission"],dict) and k["access_key"]["permission"]["FunctionCall"]["receiver_id"]==sys.argv[1] for k in keys) else 1)' "$GUARD"
}

if [[ "$GRANT_AGENT_KEY" != "1" ]]; then
  echo "key    skipped (GRANT_AGENT_KEY=0): no key can journal to $GUARD"
elif has_fc_key; then
  echo "key    $AGENT already has a function-call key for $GUARD"
else
  echo "key    granting $AGENT a function-call key: receiver $GUARD, methods record_decision, record_outcome"
  near account add-key "$AGENT" grant-function-call-access --allowance "$KEY_ALLOWANCE NEAR" \
    --contract-account-id "$GUARD" --function-names 'record_decision, record_outcome' \
    autogenerate-new-keypair save-to-legacy-keychain network-config "$NETWORK" sign-with-legacy-keychain send \
    </dev/null 2>&1 | quiet
fi

echo
echo "Done. Put these in .env:"
echo "  GUARD_CONTRACT_ID=$GUARD"
echo "  AGENT_ACCOUNT_ID=$AGENT"
echo "Explorer: https://testnet.nearblocks.io/address/$GUARD"
