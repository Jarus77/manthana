# Provisioning an org, teams, and engineers

The whole flow is **two commands for you, one for each engineer.**

## The model

| Thing | What it is |
|---|---|
| **Org** | Your company. The unit of privacy, budget, and wiki. |
| **Team** | A group inside the org. Cross-engineer features (skill mining, rollups) are computed within a team. |
| **Actor** | One engineer's identity, usually their email. Distinct actors are what clear the k-anonymity floor. |
| **Invite** | A short code that redeems for a team token. Safe to share; worthless afterwards. |
| **Team token** | The engineer's bearer credential for pushing to your server. Never handed around — issued only on redemption. |

Orgs and teams are created implicitly by `enroll` and `onboard-org`, so you
rarely create them by hand.

## Path 1 — self-hosted (you run the server)

Run these on the server host; they read its database directly.

```bash
# 1. Start the server. Zero config for a pilot: it generates and persists
#    secrets to ~/.manthana-server, then serves SQLite + in-memory.
manthana-server serve --tailscale        # or: --host 0.0.0.0 behind Caddy

# 2. Provision the team and emit the invites.
manthana-server enroll acme platform --open \
  --server-url https://manthana.acme.com
```

That's it. `enroll` creates the org and team if they don't exist (it's
idempotent) and prints the line to send.

### Open invite vs bound invites

```bash
# One shared, multi-use invite — drop it in a Slack channel.
manthana-server enroll acme platform --open --server-url https://manthana.acme.com
# → manthana setup mia_eyJzZXJ2ZXIi…
#   Each engineer adds --actor their@email at setup.

# …or one single-use, identity-bound invite per person.
manthana-server enroll acme platform --emails team.txt --server-url https://manthana.acme.com
# → alice@acme.com → manthana setup mia_…
#   bob@acme.com   → manthana setup mia_…
```

`team.txt` is one email per line; `#` comments are ignored.

Bound invites are better when you care that identities are correct from the first
session — the actor comes from the invite and can't be typo'd. Open invites are
better when you're onboarding seven people at once and don't want to send seven
DMs. Both expire in 14 days by default (`--expires-days`).

**The team token is never in the invite.** The invite carries a server URL and a
code; the token is minted only when the engineer redeems it. That's why an invite
in Slack is fine and a token in Slack is not.

### Check who has actually onboarded

```bash
manthana-server invites acme
# mia-code  team=platform  who=alice@acme.com  redeemed        expires=…
# mia-code  team=platform  who=(open)          9998 use(s) left  expires=…
```

## Path 2 — hosted or remote (someone else runs the server)

`onboard-org` does everything over the admin HTTP API, so it runs from anywhere
the server URL is reachable — no database access, no SSH.

```bash
export MANTHANA_SERVER_ADMIN_TOKEN=…    # the operator's admin token

manthana-server onboard-org acme "Acme Inc" \
  --server-url https://api.example.com \
  --teams platform,growth \
  --open \
  --quota-usd 100
```

In one command it creates the org and teams, mints an invite per team, mints an
**org-scoped founder token** for your console, sets the AI budget, and prints a
paste-ready welcome block:

```
══ Welcome to Manthana — Acme Inc ══
Server: https://api.example.com

Engineers — each runs ONE command (invites expire in 14d):
  [team platform] manthana setup mia_…
  [team growth]   manthana setup mia_…

Founder console: https://api.example.com/ui
  sign-in token (founder-only, keep private): eyJhbGc…

AI budget: $100.00/month
```

An individual is just an org of one:

```bash
manthana-server onboard-org jane "Jane" --server-url https://api.example.com --emails jane.txt
```

## What each engineer does

One command, whichever path you took:

```bash
# Install once
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh

# Then the line you sent them
manthana setup mia_…                      # bound invite
manthana setup mia_… --actor you@acme.com # open invite
```

That redeems the invite, connects, installs the capture daemon at login, runs a
first capture, and prints a confirmation. Send them
[the engineer guide](../engineers/index.md) — it answers the data questions
before they ask you.

## Giving engineers wiki access

The team token lets an engineer's agent *push*. Reading the wiki in a browser is
a separate, narrower credential:

```bash
curl -X POST https://manthana.acme.com/v1/engineer-tokens \
  -H "Authorization: Bearer $FOUNDER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"org_id":"acme","actor":"alice@acme.com"}'
```

Or use the **Mint engineer token** control in the `/ui` console. An engineer
token grants the **wiki only** — read and teach — never the oversight surfaces.
It's deliberately founder-callable rather than admin-only, so you can onboard
your own team without routing every hire through whoever runs the server.

## Rotating and removing people

- **Rotate a token:** issue a fresh invite with `enroll` and have them re-run
  `manthana setup`. Tokens otherwise expire after 365 days.
- **Someone leaves:** their laptop stops syncing when they uninstall
  (`manthana service uninstall`). Their already-released digests remain — they are
  the org's record of work, the same as merged commits.
- **Remove their data:** `POST /v1/admin/purge` with a filter, dry-run first. See
  [Operating the server](operating.md#purging-data).

## Next

→ [Privacy posture & budgets](privacy-and-budgets.md) — and note that until you
enable the server-side passes, no wiki articles are written at all.
