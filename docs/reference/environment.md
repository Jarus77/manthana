# Environment variables

Two namespaces. `MANTHANA_*` (no `SERVER`) configures the **agent** on an
engineer's laptop. `MANTHANA_SERVER_*` configures the **org server**.

A ready-to-edit file with every server variable lives at
[`../../.env.example`](../../.env.example) — `cp .env.example .env`, or run
`manthana-server init .` to have it written for you without a repo checkout.

Boolean variables accept `1`, `true`, or `yes`; anything else (including unset)
is false.

---

## Agent (engineer's laptop)

| Variable | Default | What it does |
|---|---|---|
| `MANTHANA_DATA_HOME` | `~/.manthana` | Root of your local store. The SQLite DB is `$MANTHANA_DATA_HOME/manthana.db`, the config `manthana.toml`. Accepts `~` and relative paths. |
| `MANTHANA_ACTOR` | git email → OS user | Your contributor identity. Normally set once by `manthana setup`; the env var wins over the config file. |
| `MANTHANA_SERVER_URL` | from `manthana.toml` | Org server URL. Overrides the `[server] url` in config. |
| `MANTHANA_TEAM_TOKEN` | from `manthana.toml` | Team token. Overrides the `[server] token` in config. |
| `MANTHANA_NO_UPDATE_NOTIFIER` | unset | Set to anything to silence the "a newer Manthana is available" stderr notice for this invocation. |
| `MANTHANA_REPO` | `Jarus77/manthana` | Which GitHub repo the update check and `install.sh` read. |

Everything else the agent reads lives in `~/.manthana/manthana.toml`:

```toml
[embeddings]
model = "BAAI/bge-large-en-v1.5"

[redaction]
secrets = true
pii = true

[server]
url = "https://manthana.acme.com"
token = "eyJhbGc…"

[identity]
actor = "you@acme.com"

[update]
notifier = false        # permanent opt-out from the update notice

[mode]
solo = true             # written by `manthana solo`
```

`manthana config` prints the resolved values with the token masked.

`[mode] solo` marks an install as having no org server **on purpose**. Its only
effect is on `manthana doctor`: the missing server stops counting as a critical
failure, so a healthy personal install exits 0 instead of 1. See
[Solo use](../solo/index.md).

The update notice never makes a network call on the command you ran — the check
happens in the `watch` daemon or in a detached child, and the result is shown on
a later invocation. It prints only to a real terminal, never in CI, never when
output is piped.

---

## Server

### Secrets — required in production

| Variable | Default | What it does |
|---|---|---|
| `MANTHANA_SERVER_JWT_SECRET` | *dev placeholder* | Signs every agent, founder, and engineer token. Use ≥32 random bytes. |
| `MANTHANA_SERVER_ADMIN_TOKEN` | *dev placeholder* | Gates the founder console and every admin endpoint. |

The server **refuses to start** with the shipped placeholders or with an empty
value — an empty token would authenticate anyone. If neither is set,
`manthana-server serve` generates a pair once and persists them to
`~/.manthana-server/server-secrets.toml` (mode `0600`); they are stable across
restarts, because regenerating them would invalidate every token already issued.

Never put secrets on a command line. Put them in `.env`.

### Storage

| Variable | Default | What it does |
|---|---|---|
| `MANTHANA_SERVER_DB_URL` | `sqlite:///./manthana-server.db` | SQLAlchemy URL. Production: `postgresql+psycopg://…` |
| `MANTHANA_SERVER_OBJECT_STORE` | `memory` | Where released raw transcripts live: `memory` or `s3`. **`memory` loses every raw transcript on restart** — never pair it with a Postgres DB. |
| `MANTHANA_SERVER_S3_BUCKET` | — | Required when `OBJECT_STORE=s3` |
| `MANTHANA_SERVER_S3_ENDPOINT_URL` | — | Set for MinIO or any non-AWS S3 |
| `MANTHANA_SERVER_S3_ACCESS_KEY` | — | Falls back to boto3's default credential chain |
| `MANTHANA_SERVER_S3_SECRET_KEY` | — | " |
| `MANTHANA_SERVER_MAX_RAW_BYTES` | `25000000` | Hard ceiling on one uploaded raw transcript (25 MB). Bounds memory on the privileged founder drill path. |
| `MANTHANA_SERVER_MAX_REQUEST_BYTES` | `30000000` | Whole-request `Content-Length` ceiling. Deliberately above `MAX_RAW_BYTES` so the raw endpoint's own cap stays the binding limit. |

### Privacy

| Variable | Default | What it does |
|---|---|---|
| `MANTHANA_SERVER_K_ANON` | `4` | Distinct-contributor floor for cross-engineer views. Must be ≥1. Set to `1` only for a genuinely single-person install. |
| `MANTHANA_SERVER_PRIVACY_MODE` | `k_anon` | Default posture for orgs with no override: `k_anon` (de-identified, floor-gated) or `open` (named, per-individual). Per-org overrides via `PUT /v1/admin/orgs/{id}/privacy` — prefer those on a multi-tenant server. |

