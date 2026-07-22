# CLI reference

Two binaries. `manthana` is the local agent every engineer runs (Apache-2.0).
`manthana-server` is the org server an admin runs (AGPL-3.0). Everything below is
current for **0.6.3**; `manthana version` prints yours.

Every command supports `--help`.

---

# `manthana` — the local agent

## Getting connected

### `manthana setup [INVITE]`

**The one command an engineer runs.** Redeems the `mia_…` invite for a team
token, writes `~/.manthana/manthana.toml` (mode `0600`), installs the capture
daemon so it runs at login, performs a first capture, and prints a confirmation.
If you omit the invite it prompts for one.

| Flag | Default | Meaning |
|---|---|---|
| `--actor <email>` | auto-detected | Your identity. Needed only for an **open** (shared) invite; a bound invite already carries it. |
| `--no-service` | service installed | Skip the login daemon — you'll run `manthana watch` yourself. |

```bash
manthana setup mia_eyJzZXJ2ZXIi…              # bound invite
manthana setup mia_… --actor you@acme.com     # open team invite
```

### `manthana solo`

**The one command for a user with no org.** The counterpart to `setup`: marks the
install solo, captures your existing history, compacts what has settled, installs
the capture daemon, and serves your personal wiki.

| Flag | Default | Meaning |
|---|---|---|
| `--no-service` | daemon installed | Skip the login daemon |
| `--no-dashboard` | wiki served | Set up and exit instead of serving |
| `--port <n>` | `8765` | Port for the personal wiki |

Refuses with exit 1 — changing nothing — if the install is already connected to
an org server. Silently flipping a team member to solo would stop their work
reaching the org wiki with no visible symptom. See [Solo use](../solo/index.md).

### `manthana login --server <url> --token <jwt>`

The manual alternative to `setup`, for when you were handed a raw team token
instead of an invite (e.g. from `manthana-server onboard`). Writes the same
config file and verifies reachability. Does **not** install the daemon — run
`manthana service install` after.

| Flag | Default | Meaning |
|---|---|---|
| `--server <url>` | required | Org server base URL |
| `--token <jwt>` | required | Team token |
| `--actor <email>` | auto-detected | Contributor identity |
| `--no-optimize` | optimize on | Skip wiring Claude Code through `headroom` (token reduction) if it's installed |

### `manthana config`

Prints the resolved config with the token masked: config path, server URL, actor,
and whether secret/PII redaction are on. Run this when you want to know what this
laptop believes about itself.

### `manthana datahome`

Prints the resolved `MANTHANA_DATA_HOME` and the SQLite path. Useful before a
backup or a wipe.

### `manthana version`

Prints the installed version.

## Running

### `manthana watch`

The daemon. Ingests new and changed transcripts on a loop, compacts settled
sessions, auto-releases past the grace window, and syncs. `manthana setup`
installs this to run at login, so most engineers never type it.

| Flag | Default | Meaning |
|---|---|---|
| `--interval <s>` | `5.0` | Seconds between polls of `~/.claude/projects` |
| `--settle-min <m>` | **`30.0`** | Minutes of transcript quiet before a session counts as finished and gets compacted |
| `--release-min <m>` | **`10.0`** | Minutes a compaction sits before auto-releasing |
| `--no-auto-compact` | compacting | Capture only; compact by hand |
| `--summarized-only` | off | Only compact sessions the coding surface already summarized |
| `--max-per-cycle <n>` | `5` | Compactions per cycle, so a backlog doesn't stall the loop |
| `--no-auto-release` | releasing | Nothing releases without an explicit `manthana release` |
| `--no-sync` | syncing | Never push, even with a server configured |
| `--no-sync-raw` | raw synced | Push digests but not the redacted raw transcripts |

