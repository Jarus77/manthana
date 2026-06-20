# Deploying the Manthana org server (admin guide)

The founder/admin self-hosts one server. Engineers' agents sync **released,
redacted** compactions to it; the founder uses the web console at `/ui`. This is
the AGPL `manthana-server` (Postgres + S3/MinIO).

## 1. Bring up the stack

One host, one command — the server + Postgres + MinIO (S3) + bucket creation:

```bash
cp .env.example .env          # then edit .env (see secrets below)
docker compose up -d          # builds the server image, starts everything
docker compose ps             # server should become healthy (/readyz)
```

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
