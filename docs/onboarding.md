# Onboarding an engineer (zero-touch after setup)

Goal: set up a company laptop once, then it runs itself. The employee just opens
the **dashboard** to review/compact/release; they never touch the terminal again.

## Admin: provision the engineer (once)

On the server host, mint the engineer's token (creates org+team if needed):

```bash
docker compose exec server manthana-server onboard \
    acme "Acme Inc"  platform "Platform"  bob@acme.com
# → prints the engineer's agent token
```

Cross-engineer skill mining needs **≥4 contributors** in a team, so onboard the
team. (See [deploy.md](deploy.md) to stand the server up.)

## On the laptop: one-time setup

```bash
# 1. connect the agent to the org server (writes ~/.manthana/manthana.toml + verifies)
manthana login --server https://manthana.acme.com --token <TOKEN> --actor bob@acme.com

manthana config          # sanity-check (token shown masked)
manthana sync --check    # confirm the server is reachable and the token is accepted

# 2. make it run by itself — capture + auto-sync at login (macOS launchd)
manthana service install  # logs: ~/Library/Logs/manthana-watch.log
```

That's it. `manthana watch` now starts at login and, every few seconds:
- ingests new/changed Claude Code sessions (free), and
- **auto-syncs released, redacted, non-personal** compactions to the org server.

> Linux: instead of `service install`, create a `systemd --user` unit running
> `manthana watch` (`systemctl --user enable --now manthana-watch`).

## Daily use — dashboard only

`manthana dashboard` (or have it open at <http://127.0.0.1:8765>). From there the
employee:
- sets a session **Work / Personal** (personal **never** leaves the laptop),
- **Compacts** sessions they want digested (this is the only token-spending step,
  and it's deliberate),
- **Releases** the compactions they're willing to share — released ones then
  auto-sync to the org on the next watch cycle.

Nothing is shared until the employee releases it, and secrets/PII are redacted on
the way out. The founder sees only released, redacted, k-anonymized data via the
server console (`/ui`).

## What runs where

| Piece | Where | Token cost |
|---|---|---|
| capture (auto) | `manthana watch` (launchd) | none |
| auto-sync of released | `manthana watch` | none |
| compaction | dashboard button / `manthana compact` | yes (your Claude) |
| review / release / mode | dashboard | none |

## Identity

The contributor identity (`actor`) comes from `--actor` at login (stored in
`[identity]`), else `MANTHANA_ACTOR`, else your git email, else the OS user.
Distinct actors are what let the org clear the k-anonymity floor.