The two windows do different jobs — see [How Manthana works](architecture.md#the-two-windows).

### `manthana service <install|uninstall|status>`

Manages the login service that runs `manthana watch --interval 5`:
macOS `launchd`, Linux `systemd --user`, Windows Scheduled Task.

```bash
manthana service status
manthana service uninstall    # stop capture entirely
```

macOS logs land in `~/Library/Logs/manthana-watch.log`; Linux in
`journalctl --user -u manthana-watch.service`.

### `manthana capture`

One-shot ingest of all local Claude Code and Codex transcripts. Safe to re-run;
it's incremental.

### `manthana dashboard [--host 127.0.0.1] [--port 8765]`

Serves your local dashboard: sessions, compactions, cost, topics, skills, the
action audit, and the buttons for Work/Personal, compact, hold, and release.
Binds loopback by default — it is your data, not a service.

## Your own work

### `manthana sessions [--limit 20]`

Lists captured sessions, most recent first, with mode, surface, project, and turn
count. This is where you get a session id.

### `manthana insights [--since 7d]`

Token-free rollups: session and compaction counts, estimated API-equivalent cost,
work by project, by outcome, recent friction, and any loop warnings. `--since`
takes `7d`, `2w`, `12h`, or an ISO date.

### `manthana ask "<question>"`

A grounded, cited answer over **your own** compactions. Uses your installed
`claude` CLI, falling back to `codex` — **no API key required**, and nothing
leaves your machine beyond what your coding CLI already sends. If neither CLI is
present the answer degrades gracefully rather than erroring.

| Flag | Meaning |
|---|---|
| `--source full` | Restrict to full compactions, excluding the cheaper summary-derived ones |

### `manthana related <session-id>`

Your most relevant *prior* compactions for a session, scored with local
embeddings. This is the "you've solved this before" surface.

### `manthana mine-skills`

Clusters recurring patterns in your own compactions into proposed `SKILL.md`
drafts. Deterministic and offline by default.

| Flag | Default | Meaning |
|---|---|---|
| `--min-sessions <n>` | `3` | Minimum sessions before a cluster becomes a proposal |
| `--threshold <f>` | `0.75` | Cosine cohesion; lower (e.g. `0.6`) clusters more loosely |
| `--write` | off | Actually write drafts to `~/.claude/skills/personal/` |

### `manthana mcp`

Serves Manthana's read-only query tools (`insights`, `ask`, `topics`, `thread`,
`drill_raw`) to Claude Code over MCP on stdio, scoped to *your* local data. Needs
the optional MCP extra; the command tells you if it's missing.

### `manthana retag`

Re-runs the auto-tag action over every session. Worth doing after a version
upgrade that changed tagging.

## Deciding what to share

### `manthana mode <session-id> <work|personal>`

Sets a session's mode. **Personal-mode sessions never sync** — not on
auto-release, not on `manthana sync`, not on `manthana resync`.

### `manthana compact [SESSION_ID]`

Compacts one session, or every pending Work session if you omit the id.
Deterministic and local — it spends no tokens. The qualitative fields are filled
in server-side after the digest syncs.

### `manthana release <compaction-id>`

Marks a compaction released, making it eligible to sync. Only needed if you run
with `--no-auto-release`, or if you held something and changed your mind.

### `manthana sync [--raw] [--check]`

Pushes released, non-personal compactions to the org server.

| Flag | Meaning |
|---|---|
| `--raw` | Also upload the redacted raw transcripts |
| `--check` | Verify only: is the server reachable and is the token accepted? No push. |

### `manthana resync [--confirm]`

Clears this laptop's "already sent" watermarks so the next `sync` re-uploads
everything. Use it after your org's server was wiped or re-onboarded, when your
history would otherwise be permanently missing from the server.

Dry run unless `--confirm`. It **deletes nothing locally** and it does **not**
widen the sync gate: personal and unreleased work stays put.

```bash
manthana resync              # see what would happen
manthana resync --confirm && manthana sync
```

### `manthana purge [filters] [--confirm]`

Deletes **local** compactions matching a filter. Dry run unless `--confirm`, and
it refuses to run with no filter at all.

| Flag | Meaning |
|---|---|
| `--self-generated` | Manthana's own compaction sessions (historical recursion junk) |
| `--structural-junk` | Sessions that *are* a compaction call: no files, no project, abandoned |
| `--source <pending\|full\|claude_summary>` | Only this digest source |
| `--contains <text>` | Only digests whose text contains this substring |
| `--confirm` | Actually delete |

Sessions and turns survive, so `manthana compact` can re-derive a digest you
purged by mistake.

### `manthana doctor`

The first thing to run when something looks wrong. Checks configuration, server
reachability, token acceptance, server DB readiness, whether your agent version
has drifted behind your org server's, whether a model CLI is available for `ask`,
whether the capture daemon is installed (macOS), your local data counts and last
sync time, and whether any raw transcripts are stuck. Exits non-zero on a
critical failure, so it works in a setup script.

On a **solo** install (`[mode] solo = true`, set by `manthana solo`) the header
reads `Manthana doctor (solo — no org server)`, the missing server stops being a
critical failure, and a healthy personal install exits 0.

### `manthana optimize <status|setup|proxy|mcp|stats|tune>`

Optional. Runs Claude Code through `headroom` for context compression. Requires
`pip install "headroom-ai[proxy,mcp]"`; `manthana optimize status` tells you if
it's there. `--port` (default `8787`) applies to the `proxy` action.

