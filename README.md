# Manthana

**Local-first work intelligence for AI-coding teams.** Manthana captures every AI coding
session, distills each into a typed, cited **compaction** (what you set out to do, how, the
friction, the outcome, the cost), and turns that into two things:

- **for the engineer** — a queryable memory of your own work, real-time "you've done this
  before" surfacing, loop warnings, and lower token spend;
- **for the founder** — grounded, citation-backed visibility into what the team is
  doing, what's failing, and where money goes — **without spying on anyone**.

The trust contract is the point: **the employee owns the local store and raw transcripts; the
org only ever sees data the engineer explicitly releases, redacted and k-anonymized.**
Personal-mode sessions never leave the laptop.

> New to the code? See the **[technical report](docs/report/)** (diagram-rich tour) and the
> chronological architecture log in [`spec/manthana-architecture.md`](spec/manthana-architecture.md).

---

## Install (engineer laptop)

```bash
curl -LsSf https://github.com/Suraj-gameramp/manthana/releases/latest/download/install.sh | sh
```

This installs `uv` (if needed) and the `manthana` CLI. (Dev from a clone instead: `uv sync
--all-packages`, then `uv run manthana …`.)

---

## Admin quickstart — a secure team server in 5 steps

The fastest secure path (no domain, no certificates) uses **Tailscale** (a private VPN):

```bash
# 1. On every machine (admin + engineers): install Tailscale and join your tailnet.
#    https://tailscale.com/download  — then enable MagicDNS + HTTPS in the Tailscale admin console.
tailscale up

# 2. Install the server (admin machine only):
curl -LsSf https://github.com/Suraj-gameramp/manthana/releases/latest/download/install.sh | sh -s server

# 3. Run it — auto-generates secrets, exposes it on your tailnet with automatic HTTPS:
manthana-server serve --tailscale        # prints an admin token + your https://<machine>.<tailnet>.ts.net URL

# 4. Create a shareable invite — copy the printed `manthana setup …` line:
manthana-server enroll acme platform --open --server-url https://<machine>.<tailnet>.ts.net

# 5. Send that one line to each engineer; they run `manthana setup <it>` and they're on.
```

Prefer a real domain instead of Tailscale? See **[docs/deploy.md](docs/deploy.md)** for the
Caddy automatic-HTTPS path, or run `manthana-server init .` to drop the Docker/Caddy files.

---

## Onboarding a team (details)

The whole flow is **2 commands for the admin, 1 for each engineer.** Try it end-to-end locally
with zero infra: `./scripts/quickstart_demo.sh`.

### Admin — stand up + provision

```bash
# 1. start a server (auto-secrets, SQLite + in-memory, no Docker) — prints the admin token + URL.
#    add --tailscale to expose it securely to the team (see the quickstart above).
manthana-server serve

# 2. enroll the team — emits a `manthana setup <blob>` one-liner to send each engineer
manthana-server enroll acme platform --open --server-url https://manthana.acme.com
#   --open   = one shared invite (each engineer supplies their email)
#   --emails team.txt = a single-use, identity-bound invite per engineer
```

The team token is **never** put in Slack — the invite is a code the engineer redeems for a
token. Cross-engineer features (skills, org rollups) need **≥4 contributors**, so onboard the
team, not one person.

### Engineer — one command

```bash
manthana setup mia_…        # the one-liner from your admin (add --actor you@acme.com if it's an open invite)
```

That single command redeems the invite, connects, installs auto-capture at login (macOS / Linux / Windows),
runs a first capture, and prints a confirmation. Check anytime:

```bash
manthana doctor             # configured? · reachable · token accepted · daemon · data flowing
```

Full guide: **[docs/onboarding.md](docs/onboarding.md)**.

---

## Where the server lives (serving a real team, securely)

Onboarding is just "every laptop points at one `server_url`" (baked into the invite). The only
real decision is where that one server runs, with HTTPS in front (the team token is a bearer
credential — never send it unencrypted). Three paths, explained in plain English in
**[docs/deploy.md](docs/deploy.md)**:

| Path | Best for | How |
|---|---|---|
| **Tailscale / VPN** | fastest secure pilot (no domain, no certs) | `manthana-server quickstart` + `./scripts/tailscale_serve.sh` → `https://<machine>.<tailnet>.ts.net` |
| **Cloud + domain + TLS** | the productized path | quickstart (or Docker) behind **Caddy** (`deploy/Caddyfile` / `docker-compose.tls.yml`) → auto Let's Encrypt HTTPS |
| **Full Docker stack** | Postgres + object store at scale | `docker compose up -d` (see deploy.md) |

⚠️ Never expose the server publicly **without** HTTPS — `serve` warns you if you try.

---

## Daily use (engineer)

Work as normal; capture is automatic. From the **dashboard** (`manthana dashboard`,
<http://127.0.0.1:8765>) or the CLI:

```bash
manthana insights --since 7d                   # token-free rollups (projects, outcomes, cost)
manthana ask "what did I work on last week?"   # grounded, cited answer over your own sessions
manthana related <session-id>                  # prior work related to a session
```

- Set a session **Work / Personal** (personal **never** syncs).
- **Compact** sessions you want digested (the only token-spending step).
- **Release** what you're willing to share — released compactions auto-sync on the next watch
  cycle (or **hold** within the 10-minute grace window). Secrets/PII are redacted on the way out.

The founder sees only released, redacted, k-anonymized data via the console at `/ui`, with a
weekly **digest** and a **cost analyzer** (what sessions would cost on cheaper model tiers).

---

## Uninstall / rollback

Everything is cleanly removable — nothing is installed system-wide beyond a CLI and (optionally)
a login service.

```bash
# Engineer (client)
manthana service uninstall        # stop the auto-capture daemon (macOS/Linux/Windows)
uv tool uninstall manthana        # remove the CLI
rm -rf ~/.manthana                # remove the local store (your captured data)

# Admin (server)
tailscale serve --https=443 off   # stop sharing over the tailnet (if you used --tailscale)
uv tool uninstall manthana-server
rm -rf ~/.manthana-server         # server DB + generated secrets
# Docker deployments:  docker compose down -v     # also removes volumes (data + certs)
```

---

## Repository layout

```
schemas/     manthana-schemas      (Apache-2.0)  Pydantic models + JSON Schema mirror
collectors/  manthana-collectors   (Apache-2.0)  per-surface transcript adapters
skills/      manthana-skills       (Apache-2.0)  skill miner (shared by agent + server)
agent/       manthana              (Apache-2.0)  local agent + `manthana` CLI
server/      manthana-server       (AGPL-3.0)    org server + founder console
tests/       cross-package tests   ·  spec/  specification + architecture log  ·  docs/  guides
```

All packages share the PEP 420 namespace `manthana` but are separately distributable, so the
AGPL (server) / Apache (everything the engineer runs) split is real.

## Development

Requires [`uv`](https://docs.astral.sh/uv/) (Python 3.11+).

```bash
uv sync --all-packages          # install all members editable
uv run ruff check . && uv run pyright && uv run pytest     # the gate
uv sync --extra embeddings      # optional: bge-large embeddings for sharper retrieval/mining
```

## Licensing

Dual-licensed by component — see [`LICENSE`](LICENSE). The server is **AGPL-3.0-or-later**; all
client tooling is **Apache-2.0**. Portions derive from [ECC](https://github.com/affaan-m/ecc)
(MIT, © 2026 Affaan Mustafa); see [`NOTICE`](NOTICE).
