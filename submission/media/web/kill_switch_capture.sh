#!/usr/bin/env bash
# Shot 5b / gallery 08: the owner's kill switch on a MEDIA guard only (never the team or demo guard).
#   MEDIA_OWNER=anchorium-media-6018.testnet submission/media/web/kill_switch_capture.sh
# check_trade (view) -> owner pause (tx) -> check_trade (view) -> owner resume (tx) -> check_trade (view)
set -euo pipefail
: "${MEDIA_OWNER:?set MEDIA_OWNER=anchorium-media-<n>.testnet}"
[[ "$MEDIA_OWNER" =~ ^anchorium-media-[0-9]+\.testnet$ ]] || { echo "refusing: $MEDIA_OWNER is not a media owner"; exit 1; }
GUARD="guard.$MEDIA_OWNER"
ARGS='{"asset":"nep141:wrap.near","side":"BUY","notional_usd_cents":199,"slippage_bps":39}'
check() { echo "\$ check_trade BUY wNEAR \$1.99 on $GUARD"
  near contract call-function as-read-only "$GUARD" check_trade json-args "$ARGS" network-config testnet now 2>&1 \
    | grep -E '"allowed"|"reason"|^\{|^\}' ; echo; }
owner() { echo "\$ owner $MEDIA_OWNER calls $1 on $GUARD"
  near contract call-function as-transaction "$GUARD" "$1" json-args '{}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' \
    sign-as "$MEDIA_OWNER" network-config testnet sign-with-legacy-keychain send 2>&1 \
    | grep -E 'EVENT_JSON|Transaction ID|https://|succeeded|Function execution' ; echo; }
check; owner pause; check; owner resume; check
