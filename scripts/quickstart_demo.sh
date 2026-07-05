#!/bin/sh
# End-to-end onboarding demo — zero infra, no permanent system changes. Shows the whole
# smooth flow: admin `quickstart` + `enroll` (2 commands) → engineer `setup` (1 command) →
# `doctor`. Runs everything in throwaway temp dirs and stops the server on exit.
#
# Local dev uses `uv run`; a real deploy uses the installed `manthana` / `manthana-server`.
#   ./scripts/quickstart_demo.sh
set -eu

PORT="${PORT:-8020}"
URL="http://127.0.0.1:$PORT"
SRV_DIR="$(mktemp -d)"
ENG_HOME="$(mktemp -d)"
SRV_PID=""
cleanup() {
  [ -n "$SRV_PID" ] && kill "$SRV_PID" 2>/dev/null || true
  rm -rf "$SRV_DIR" "$ENG_HOME"
}
trap cleanup EXIT

echo "1) admin: quickstart server → $URL  (SQLite + in-memory, no Docker)"
uv run manthana-server quickstart --port "$PORT" --k-anon 1 --data "$SRV_DIR" \
  >"$SRV_DIR/server.log" 2>&1 &
SRV_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if uv run python -c "import urllib.request as u,sys; sys.exit(0 if u.urlopen('$URL/readyz',timeout=2).status==200 else 1)" 2>/dev/null; then
    break
  fi
  sleep 1
done
grep 'admin token' "$SRV_DIR/server.log" || true

echo
echo "2) admin: enroll the team (one shared Slack-able invite)"
BLOB="$(uv run manthana-server enroll acme platform --open --server-url "$URL" --data "$SRV_DIR" \
  | grep -o 'mia_[A-Za-z0-9_-]*')"
echo "   → manthana setup $BLOB"

echo
echo "3) engineer: onboard in ONE command"
MANTHANA_DATA_HOME="$ENG_HOME" uv run manthana setup "$BLOB" --no-service --actor alice@acme.com

echo
echo "4) engineer: health check"
MANTHANA_DATA_HOME="$ENG_HOME" uv run manthana doctor || true

echo
echo "founder console: $URL/ui   (admin token above; k-anon 1 for this solo demo)"
echo "done — server + temp data are torn down on exit."
