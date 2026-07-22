# The wiki client (`web/`)

The browser UI your team reads is a **separate Next.js application** in
[`web/`](../../web/). It is not part of the `manthana-server` process, it is not
in the server's Docker image, and it is not started by `manthana-server serve`.

If you deploy only the server, you get the founder console at `/ui` and a plain
server-rendered wiki at `/ui/home`. Perfectly usable. But the client is the wiki
your engineers should be reading, and it is a second deployable you have to
stand up on purpose.

## What it serves

| Route | Page |
|---|---|
| `/` | Main page — org-wide digests, what's active |
| `/projects`, `/projects/[slug]` | Projects and their living articles |
| `/people`, `/people/[actor]` | Who has worked on what |
| `/sessions`, `/sessions/[id]` | Session digests — what was attempted, **What came out of it**, **Dead ends** — and `/verbatim` for the released digest in full |
| `/notes/[id]`, `/notes/[id]/history` | A typed note and its revisions |
| `/entities/[kind]/[name]` | Everything touching one entity |
| `/ask` | Grounded question box |
| `/login` | Sign in with a founder, engineer, or admin token |

It talks to the server over `/ui/api/wiki/*` and nothing else. There is no API
URL baked in at build time.

## The one constraint: same origin

**The client and the server must be served from the same hostname.**

The session cookie is `httponly` and scoped `path=/ui`. A browser will only
attach it to same-origin requests, and it refuses to send an `httponly` cookie
cross-origin no matter what the server allows. **No CORS configuration can
substitute.** Give the client its own hostname and every authenticated call
silently fails.

So: one hostname, two upstreams, routed by path.

## Deploying it

### With Docker Compose

The `web` service is already in [`docker-compose.yml`](../../docker-compose.yml).
`docker compose up -d` builds and starts both.

```
docker compose up -d
# server → :8000    web → :3000
```

Reaching `localhost:3000` directly works for a quick look, but every API call
404s, because nothing is routing `/ui/*` to the server. That is the expected
symptom of a missing proxy, not a bug.

### The reverse proxy

[`deploy/Caddyfile`](../../deploy/Caddyfile) does the path routing:

```caddyfile
<your-domain> {
	@server path /ui* /v1* /mcp* /docs* /openapi.json /healthz /readyz
	reverse_proxy @server 127.0.0.1:8000

	# Everything else: the Next.js wiki client.
	reverse_proxy 127.0.0.1:3000
}
```

Everything the server owns goes to `:8000`; everything else is the client. That
path list is load-bearing — drop `/v1*` and agents can't sync; drop `/ui*` and
nobody can log in.

Adapt the upstreams to your setup: `127.0.0.1:8000` / `127.0.0.1:3000` for
processes on the host, or `server:8000` / `web:3000` for Compose service names on
a shared network.

> The `docker-compose.tls.yml` overlay uses a one-line
> `caddy reverse-proxy --from … --to server:8000`, which sends **everything** to
> the server. That's correct for a server-only deployment and wrong once you run
> the client. Mount `deploy/Caddyfile` into the Caddy container instead
> (`command: caddy run --config /etc/caddy/Caddyfile`).

Behind TLS, also set `MANTHANA_SERVER_COOKIE_SECURE=1`.

## Retiring the old HTML wiki

Once the client is genuinely in front of the server, you can retire the
server-rendered wiki so there is exactly one implementation live:

```bash
MANTHANA_SERVER_RETIRE_HTML_WIKI=1
```

Every `/ui/...` wiki page then `303`s to its equivalent client route rather than
rendering HTML:

| Old | New |
|---|---|
| `/ui/home` | `/` |
| `/ui/page/project/{project}` | `/projects/{project}` |
| `/ui/page/person/{actor}` | `/people/{actor}` |
| `/ui/note/{id}` | `/notes/{id}` |
| `/ui/note/{id}/history` | `/notes/{id}/history` |

Old links keep working instead of 404ing — bookmarks, Slack messages, and the
console's own navigation all point at the old paths, and someone who follows one
should land on the new page.

**The default is `0`, and the default is load-bearing.** The redirect targets
(`/`, `/people/…`) belong to the client, not to the server. Turn this on where
the client is not being served and you replace a working wiki with a 404 for
everyone. Enable it only after you have confirmed the client answers on the same
origin.

The founder console (`/ui`, `/ui/query`, `/ui/digest`, `/ui/router`, …) is
unaffected either way — it stays server-rendered.

## Running it in development

Two processes, and the dev server proxies for you.

```bash
# 1. The API. Seed a demo org first if you have no real data.
uv run python validation/seed_demo_org.py
uv run python validation/seed_demo_notes.py

MANTHANA_SERVER_DB_URL=sqlite:///./manthana-demo.db \
MANTHANA_SERVER_JWT_SECRET=$(openssl rand -hex 32) \
MANTHANA_SERVER_ADMIN_TOKEN=demo-admin-token \
uv run uvicorn manthana.server.app:build_default_app --factory --port 8000

# 2. The client.
cd web && npm install && npm run dev      # http://localhost:3000
```

In development `next.config.mjs` rewrites `/ui/*` through to the API, so the
cookie is set and sent on one origin. Point it elsewhere with
`MANTHANA_API_ORIGIN` (default `http://127.0.0.1:8000`). In production there is
no rewrite — that's the reverse proxy's job.

Sign in at `/login` with the admin token, or with an engineer token minted from
the founder console, which is how you see it as an engineer sees it.

## Next

→ [Self-hosting overview](index.md)
→ [Operations & upgrades](operations.md)