See [Privacy & security model](privacy.md).

### Console, cookies, and the wiki client

| Variable | Default | What it does |
|---|---|---|
| `MANTHANA_SERVER_PUBLIC_URL` | `http://127.0.0.1:8000` | Public base URL of this deployment, no trailing slash. Used **only** to print shareable links; never for routing or auth, so a wrong value yields a bad link, not a security hole. |
| `MANTHANA_SERVER_COOKIE_SECURE` | `0` | Mark console cookies `Secure` (HTTPS-only). **Set to `1` on any public TLS deployment**; leave off for local HTTP or logins won't stick. |
| `MANTHANA_SERVER_RETIRE_HTML_WIKI` | `0` | When on, every `/ui/...` wiki page `303`s to the equivalent route in the Next.js client instead of rendering HTML. **Only enable this once the client is actually being served in front of this process** — the redirect targets (`/`, `/people/…`) belong to the client, so turning it on without one replaces a working wiki with a 404. See [self-hosting/web-client.md](../self-hosting/web-client.md). |
| `MANTHANA_SERVER_ENABLE_FOUNDER_MCP` | `0` | Mounts the founder MCP gateway. Off by default until verified against a live MCP client. |
| `MANTHANA_SERVER_MCP_ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver` | Comma-separated `Host` allowlist for the MCP endpoint's DNS-rebinding check. Must include your public domain. `*` disables the check. |

### The model provider

| Variable | Default | What it does |
|---|---|---|
| `MANTHANA_SERVER_LLM` | `mock` | `mock` · `anthropic` · `claude_cli` — see below |
| `MANTHANA_SERVER_CLAUDE_CLI` | `claude` | Which binary the `claude_cli` provider invokes |
| `MANTHANA_SERVER_LLM_MODEL` | `claude-sonnet-4-6` | Model for founder narratives and the weekly digest |
| `MANTHANA_SERVER_LLM_MAX_TOKENS` | `1024` | Narrative output cap (1..100000) |
| `ANTHROPIC_API_KEY` | — | Required when `MANTHANA_SERVER_LLM=anthropic` |
| `MANTHANA_SERVER_LLM_MONTHLY_CAP_USD` | `0.0` | Server-wide default monthly AI budget per org. **`0` = unlimited** (usage is still recorded). Per-org overrides live in the quota table. |

| Provider | What it is | Needs |
|---|---|---|
| `mock` (default) | Deterministic and offline. Returns `{}` / "insufficient data" — honest, and empty. | nothing |
| `anthropic` | The API. The normal choice for a real deployment. | the `manthana-server[llm]` extra + `ANTHROPIC_API_KEY` |
| `claude_cli` | **Bring your own model.** Shells out to a Claude CLI logged in as the user running the server, so a self-hoster spends the subscription they already have instead of buying a second key. | the binary on `PATH` **and a logged-in `$HOME`** |

> **`claude_cli` does not work in the container images.** It needs both the binary
> and a logged-in home directory, so it works when the server runs as a human's own
> user (`manthana-server serve` on a laptop) and not inside Docker or Kubernetes.
> That's why it is opt-in rather than an automatic fallback. If the binary is
> missing the server **logs loudly and falls back to the mock** — it does not crash,
> and your wiki stays honest but empty.

Cost for `claude_cli` is metered from what the CLI actually reports, not estimated
from a price table, so the usage figures reflect real subscription usage.

Every provider is fail-safe: transient errors are retried with backoff, and a
missing SDK, key, or binary falls back to the mock and logs rather than crashing
the server or taking it down at boot.

> **Hosted operators:** `manthana-server onboard-org` provisions each new customer
> org with an explicit **$100/month** per-org override. The `ServerConfig` default
> `llm_monthly_cap_usd` stays `0.0` — unlimited — because that is the *self-hoster's*
> value: someone paying their own model bill should never be throttled by a number
> we chose. The two are deliberately different settings.

### Enrichment — fills in the qualitative fields

Agents emit deterministic `pending` digests; this pass fills in the narrative
fields on the operator's key. **Off by default.**

| Variable | Default | What it does |
|---|---|---|
| `MANTHANA_SERVER_ENABLE_ENRICHMENT` | `0` | Turns the background pass on |
| `MANTHANA_SERVER_ENRICH_MODEL` | `claude-haiku-4-5` | Bulk structured summarization, not reasoning — hence the cheap tier |
| `MANTHANA_SERVER_ENRICH_MAX_TOKENS` | `2048` | Output cap (1..100000) |
| `MANTHANA_SERVER_ENRICH_INTERVAL` | `300` | Seconds between passes |
| `MANTHANA_SERVER_ENRICH_BATCH_PER_ORG` | `25` | Per-org ceiling for one pass, so one org's backlog can't starve other tenants |
| `MANTHANA_SERVER_ENRICH_MAX_BATCH` | `200` | Whole-pass ceiling across all orgs |
| `MANTHANA_SERVER_ENRICH_MAX_ATTEMPTS` | `5` | Attempts before a digest is abandoned |
| `MANTHANA_SERVER_ENRICH_MAX_AGE_DAYS` | `7` | Age at which a never-enrichable digest is abandoned |

