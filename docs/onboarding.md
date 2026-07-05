# Onboarding a team (admin: 2 commands · engineer: 1 command)

The whole flow is designed to be fast and unattended. Try it locally end-to-end with no
infra: `./scripts/quickstart_demo.sh`.

## Admin — stand up + provision (2 commands)

**1. Start a server.** For a pilot, zero-infra (SQLite + in-memory, no Docker/Postgres):

```bash
manthana-server quickstart
# → prints the admin token + console URL, then serves on http://127.0.0.1:8000
#   (secrets are generated once and persisted to ~/.manthana-server, so restarts keep
#    working; use --port / --k-anon / --data to override.)
```

For a real deployment instead, see [deploy.md](deploy.md) (Docker + Postgres + object store).

**2. Enroll the team.** One shared invite to drop in Slack, or one per engineer:

```bash
# one shared, multi-use team invite (each engineer supplies their own email at setup)
manthana-server enroll acme platform --open --server-url https://manthana.acme.com

# …or a single-use, identity-bound invite per engineer
manthana-server enroll acme platform --emails team.txt --server-url https://manthana.acme.com
```

Each prints a `manthana setup <blob>` one-liner. The **team token is never in the invite** —
it's issued only when the engineer redeems the code. Cross-engineer skill mining needs **≥4
contributors**, so onboard the team, not just one person. List invites: `manthana-server
invites acme`.

## Engineer — one command

First, install the CLI (once):

```bash
curl -LsSf https://github.com/Suraj-gameramp/manthana/releases/latest/download/install.sh | sh
```

Then paste the one-liner your admin sent:

```bash
manthana setup mia_…          # add --actor you@acme.com for an open team invite
```

That single command redeems the invite, connects, installs auto-capture at login (macOS),
runs a first capture, and prints a confirmation:

```
✓ connected as you@acme.com → https://manthana.acme.com
  captured 14 session(s) · auto-capture: running at login (launchd)
  dashboard: http://127.0.0.1:8765  ·  health check: manthana doctor
```

Flags: `--no-service` (skip the login daemon and run `manthana watch` yourself); on Linux the
daemon step prints a `systemd --user` note (auto-install is macOS-only for now).

## Check it's working

```bash
manthana doctor     # configured? · server reachable · token accepted · daemon · data flowing
```

Exits non-zero if a critical check fails — handy in a setup script.

## Daily use — dashboard only

`manthana dashboard` (or leave it open at <http://127.0.0.1:8765>). From there you:

- set a session **Work / Personal** (personal **never** leaves the laptop),
- **Compact** sessions you want digested (the only token-spending step, and it's deliberate),
- **Release** what you're willing to share — released compactions auto-sync on the next watch
  cycle (or hold within the 10-minute window; secrets/PII are redacted on the way out).

The founder sees only released, redacted, k-anonymized data via the server console (`/ui`).

## What runs where

| Piece | Where | Token cost |
|---|---|---|
| capture (auto) | `manthana watch` (launchd) | none |
| auto-release + auto-sync | `manthana watch` | none |
| compaction | dashboard button / `manthana compact` | yes (your Claude) |
| review / release / mode | dashboard | none |

## Identity

Your `actor` comes from the invite (bound) or `--actor` at setup, else `MANTHANA_ACTOR`, else
your git email, else the OS user. Distinct actors are what let the org clear the k-anonymity floor.
