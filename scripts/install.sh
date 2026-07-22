#!/bin/sh
# Manthana installer — installs `uv` (if missing), then a Manthana CLI from a GitHub Release's
# wheels. The manthana-* packages come from the release; their normal deps (typer, fastapi, …)
# resolve from PyPI as usual.
#
#   engineer (agent):  curl -LsSf …/releases/latest/download/install.sh | sh
#   admin (server):    curl -LsSf …/releases/latest/download/install.sh | sh -s server
#   both:              curl -LsSf …/releases/latest/download/install.sh | sh -s all
#
# Env overrides: MANTHANA_REPO (owner/repo), MANTHANA_VERSION (a tag, or "latest"),
# MANTHANA_PYTHON (interpreter request passed to uv; defaults to ">=3.11").
set -eu

WHAT="${1:-agent}"   # agent | server | all
case "$WHAT" in agent|server|all) ;; *) echo "usage: install.sh [agent|server|all]" >&2; exit 2;; esac
REPO="${MANTHANA_REPO:-Jarus77/manthana}"
TAG="${MANTHANA_VERSION:-latest}"
echo "→ installing Manthana ($WHAT) (repo=$REPO, version=$TAG)"

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
if [ -n "${MANTHANA_WHEELS:-}" ]; then
  # Wheels already on disk. This exists so CI can run THIS script — the real one,
  # end to end — against a fresh build instead of reimplementing its steps and
  # proving nothing. Two installer bugs reached engineers behind a green
  # "install-proof" job that never executed this file.
  echo "→ using local wheels from $MANTHANA_WHEELS"
  cp "$MANTHANA_WHEELS"/*.whl "$TMP/"
else
  urls="$(curl -LsSf "$api" \
    | grep -o '"browser_download_url":[[:space:]]*"[^"]*\.whl"' \
    | sed 's/.*"\(http[^"]*\)".*/\1/')"
  if [ -z "$urls" ]; then
    echo "✗ no wheels found on the $TAG release of $REPO" >&2
    exit 1
  fi
  for u in $urls; do (cd "$TMP" && curl -LsSO "$u"); done
fi

# 3. install the requested CLI(s) as isolated tools; manthana-* resolve from the release wheels,
#    everything else from PyPI. (case avoids `set -e` tripping on a false test.)
#
# --force is REQUIRED, not cosmetic: without it `uv tool install` prints
# "`manthana` is already installed" and exits 0 without upgrading, so every
# engineer who already had Manthana was silently pinned to whatever version they
# first installed. This is an INSTALLER, but it is also the only upgrade path we
# ship, so it must always converge on the requested release.
#
# --python is equally REQUIRED. Without it uv resolves against whatever
# interpreter it happens to find first, which on a stock Mac is the system
# Python 3.9 — and the install dies with "requires Python >=3.11 … your
# requirements are unsatisfiable", which reads like our packaging is broken
# rather than like their default python is old. Given an explicit request uv
# will find a suitable interpreter or DOWNLOAD one, so this works on a machine
# with no modern Python at all.
#
# It is a range, not a pinned "3.12", so it keeps mirroring the wheels'
# requires-python instead of quietly pinning everyone to one minor version.
PY_REQ="${MANTHANA_PYTHON:->=3.11}"
case "$WHAT" in agent|all) uv tool install --force --python "$PY_REQ" --find-links "$TMP" manthana ;; esac
case "$WHAT" in server|all) uv tool install --force --python "$PY_REQ" --find-links "$TMP" manthana-server ;; esac

if [ "$WHAT" = "server" ]; then
  echo "✓ installed manthana-server. Next: manthana-server serve --tailscale   (or see docs/deploy.md)"
  manthana-server --help >/dev/null 2>&1 && echo "  run 'manthana-server init .' to drop deploy files (Caddyfile, compose, .env)"
else
  # Print the version LAST and labelled — engineers upgrading need to confirm the
  # new release actually landed, and a bare number under a "✓ installed" line was
  # easy to read as success when the install had in fact been skipped.
  echo "✓ installed. Next: manthana setup <invite-from-your-admin>"
  printf '  version: '; manthana version 2>/dev/null || echo "unknown"
fi
