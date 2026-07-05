# Deploying the Manthana org server (admin guide)

The founder/admin self-hosts one server. Engineers' agents sync **released,
redacted** compactions to it; the founder uses the web console at `/ui`. This is
the AGPL `manthana-server` (Postgres + S3/MinIO).

## 0. Serving a real team — where the server lives (+ a plain-English glossary)

**Install the server (no clone needed).** Either install the CLI or run the container:

```bash
# CLI — installs the `manthana-server` command
curl -LsSf https://github.com/Suraj-gameramp/manthana/releases/latest/download/install.sh | sh -s server

# …or pure Docker (no install at all)
docker run -p 8000:8000 \
  -e MANTHANA_SERVER_JWT_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))') \
  -e MANTHANA_SERVER_ADMIN_TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(24))') \
  ghcr.io/suraj-gameramp/manthana-server:0.4.0
```

Then run `manthana-server serve` (auto-generates + persists secrets when the env vars aren't set)
and `manthana-server init .` to drop the deploy files (Caddyfile, compose, `.env.example`) locally
— again, no repo checkout.

---

Onboarding a whole team is just "every laptop points at **one** `server_url`" (that URL is
baked into the `manthana setup` invite). The only real decision is **where that one server
lives so 7 engineers + a founder can all reach it** — and making sure the connection is
encrypted. Some jargon first:

- **Loopback / `127.0.0.1`** — "this machine only". A server bound here is invisible to any
  other laptop. `manthana-server quickstart` defaults to loopback (a local demo).
- **`0.0.0.0`** — "accept connections from other machines too". Needed to serve a team, but
  only safe *behind HTTPS* (see below).
- **Domain / DNS** — a human name like `manthana.acme.com`. DNS is the phone book that maps
  that name to your server's IP address (you set an "A record" at your domain registrar).
- **TLS / HTTPS** — encryption on the wire (the padlock in a browser). Manthana's team token is
  a *bearer credential* (whoever holds it is trusted), so it must **never** travel unencrypted.
  HTTPS = HTTP + TLS.
- **TLS certificate** — the proof of identity HTTPS needs. **Let's Encrypt** issues them for
  free, automatically.
- **Reverse proxy** — a small front-door program that terminates HTTPS and forwards requests to
  your app. **Caddy** is one that fetches + renews the Let's Encrypt certificate for you with
  near-zero config.
- **Tailscale** — a private network (VPN) that securely connects your machines directly, giving
  each a stable `…​.ts.net` name with HTTPS built in — no domain, no open firewall ports.

Pick one path:

### Path A — Cloud host + your domain + HTTPS (most "productized")
The server runs on a small cloud VM; **Caddy** sits in front and auto-provisions HTTPS for your
domain. Two flavours:

- **Zero-infra (serve + Caddy):** point `manthana.acme.com`'s DNS at the VM, open ports 80/443,
  then:
  ```bash
  manthana-server init .                                              # writes Caddyfile, compose, .env
  # edit <your-domain> in ./Caddyfile, then:
  manthana-server serve --public-url https://manthana.acme.com &      # server on 127.0.0.1:8000
  caddy run --config ./Caddyfile
  manthana-server enroll acme platform --open --server-url https://manthana.acme.com
  ```
- **Full stack (Docker + Caddy overlay):**
  ```bash
  manthana-server init .                                              # writes docker-compose*.yml + .env
  # fill .env (secrets — see its header), then:
  MANTHANA_DOMAIN=manthana.acme.com \
    docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d
  ```

### Path B — Tailscale / VPN (fastest secure path, no domain)
If everyone installs Tailscale (`tailscale up`, with MagicDNS + HTTPS enabled in the admin
console), one command exposes the server on the tailnet with automatic HTTPS — no domain, no
certs, no public exposure:
```bash
manthana-server serve --tailscale        # → prints https://<machine>.<tailnet>.ts.net
manthana-server enroll acme platform --open --server-url https://<machine>.<tailnet>.ts.net
```

> ⚠️ Do **not** expose `serve --host 0.0.0.0` to the internet **without** TLS in front —
> tokens would travel in plaintext. `serve` prints a warning if you try. Caddy or Tailscale
> provides that TLS layer. (The `/v1/enroll` redemption endpoint is intentionally unauthenticated
> — the invite code *is* the credential, single-use + expiring — so it must sit behind HTTPS;
> rate-limiting is deferred as pilot scope.)

## 1. Bring up the stack

One host, one command — the server + Postgres + MinIO (S3) + bucket creation:

```bash
cp .env.example .env          # then edit .env (see secrets below)
docker compose up -d          # builds the server image, starts everything
docker compose ps             # server should become healthy (/readyz)
```

**Deploy a pinned release** (no source build — pull the published image):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
# a different tag: MANTHANA_VERSION=0.3.0 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

This runs `ghcr.io/suraj-gameramp/manthana-server:0.4.0` (overlay inherits
Postgres/MinIO from the base file). Requires Docker Compose v2.24+.

- Founder console: <http://localhost:8000/ui> (sign in with `MANTHANA_SERVER_ADMIN_TOKEN`)
- API docs: <http://localhost:8000/docs> · health: `/healthz` (live), `/readyz` (DB ping)
- MinIO console: <http://localhost:9001> (`manthana` / `manthana-secret`)

The container reaches Postgres/MinIO by service name (`postgres:5432`,
`minio:9000`); compose sets those for the server automatically. Host ports
(`5433`, `9000/9001`, `8000`) are for your machine. Tables are created on startup
(idempotent).

## 2. Secrets (`.env`)

Set real values — the server refuses to start with an empty admin token or JWT
secret. **Never put these on a command line.**

| Var | Purpose |
|---|---|
| `MANTHANA_SERVER_JWT_SECRET` | signs engineer agent tokens (use ≥32 random bytes) |
| `MANTHANA_SERVER_ADMIN_TOKEN` | gates the founder console + admin/founder API |
| `MANTHANA_SERVER_K_ANON` | k-anonymity floor for org aggregates (keep ≥4 in prod) |
| `ANTHROPIC_API_KEY` + `MANTHANA_SERVER_LLM=anthropic` | real founder narratives (optional) |
| `MANTHANA_SERVER_LLM_MODEL` | model id (default `claude-sonnet-4-6`) |
| `MANTHANA_SERVER_LLM_MAX_TOKENS` | narrative cap (default 1024; 1..100000) |

**LLM provider (founder narrative + weekly digest).** Default is the deterministic mock
(no key needed). Set `MANTHANA_SERVER_LLM=anthropic` with a single server-wide
`ANTHROPIC_API_KEY` (and install the `manthana-server[llm]` extra) for real narratives.
The provider is **resilient + fail-safe**: transient errors (rate limit, connection, 5xx)
are retried with backoff; a missing SDK/key or a persistent error **falls back to the mock
and logs** rather than crashing the server, and the founder/digest endpoints still return a
safe "insufficient data" instead of a 500.

Compose overrides DB/S3 wiring for the in-cluster server, so the `MANTHANA_SERVER_DB_URL`
/ object-store lines in `.env` only matter when running the server **on the host**
(`./scripts/serve.sh`).

## 3. TLS / public exposure

Compose binds the API on `:8000` (HTTP, localhost). For a team, put a reverse
proxy (Caddy / nginx / a cloud LB) in front terminating TLS and forwarding to
`server:8000`, and expose only the proxy. Engineers then point at
`https://manthana.yourco.com`.

## 4. Provision each engineer

One command creates the org + team (idempotent) and mints that engineer's token:

```bash
docker compose exec server manthana-server onboard \
    acme "Acme Inc"  platform "Platform"  alice@acme.com
# prints: provisioned org=acme team=platform actor=alice@acme.com
#         eyJhbGc...   <- the engineer's agent token (valid 365 days)
```

Hand the printed token to the employee for their one-time `manthana login`
(see [onboarding.md](onboarding.md)). Cross-engineer **skill mining only fires at
≥4 distinct contributors** in a team (the k-anon floor), so onboard the team, not
just one person.

## 5. Operate

- **Founder query / org skills:** the `/ui` console (or `POST /v1/founder/query`,
  `POST /v1/admin/mine-skills` with `X-Admin-Token`).
- **Backups:** the `pgdata` and `miniodata` volumes hold all org state.
- **Rotate an engineer:** re-run `onboard` (mints a fresh token); tokens otherwise
  expire after 365 days.
- **Upgrade:** `git pull && docker compose up -d --build`.

## Scaling beyond one host (published image + Kubernetes)

The server image is published to **GHCR** on each version tag by
`.github/workflows/publish-image.yml`:

```
ghcr.io/suraj-gameramp/manthana-server:<version>   # e.g. :0.1, :0.1.0
```

Example Kubernetes manifests live in `deploy/k8s/` (the server is stateless;
**Postgres + S3/MinIO are external/managed** — point the ConfigMap at them):

```bash
kubectl apply -f deploy/k8s/configmap.yaml
kubectl create secret generic manthana-server-secrets \
  --from-literal=MANTHANA_SERVER_JWT_SECRET="$(openssl rand -hex 32)" \
  --from-literal=MANTHANA_SERVER_ADMIN_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=ANTHROPIC_API_KEY="sk-ant-..."          # if LLM=anthropic
kubectl apply -f deploy/k8s/deployment.yaml -f deploy/k8s/service.yaml
```

The Deployment runs **non-root** (uid 10001, caps dropped), with liveness
`/healthz` and readiness `/readyz` probes; put an Ingress with TLS in front of
the Service. The real Secret (`deploy/k8s/secret.yaml`) is gitignored — prefer
the `kubectl create secret` form so secrets never touch a file.

## Auditing founder access

Every founder query (API + `/ui`) is recorded — `GET /v1/admin/audit?org_id=…`
(admin token) lists who-asked-what, whether it was answered or withheld, and the
citation count; the console shows a "Recent founder queries" panel.

## Scope (v1)

Single-host Compose or k8s, HTTP behind your own TLS proxy/ingress. Not yet built:
in-app TLS, token refresh/rotation beyond re-`onboard`.
