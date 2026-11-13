#!/usr/bin/env bash
# Fails if the staged diff (default) or the given git range adds anything that looks like a secret:
# an API key (sk-...), a NEAR private key (ed25519: + 64+ base58 chars) or a JWT. Public keys (44 chars) pass.
set -euo pipefail
diff="$(if [ $# -gt 0 ]; then git log -p "$1"; else git diff --cached; fi)"
hits="$(printf '%s\n' "$diff" | grep -E '^\+' | grep -nE 'sk-[A-Za-z0-9]{20,}|ed25519:[1-9A-HJ-NP-Za-km-z]{60,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}' || true)"
if [ -n "$hits" ]; then echo "secret scan: FAIL"; printf '%s\n' "$hits" | cut -c1-20; exit 1; fi
echo "secret scan: clean"
