# Self-hosting the Manthana server

Onboarding a team is just "every laptop points at **one** server URL" — that URL
is baked into the invite. The only real decision is **where that one server runs**,
with HTTPS in front of it.

That last part is not optional. The team token is a **bearer credential**:
whoever holds it can push as that engineer. It must never travel unencrypted.
`manthana-server serve` prints a warning if you bind a public address without TLS.

## Jargon, once

- **Loopback / `127.0.0.1`** — "this machine only". Invisible to every other
  laptop. The default.
- **`0.0.0.0`** — "accept connections from other machines". Needed to serve a
  team, safe only behind HTTPS.
- **Domain / DNS** — a name like `manthana.acme.com`. You point an "A record" at
  your server's IP at your registrar.
- **TLS / HTTPS** — encryption on the wire.
- **Reverse proxy** — a front door that terminates HTTPS and forwards to your
  app. **Caddy** fetches and renews a free Let's Encrypt certificate for you with
  almost no config.
- **Tailscale** — a private VPN giving each machine a stable `.ts.net` name with
  HTTPS built in. No domain, no open ports.

## Pick a path

| Path | Best for | HTTPS from | Data store |
|---|---|---|---|
| **A. Tailscale** | fastest secure pilot | Tailscale | SQLite + in-memory |
| **B. Domain + Caddy, no Docker** | a small real deployment | Let's Encrypt | SQLite + in-memory |
| **C. Full Docker stack** | the productized path | Let's Encrypt | Postgres + MinIO/S3 |
| **D. Kubernetes** | you already run k8s | your ingress | external Postgres + S3 |

Paths A and B store raw transcripts **in memory**, which is fine for a pilot and
wrong for anything you care about — they vanish on restart. Move to C or D before
you rely on them.

## Install the server

No repo checkout needed:

```bash
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh -s server
```

Then `manthana-server init .` writes the deploy files (`Caddyfile`,
`docker-compose.yml`, `docker-compose.tls.yml`, `.env.example`) into the current
directory.

---

## Path A — Tailscale

Everyone installs Tailscale (`tailscale up`) and joins your tailnet, with MagicDNS
and HTTPS enabled in the Tailscale admin console. Then:

```bash
manthana-server serve --tailscale
# → runs `tailscale serve` in front of loopback and prints
#   https://<machine>.<tailnet>.ts.net

manthana-server enroll acme platform --open \
  --server-url https://<machine>.<tailnet>.ts.net
```

Nothing is exposed to the public internet, and there is no certificate to manage.
Stop sharing with `tailscale serve --https=443 off`.

---

## Path B — your domain, Caddy, no Docker

Point `manthana.acme.com`'s DNS A record at the VM and open ports 80 and 443.

```bash
manthana-server init .                       # writes ./Caddyfile
# edit <your-domain> in ./Caddyfile

manthana-server serve --public-url https://manthana.acme.com &   # 127.0.0.1:8000
caddy run --config ./Caddyfile

manthana-server enroll acme platform --open --server-url https://manthana.acme.com
```

`--public-url` doesn't route anything — it's what the server prints in shareable
links, so a wrong value gives you a bad link, never a security hole.

Set `MANTHANA_SERVER_COOKIE_SECURE=1` once you're behind TLS, or console logins
won't stick correctly.

---

## Path C — the full Docker stack

Server + Postgres + MinIO (S3) + bucket creation + the wiki client, one command.

```bash
manthana-server init .        # or clone the repo
cp .env.example .env          # then fill it in — see "Secrets" below
docker compose up -d          # builds server + web, starts pg + minio + bucket
docker compose ps             # server should become healthy (/readyz)
```

| Service | Port | What |
|---|---|---|
| `server` | `8000` | API, founder console at `/ui` |
| `web` | `3000` | The Next.js wiki client |
| `postgres` | `5433` (host) | pgvector-enabled Postgres |
| `minio` | `9000` / `9001` | S3-compatible object store; console at `:9001` (`manthana` / `manthana-secret`) |

The server container reaches Postgres and MinIO by service name
(`postgres:5432`, `minio:9000`); Compose wires that automatically and it
overrides whatever your `.env` says for DB and object store. Host ports are for
your machine. Tables are created on startup, idempotently.

**The `web` service needs a reverse proxy in front of it.** Hitting
`localhost:3000` directly works for a look around, but every API call it makes
will 404 — the client and the server must share one origin. That's what
[`deploy/Caddyfile`](../../deploy/Caddyfile) is for, and why it routes by path.
Read [the wiki client](web-client.md) before you deploy this; it's the piece most
easily got wrong.

### Deploy a pinned release instead of building

