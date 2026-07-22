# Operations & upgrades

Running a Manthana server after it's up. For the founder-facing side — budgets,
audits, backlogs — see [Operating the server](../founders/operating.md).

## Health

| Check | How |
|---|---|
| Liveness | `GET /healthz` |
| Readiness (DB ping) | `GET /readyz` |
| Everything | `manthana-server doctor` |

`doctor` exits non-zero on a critical failure, so it drops straight into a cron
job or a monitoring check. Compose and the k8s Deployment already probe
`/healthz` and `/readyz`.

Two checks worth understanding, both flagged only on Postgres deployments:

- **Object store persistent.** A Postgres database paired with the in-memory
  object store loses every raw transcript on restart. Almost always a
  misconfiguration — set `MANTHANA_SERVER_OBJECT_STORE=s3`.
- **Secure cookies.** Behind TLS, `MANTHANA_SERVER_COOKIE_SECURE=1`, or console
  logins misbehave.

## Backups

| What | Where | Why |
|---|---|---|
| Database | `pgdata` volume, or your managed Postgres | All org state: digests, notes, invites, audit, quotas |
| Object store | `miniodata` volume, or your S3 bucket | Released raw transcripts |
| Secrets | `.env`, or `~/.manthana-server/server-secrets.toml` | `jwt_secret` signs every token you've issued |
| TLS certificates | `caddy_data` volume | Avoids re-issuing on every restart |

**Back up the secrets.** Losing `jwt_secret` invalidates every engineer's agent
token and every founder login simultaneously, and there is no recovery except
re-onboarding everyone.

`docker compose down -v` removes the volumes — data and certificates together.
That is the correct command for a clean teardown and the wrong one for a restart.

## Restoring onto a fresh database

Engineers' laptops keep watermarks of what they've already pushed, so against a
fresh server their history would be skipped forever and simply be missing. Their
`manthana doctor` will start reporting raw transcripts rejected as unknown.

Tell them:

```bash
manthana resync --confirm
manthana sync
```

That clears the watermarks and re-uploads. It deletes nothing locally, and it
does not widen the sync gate — personal and unreleased work stays on the laptop.

## Upgrading

Agent and server versions move in **lockstep**; all packages ship at the same
version (currently **0.6.3**).

### The server

```bash
# Compose, from source
git pull && docker compose up -d --build

# Compose, pinned image
MANTHANA_VERSION=0.6.3 docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml pull
MANTHANA_VERSION=0.6.3 docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml up -d

# CLI install
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh -s server
manthana-server doctor
```

Database tables are created idempotently at startup, so a rolling upgrade needs
no manual migration step.

### The wiki client

It's a separate deployable. `docker compose up -d --build web`, or rebuild and
redeploy however you host it. Its API surface is small and versioned with the
server, so upgrade both together.

### Engineers

They re-run the installer:

```bash
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh
manthana version
```

> **Worth a broadcast message.** Before **2026-07-19** the installer silently
> no-opped when Manthana was already present — `uv tool install` printed "already
> installed" and exited 0 without upgrading. Anyone who installed before that
> date may still be pinned to the version they first installed, regardless of how
> many releases have gone out since. Ask your team to run the two lines above and
> report `manthana version`.

## Rotating credentials

| Credential | How | Blast radius |
|---|---|---|
| Admin token | new `MANTHANA_SERVER_ADMIN_TOKEN`, restart | Admin API and console sign-in only |
| JWT secret | new `MANTHANA_SERVER_JWT_SECRET`, restart | **Every** agent, founder, and engineer token — everyone re-onboards |
| One engineer's team token | issue a fresh invite with `enroll`; they re-run `manthana setup` | That engineer |

Team tokens otherwise expire after 365 days.

## Scaling notes

- The server process is **stateless** — scale horizontally behind a load
  balancer, with external Postgres and S3.
- Raw transcript uploads are capped at `MANTHANA_SERVER_MAX_RAW_BYTES` (25 MB),
  with a whole-request ceiling of `MANTHANA_SERVER_MAX_REQUEST_BYTES` (30 MB)
  slightly above it so the raw endpoint's own cap stays the binding limit. Raise
  both together or neither.
- The background LLM passes are bounded per-org and per-pass so one tenant's
  backlog cannot starve another. Tune with `ENRICH_BATCH_PER_ORG` /
  `ENRICH_MAX_BATCH` and their consolidation twins.
- Org skill mining is quadratic in clustering plus one model call per cluster, so
  `MINE_WINDOW_DAYS` (90) and `MINE_MAX_ITEMS` (1000) exist to keep a run finite.
  Both are reported to the founder — a run that hit a bound says so, rather than
  silently covering less than the founder assumes.

## Uninstalling

```bash
tailscale serve --https=443 off      # if you used --tailscale
uv tool uninstall manthana-server
rm -rf ~/.manthana-server            # DB + generated secrets

docker compose down -v               # Docker: also removes volumes
```

## Next

→ [The wiki client](web-client.md)
→ [Environment variables](../reference/environment.md)
→ [Founder operations](../founders/operating.md)