A digest with neither a native summary nor an uploaded raw transcript **waits**
rather than burning a model call. `GET /v1/admin/enrichment?org_id=` shows the
backlog and the stuck ones; `POST /v1/admin/enrichment/retry?org_id=` un-abandons
the ones whose raw has since arrived.

### Consolidation — digests into typed wiki notes

Turns enriched digests into decisions, conventions, gotchas, and benchmarks via
one cheap adjudication call per session. **Off by default.** Requires enrichment
to be on — it only ever reads digests enrichment has already filled in.

| Variable | Default | What it does |
|---|---|---|
| `MANTHANA_SERVER_ENABLE_CONSOLIDATION` | `0` | Turns the background pass on |
| `MANTHANA_SERVER_CONSOLIDATE_MODEL` | `claude-haiku-4-5` | Bulk adjudication tier |
| `MANTHANA_SERVER_CONSOLIDATE_MAX_TOKENS` | `2048` | Output cap (1..100000) |
| `MANTHANA_SERVER_CONSOLIDATE_INTERVAL` | `300` | Seconds between passes |
| `MANTHANA_SERVER_CONSOLIDATE_BATCH_PER_ORG` | `25` | Per-org ceiling for one pass |
| `MANTHANA_SERVER_CONSOLIDATE_MAX_BATCH` | `200` | Whole-pass ceiling |
| `MANTHANA_SERVER_CONSOLIDATE_TOP_K` | `8` | How many live notes one adjudication sees (top-k by cosine, plus entity-overlap hits) |
| `MANTHANA_SERVER_CONSOLIDATE_NOTE_SCAN` | `500` | How many notes that retrieval scans |
| `MANTHANA_SERVER_CONSOLIDATE_MAX_ATTEMPTS` | `3` | Attempts before a compaction is abandoned |

### Project overviews — the living article per project

Writes one `project_overview` note per project describing what the project *is*.
A project slug is only ever a git directory name, so without this the wiki can
say nothing about a project beyond which org it belongs to. **Off by default.**
Cost is bounded by a hash of each project's contributing sessions: it regenerates
when the *work* changes, not on a timer, and never re-describes a project a human
has edited.

| Variable | Default | What it does |
|---|---|---|
| `MANTHANA_SERVER_ENABLE_PROJECT_OVERVIEW` | `0` | Turns the background pass on |
| `MANTHANA_SERVER_OVERVIEW_INTERVAL` | `3600` | Seconds between passes — a description changes over weeks |
| `MANTHANA_SERVER_OVERVIEW_MAX_PER_PASS` | `10` | Whole-pass ceiling across all orgs |
| `MANTHANA_SERVER_OVERVIEW_SESSION_LIMIT` | `40` | Sessions fed into one call |
| `MANTHANA_SERVER_OVERVIEW_MIN_SESSIONS` | `1` | Below this there is nothing to describe |
| `MANTHANA_SERVER_OVERVIEW_MAX_ATTEMPTS` | `3` | Attempts before giving up on a project |

### Org skill mining

Mining is quadratic in clustering plus one model call per cluster, so both bounds
are real limits, and both are **reported** to the founder — a run that hit a bound
says so rather than silently covering less than the founder assumes.

| Variable | Default | What it does |
|---|---|---|
| `MANTHANA_SERVER_MINE_WINDOW_DAYS` | `90` | Only compactions started within this window |
| `MANTHANA_SERVER_MINE_MAX_ITEMS` | `1000` | Newest-first cap on what one run clusters |

---

## Deploy-only variables

Not read by the application — used by the shipped Compose files.

| Variable | Default | Used by |
|---|---|---|
| `MANTHANA_VERSION` | `0.4.0` | `docker-compose.prod.yml` — the image tag to pull. Set it to the release you actually want, e.g. `MANTHANA_VERSION=0.6.3`. |
| `MANTHANA_DOMAIN` | required | `docker-compose.tls.yml` — the domain Caddy issues a certificate for |
| `MANTHANA_REPO`, `MANTHANA_VERSION` | `Jarus77/manthana`, `latest` | `install.sh` — which repo and release to install from |
| `MANTHANA_API_ORIGIN` | `http://127.0.0.1:8000` | `web/next.config.mjs` — where the client's **dev-mode** `/ui/*` rewrite points. Unused in production, where the reverse proxy does the routing. |

## Next

- [CLI reference](cli.md)
- [Self-hosting](../self-hosting/index.md)