```bash
MANTHANA_VERSION=0.6.3 docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml pull
MANTHANA_VERSION=0.6.3 docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml up -d
```

The overlay swaps the server's source build for the published image and inherits
Postgres and MinIO from the base file. Requires Docker Compose v2.24+.

> **Running a fork or a mirror?** Override `MANTHANA_IMAGE` rather than editing
> the Compose file: `MANTHANA_IMAGE=ghcr.io/your-org/manthana-server`. The default
> is `ghcr.io/jarus77/manthana-server`, which is what
> `.github/workflows/publish-image.yml` actually pushes. Always set
> `MANTHANA_VERSION` explicitly for anything real — the default tracks the current
> release and will move under you.

### Add HTTPS

```bash
MANTHANA_DOMAIN=manthana.acme.com \
  docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d
```

That overlay runs Caddy on 80/443, fetches and renews a Let's Encrypt certificate
for `$MANTHANA_DOMAIN`, and proxies to the server container. Its certificate
lives in the `caddy_data` volume, so don't delete it casually.

Note: the TLS overlay's one-line `caddy reverse-proxy` sends **everything** to
`server:8000`. If you're also running the `web` client, use
[`deploy/Caddyfile`](../../deploy/Caddyfile) instead — it does the path routing
both services need. See [the wiki client](web-client.md).

---

## Path D — Kubernetes

Manifests in [`deploy/k8s/`](../../deploy/k8s/). The server is stateless;
**Postgres and S3 are external or managed** — point the ConfigMap at them.

```bash
kubectl apply -f deploy/k8s/configmap.yaml
kubectl create secret generic manthana-server-secrets \
  --from-literal=MANTHANA_SERVER_JWT_SECRET="$(openssl rand -hex 32)" \
  --from-literal=MANTHANA_SERVER_ADMIN_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=ANTHROPIC_API_KEY="sk-ant-..."       # if LLM=anthropic
kubectl apply -f deploy/k8s/deployment.yaml -f deploy/k8s/service.yaml
```

The Deployment runs non-root (uid 10001, capabilities dropped) with `/healthz`
liveness and `/readyz` readiness probes. Put an Ingress with TLS in front of the
Service. `deploy/k8s/secret.example.yaml` is a template — prefer the
`kubectl create secret` form above so secrets never touch a file. Update the
image tag in `deployment.yaml`; it is pinned to an older release.

---

## Secrets

The server **refuses to start** with the shipped dev placeholders or an empty
value. Put real ones in `.env` and never on a command line — command lines leak
into shell history, process lists, and logs.

```bash
MANTHANA_SERVER_JWT_SECRET="$(openssl rand -hex 32)"
MANTHANA_SERVER_ADMIN_TOKEN="$(openssl rand -hex 24)"
```

| Variable | Why it matters |
|---|---|
| `MANTHANA_SERVER_JWT_SECRET` | Signs every agent, founder, and engineer token. Rotating it invalidates all of them at once. |
| `MANTHANA_SERVER_ADMIN_TOKEN` | Gates the founder console and every admin endpoint. Safe to rotate independently. |
| `ANTHROPIC_API_KEY` | Only needed with `MANTHANA_SERVER_LLM=anthropic` |

If you don't set the first two, `manthana-server serve` generates them once and
persists them to `~/.manthana-server/server-secrets.toml` (mode `0600`) — stable
across restarts, because regenerating would invalidate every issued token. Back
that file up.

Every variable, with defaults, is in
[Environment variables](../reference/environment.md).

## Before you call it done

```bash
manthana-server doctor
```

Checks secrets, database, object store, LLM provider and key, k-anon floor, and
the budget default. On a Postgres deployment it also flags the two
misconfigurations that lose things silently: an in-memory object store (raw
transcripts vanish on restart) and non-secure cookies behind TLS (logins don't
stick).

Then, deliberately, decide about the LLM passes — **all three are off by
default** and the server writes no wiki articles until you turn them on. See
[Privacy posture & budgets](../founders/privacy-and-budgets.md).

> **A note on `MANTHANA_SERVER_LLM=claude_cli`.** It lets the server use a Claude
> CLI you're already logged into instead of an API key, which is excellent for a
> server you run as yourself on a laptop or a VM — and it **does not work in the
> published container images**, which have neither the binary nor a logged-in
> `$HOME`. For Docker or Kubernetes, use `anthropic`. If you misconfigure it the
> server logs loudly and falls back to the mock rather than crashing, so the
> symptom is an empty wiki, not an outage.

## Next

→ [The wiki client](web-client.md) — the actual UI, and the same-origin constraint
→ [Operations & upgrades](operations.md)
→ [Environment variables](../reference/environment.md)