---

# `manthana-server` — the org server

## Running the server

### `manthana-server serve`

Runs the org server. Zero-config for a pilot: when the `MANTHANA_SERVER_*`
secrets are not set it generates them once and persists them to
`~/.manthana-server/server-secrets.toml` (mode `0600`), on SQLite with an
in-memory object store. When the env vars *are* set it honours them.

| Flag | Default | Meaning |
|---|---|---|
| `--host <addr>` | `127.0.0.1` | Bind address. `0.0.0.0` to serve other machines — **only behind TLS** |
| `--port <n>` | `8000` | Port |
| `--public-url <url>` | printed URL | The HTTPS URL engineers actually reach, when something terminates TLS in front |
| `--k-anon <n>` | env, else `4` | k-anonymity floor for this process |
| `--data <dir>` | `~/.manthana-server` | Data dir for the SQLite DB and persisted secrets |
| `--tailscale` | off | `tailscale serve` in front of loopback: automatic HTTPS on your tailnet, no domain, no certs |

It prints the admin token, the data dir, the console URL, a warning if you bound
a public address without HTTPS, a warning if the k-anon floor is below 4, and the
exact `enroll` command to run next.

### `manthana-server quickstart`

An alias for `serve` with the same flags. Kept because older docs and scripts
reference it; new work should say `serve`.

### `manthana-server init [DIRECTORY]`

Writes the deploy templates — `Caddyfile`, `docker-compose.yml`,
`docker-compose.tls.yml`, `.env.example` — into a directory (default `.`), so you
never have to clone the repo to stand up a Docker or TLS deployment.

### `manthana-server doctor [--data <dir>]`

Health check for the server: secrets valid, database reachable, object store
constructible, LLM provider and key, k-anon floor, and the AI budget default. On
a Postgres deployment it additionally flags the two configurations that lose data
or logins silently — an in-memory object store, and non-secure cookies behind
TLS. Prints org/actor/compaction/invite counts. Exits non-zero on a critical
failure.

## Provisioning (self-hosted)

These read the server's own database directly, so run them on the server host.
`enroll`, `invites`, `init`, `doctor`, and `serve` all fall back to the persisted
secrets in `--data`; the rest require the `MANTHANA_SERVER_*` env vars to be set.

### `manthana-server enroll <ORG_ID> <TEAM_ID> --server-url <url>`

**The command that onboards a team.** Creates the org and team if needed
(idempotent) and emits a `manthana setup <blob>` one-liner per engineer. The team
token is never in the invite — it is issued only when the engineer redeems the
code, which is why an invite is safe to put in Slack and a token is not.

| Flag | Default | Meaning |
|---|---|---|
| `--server-url <url>` | required | The public URL engineers redeem against. Baked into the invite. |
| `--open` | off | One shared, multi-use invite. Each engineer supplies their own email at setup. |
| `--emails <file>` | — | A file of emails, one per line (`#` comments allowed) → one single-use, identity-bound invite each |
| `--org-name`, `--team-name` | the ids | Display names |
| `--expires-days <n>` | `14` | Invite lifetime |
| `--data <dir>` | `~/.manthana-server` | Data dir |

You must pass exactly one of `--open` or `--emails`.

### `manthana-server invites <ORG_ID> [--data <dir>]`

Lists an org's invites: code, team, who it's bound to, uses left or redeemed, and
expiry. This is how you tell whether an engineer has actually onboarded.

### `manthana-server onboard <ORG_ID> <ORG_NAME> <TEAM_ID> <TEAM_NAME> <ACTOR>`

The older per-engineer path: ensures the org and team exist and prints a raw team
token (valid 365 days) to hand over for `manthana login`. Prefer `enroll` — it
avoids passing a bearer token around at all. Requires the env secrets.

### `manthana-server create-org <ORG_ID> <NAME>` · `create-team <TEAM_ID> <ORG_ID> <NAME>` · `token <ORG_ID> <TEAM_ID> <ACTOR>`

The primitives `onboard` composes. Requires the env secrets.

## Provisioning (hosted / remote)

These talk to a running server over the **admin HTTP API**, so they work from
anywhere the server URL is reachable — no database access needed. All accept
`--admin-token`, or read `MANTHANA_SERVER_ADMIN_TOKEN` from the environment.

### `manthana-server onboard-org <ORG_ID> <ORG_NAME> --server-url <url>`

Onboards a whole customer org in one command: creates the org and team(s), mints
engineer invites, mints an org-scoped founder console token, sets the AI budget,
and prints a paste-ready welcome block to email.

