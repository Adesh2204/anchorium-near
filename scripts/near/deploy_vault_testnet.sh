#!/usr/bin/env bash
# Vault demo on NEAR testnet (free: faucet accounts, mock token). Never touches the team's guards.
#
#   OWNER=<pick-a-new-name>.testnet make vault-demo
#
# Creates (or reuses) OWNER, guard.OWNER and agent.OWNER via deploy_testnet.sh (demo policy: cooldown 0, 3 positions),
# then usdc.OWNER (a TEST-ONLY mock NEP-141 "mUSDC", contracts/mock-ft) and vault.OWNER (contracts/anchorium-vault,
# initialised with accept_dry_run=true because the demo journals DRY_RUN decisions; a production vault uses false).
# The agent gets a second function-call key: receiver vault.OWNER, method `execute` only. The owner funds the vault
# with 10 mUSDC. Then `python -m anchorium vault-demo` runs the flow. Keys go to ~/.near-credentials (never git).
set -euo pipefail
: "${OWNER:?set OWNER to a new testnet account name, e.g. OWNER=vault-demo-$RANDOM.testnet}"
[[ "$OWNER" == *.testnet ]] || { echo "OWNER must be a .testnet account (this script never touches mainnet)"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
NETWORK=testnet
RPC="${NEAR_RPC_URL:-https://rpc.testnet.fastnear.com}"
USDC="usdc.$OWNER"
VAULT="vault.$OWNER"
AGENT="agent.$OWNER"
GUARD="guard.$OWNER"
quiet() { grep -viE 'private|secret|seed phrase' || true; }
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
call_owner() {  # contract method json deposit
  near contract call-function as-transaction "$1" "$2" json-args "$3" prepaid-gas '100 Tgas' attached-deposit "$4" \
    sign-as "$OWNER" network-config "$NETWORK" sign-with-legacy-keychain send </dev/null 2>&1 | quiet | grep -E "Transaction ID|succeeded|Error|error" || true
}

# The faucet gives a new owner 10 NEAR: guard 3 (214 KB wasm), agent 1, token 2 (150 KB), vault 2.5 (180 KB).
GUARD_FUNDING="${GUARD_FUNDING:-3}" POLICY_FILE="$HERE/policy.vault-demo.json" OWNER="$OWNER" "$HERE/deploy_testnet.sh"

for pair in "$USDC:2" "$VAULT:2.5"; do
  account="${pair%%:*}"; funding="${pair##*:}"
  if exists "$account"; then echo "account $account exists"; else
    echo "account creating $account ($funding NEAR from $OWNER)"
    near account create-account fund-myself "$account" "$funding NEAR" autogenerate-new-keypair \
      save-to-legacy-keychain sign-as "$OWNER" network-config "$NETWORK" sign-with-legacy-keychain send </dev/null 2>&1 | quiet
  fi
done

if has_code "$USDC"; then echo "deploy $USDC already has code"; else
  echo "deploy $USDC (TEST-ONLY mock NEP-141, 100 mUSDC minted to $OWNER)"
  near contract deploy "$USDC" use-file "$ROOT/contracts/mock-ft/res/mock_ft.wasm" with-init-call new \
    json-args "{\"owner\":\"$OWNER\",\"total_supply\":\"100000000\",\"symbol\":\"mUSDC\"}" prepaid-gas '100 Tgas' \
    attached-deposit '0 NEAR' network-config "$NETWORK" sign-with-legacy-keychain send </dev/null 2>&1 | quiet
fi

if has_code "$VAULT"; then echo "deploy $VAULT already has code"; else
  echo "deploy $VAULT (accept_dry_run=true: DEMO deployment)"
  ARGS="{\"owner\":\"$OWNER\",\"agent\":\"$AGENT\",\"guard\":\"$GUARD\",\"quote_token\":\"$USDC\",\"quote_decimals\":6,\
\"allowed_tokens\":[\"$USDC\"],\"accept_dry_run\":true,\"max_releases_per_day\":10,\"max_quote_per_day\":\"20000000\",\
\"max_storage_per_day\":\"12500000000000000000000\"}"
  near contract deploy "$VAULT" use-file "$ROOT/contracts/anchorium-vault/res/anchorium_vault.wasm" with-init-call new \
    json-args "$ARGS" prepaid-gas '100 Tgas' attached-deposit '0 NEAR' network-config "$NETWORK" \
    sign-with-legacy-keychain send </dev/null 2>&1 | quiet
fi

has_vault_key() {
  curl -s "$RPC" -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"query\",\"params\":{\"request_type\":\"view_access_key_list\",\"finality\":\"final\",\"account_id\":\"$AGENT\"}}" \
    | python3 -c 'import sys,json; keys=json.load(sys.stdin)["result"]["keys"]; sys.exit(0 if any(isinstance(k["access_key"]["permission"],dict) and k["access_key"]["permission"]["FunctionCall"]["receiver_id"]==sys.argv[1] for k in keys) else 1)' "$VAULT"
}
if has_vault_key; then echo "key    $AGENT already has a function-call key for $VAULT"; else
  echo "key    granting $AGENT a function-call key: receiver $VAULT, method execute (1 NEAR gas allowance)"
  near account add-key "$AGENT" grant-function-call-access --allowance '1 NEAR' --contract-account-id "$VAULT" \
    --function-names 'execute' autogenerate-new-keypair save-to-legacy-keychain network-config "$NETWORK" \
    sign-with-legacy-keychain send </dev/null 2>&1 | quiet
fi

echo "fund   register $VAULT on $USDC, then the owner deposits 10 mUSDC (ft_transfer_call)"
call_owner "$USDC" storage_deposit "{\"account_id\":\"$VAULT\",\"registration_only\":true}" '0.00125 NEAR'
call_owner "$USDC" ft_transfer_call "{\"receiver_id\":\"$VAULT\",\"amount\":\"10000000\",\"msg\":\"\"}" '1 yoctoNEAR'
echo
echo "Ready: guard $GUARD, vault $VAULT, token $USDC (mock). Next: python -m anchorium vault-demo --owner $OWNER"
