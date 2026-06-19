#!/usr/bin/env bash
# Load .env (if present) and start the Manthana server — keeps secrets out of the
# command line / shell history. Usage:
#   cp .env.example .env && $EDITOR .env
#   ./scripts/serve.sh [--port 8000]
set -euo pipefail

# Resolve the repo root regardless of where the script is invoked from.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  # `set -a` exports every variable assigned while sourcing .env.
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  echo "loaded .env"
else
  echo "no .env found — copy .env.example to .env first (running on defaults)" >&2
fi

exec uv run manthana-server serve "$@"
