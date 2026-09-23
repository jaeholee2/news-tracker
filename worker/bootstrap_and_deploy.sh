#!/usr/bin/env bash
# Deploys the keywords API worker, creating its KV namespace on the very
# first run and reusing the same namespace id on every run after that.
# Run from the worker/ directory. Requires CLOUDFLARE_API_TOKEN,
# CLOUDFLARE_ACCOUNT_ID and ADMIN_KEY in the environment.
set -euo pipefail

NS_TITLE="KEYWORDS_KV"

echo "Looking for an existing '$NS_TITLE' KV namespace..."
LIST_JSON="$(npx --yes wrangler@4 kv namespace list 2>/tmp/kv_list.stderr || true)"
KV_ID="$(printf '%s' "$LIST_JSON" | python3 -c "
import json, sys
try:
    namespaces = json.load(sys.stdin)
except Exception:
    namespaces = []
for ns in namespaces:
    if ns.get('title') == '$NS_TITLE':
        print(ns.get('id', ''))
        break
" || true)"

if [ -z "${KV_ID:-}" ]; then
  echo "No existing namespace found -- creating it."
  cat /tmp/kv_list.stderr || true
  CREATE_OUT="$(npx --yes wrangler@4 kv namespace create KEYWORDS_KV 2>&1 | tee /tmp/kv_create.log || true)"
  KV_ID="$(grep -oE '[0-9a-f]{32}' /tmp/kv_create.log | head -1 || true)"
  if [ -z "${KV_ID:-}" ]; then
    echo "::error::Could not create or find the KEYWORDS_KV namespace. wrangler output:"
    cat /tmp/kv_create.log
    exit 1
  fi
fi

echo "Using KV namespace id: $KV_ID"
sed -i "s/__KEYWORDS_KV_ID__/$KV_ID/" wrangler.toml

echo "Setting ADMIN_KEY secret..."
printf '%s' "$ADMIN_KEY" | npx --yes wrangler@4 secret put ADMIN_KEY

echo "Deploying worker..."
npx --yes wrangler@4 deploy
