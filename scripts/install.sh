#!/bin/sh
# Manthana agent installer — installs `uv` (if missing), then the `manthana` CLI from a
# GitHub Release's wheels. The manthana-* packages come from the release; their normal
# deps (typer, fastapi, httpx, …) resolve from PyPI as usual.
#
#   curl -LsSf https://github.com/Suraj-gameramp/manthana/releases/latest/download/install.sh | sh
#
# Env overrides: MANTHANA_REPO (owner/repo), MANTHANA_VERSION (a tag, or "latest").
set -eu

REPO="${MANTHANA_REPO:-Suraj-gameramp/manthana}"
TAG="${MANTHANA_VERSION:-latest}"
echo "→ installing Manthana (repo=$REPO, version=$TAG)"

# 1. uv (fast Python package manager) — installs to ~/.local/bin
if ! command -v uv >/dev/null 2>&1; then
  echo "→ installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. discover + download the release wheels (no filename guessing — read the API)
if [ "$TAG" = "latest" ]; then
  api="https://api.github.com/repos/$REPO/releases/latest"
else
  api="https://api.github.com/repos/$REPO/releases/tags/$TAG"
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
urls="$(curl -LsSf "$api" \
  | grep -o '"browser_download_url":[[:space:]]*"[^"]*\.whl"' \
  | sed 's/.*"\(http[^"]*\)".*/\1/')"
if [ -z "$urls" ]; then
  echo "✗ no wheels found on the $TAG release of $REPO" >&2
  exit 1
fi
for u in $urls; do (cd "$TMP" && curl -LsSO "$u"); done

# 3. install the CLI as an isolated tool; manthana-* resolve from the release, the rest from PyPI
uv tool install --find-links "$TMP" manthana

echo "✓ installed. Next: manthana setup <invite-from-your-admin>"
manthana version 2>/dev/null || true