| Flag | Default | Meaning |
|---|---|---|
| `--server-url <url>` | required | The hosted server's HTTPS URL |
| `--admin-token <tok>` | `$MANTHANA_SERVER_ADMIN_TOKEN` | Operator admin token |
| `--teams <a,b>` | `core` | Comma-separated team ids |
| `--open` | off | One shared invite per team |
| `--emails <file>` | — | Bound invites, one per email; they land on the first team |
| `--quota-usd <n>` | `100.0` | Monthly AI budget for this org. `0` = unlimited, `-1` = server default |
| `--expires-days <n>` | `14` | Invite lifetime |

An individual is just an org of one:
`manthana-server onboard-org jane "Jane" --server-url … --emails jane.txt`.

### `manthana-server usage <ORG_ID> --server-url <url>`

An org's month-by-month server-side AI spend and its effective cap, showing
whether the cap is an org override or the server default. The first thing to
check when the wiki stops filling in.

### `manthana-server set-quota <ORG_ID> <MONTHLY_CAP_USD> --server-url <url>`

Sets an org's monthly AI budget. `0` means unlimited.

## Reading the org

### `manthana-server digest <ORG_ID> [--since <date>] [--until <date>]`

Prints the founder weekly digest for an org (last 7 days by default): the
narrative sections with their citations, plus an explicit list of what was
omitted because it did not clear the k-anonymity floor. Requires the env secrets.

### `manthana-server router-analysis <ORG_ID>`

Estimates what the org's sessions would have cost on cheaper model tiers —
current vs projected spend, the saving, and the ten biggest individual
opportunities. Requires the env secrets.

---

## Admin HTTP API (the bits with no CLI equivalent)

All require the `X-Admin-Token` header unless noted.

| Endpoint | What it does |
|---|---|
| `GET /healthz` · `GET /readyz` | Liveness · DB ping. No auth. `/healthz` also returns `server_version` and `latest_agent_version` — the agent and server ship in lockstep, so the deployed server's version is what an engineer's agent should converge on. That's the channel `manthana doctor` and the update notice use. |
| `GET /docs` | OpenAPI browser |
| `POST /v1/enroll` | Invite redemption. Deliberately unauthenticated — the invite code *is* the credential — so it must sit behind HTTPS. |
| `GET /v1/admin/usage?org_id=` | Spend by month **and by purpose** (`enrich`, `consolidate`, …), plus `spent_usd` for the current month, the cap, and a plain `quota_blocked` boolean. An exhausted cap has no other symptom — enrichment just stops — so this flag is how you find it. |
| `PUT /v1/admin/orgs/{org_id}/quota` | Set the monthly cap |
| `PUT /v1/admin/orgs/{org_id}/privacy` | Set the org's `privacy_mode` (`open` / `k_anon`) |
| `GET /v1/admin/enrichment?org_id=` | Backlog size, whether enrichment is enabled, and the digests that are stuck or abandoned |
| `POST /v1/admin/enrichment/run?org_id=` | Run one bounded enrichment pass now instead of waiting for the interval |
| `POST /v1/admin/enrichment/retry?org_id=` | **Un-abandon** digests that gave up waiting for their raw transcript. By default only ones that could actually enrich now are reset. `limit` (default 200, max 500), `include_without_input=true` to reset the rest too. |
| `GET /v1/admin/consolidation?org_id=` · `POST /v1/admin/consolidation/run?org_id=` | The same pair for the consolidation pass |
| `POST /v1/admin/mine-skills` | Run org skill mining |
| `GET /v1/admin/router-analysis?org_id=` · `GET /v1/admin/digest?org_id=` | HTTP twins of the CLI commands |
| `POST /v1/admin/founder-tokens` | Mint an org-scoped founder token |
| `POST /v1/engineer-tokens` | Mint a wiki login for one named engineer. Founder-callable, not admin-only, and grants the **wiki only** — read and teach, never the oversight surfaces. |
| `GET /v1/admin/audit?org_id=` | Every founder query and drill-down |
| `POST /v1/admin/purge` | Purge an org's compactions. Dry run unless `confirm: true`, refuses an unfiltered request, always audited. |
| `GET /v1/admin/purge-audit?org_id=` | Purge history, dry runs included |

Founder-token endpoints (`/v1/founder/query`, `/ask`, `/topics`, `/thread`,
`/drill`, `/digest`, `/audit`) are scoped to the caller's own org.

## Next

- [Environment variables](environment.md)
- [Troubleshooting](../troubleshooting.md)
