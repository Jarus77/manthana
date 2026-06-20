#!/usr/bin/env bash
# Live multi-contributor demo against a running stack (docker compose up).
# Provisions an org + team + 4 engineers, pushes a released compaction as each,
# then runs org skill mining (clears the k-anon floor of 4, drops names) and a
# founder query. Uses a throwaway org ("demoacme") so it won't touch real data.
#
#   docker compose up -d && ./scripts/demo_team.sh
set -euo pipefail

SERVER="${SERVER:-http://localhost:8000}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADMIN="$(grep -E '^MANTHANA_SERVER_ADMIN_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)"
ADMIN="${ADMIN:-demo-admin}"
ORG="demoacme"
INTENT="fix a flaky pytest timeout in CI by raising the asyncio wait budget"

mint() {  # actor -> token (ensures org+team exist; idempotent)
  docker compose exec -T server manthana-server onboard \
    "$ORG" "Demo Acme" platform "Platform" "$1" 2>/dev/null | tail -1
}

push() {  # token actor cid -> POST a released compaction
  local token="$1" actor="$2" cid="$3"
  curl -s -o /dev/null -w "  pushed $actor -> %{http_code}\n" \
    -H "Authorization: Bearer $token" -H "Content-Type: application/json" \
    -d "{\"compactions\":[{\"kind\":\"engineering\",\"id\":\"$cid\",\"session_id\":\"$cid\",\
\"actor\":\"$actor\",\"surface\":\"claude_code\",\"project\":\"ci\",\
\"started_at\":\"2026-01-01T00:00:00Z\",\"ended_at\":\"2026-01-01T00:00:00Z\",\
\"duration_seconds\":1.0,\"task_intent\":\"$INTENT\",\"approach\":\"raised the wait budget\",\
\"outcome\":\"success\",\"est_cost_usd\":0.5,\"tier_used\":\"opus\",\"released\":true}]}" \
    "$SERVER/v1/compactions"
}

echo "== provisioning + pushing 4 engineers' released compactions =="
for i in 0 1 2 3; do
  actor="eng$i@$ORG.com"
  push "$(mint "$actor")" "$actor" "demo-c$i"
done

echo
echo "== org skill mining (k-anon >= 4; contributor names dropped) =="
curl -s -H "X-Admin-Token: $ADMIN" -H "Content-Type: application/json" \
  -d "{\"org_id\":\"$ORG\"}" "$SERVER/v1/admin/mine-skills" | python3 -m json.tool

echo
echo "== founder query =="
curl -s -H "X-Admin-Token: $ADMIN" -H "Content-Type: application/json" \
  -d "{\"org_id\":\"$ORG\",\"query\":\"what is the team working on?\"}" \
  "$SERVER/v1/founder/query" | python3 -m json.tool
