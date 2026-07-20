# Manthana team wiki (client)

The browser UI for the org wiki: what everyone is working on, what the team has
learned, and how the two connect. Talks to `server/src/manthana/server/wiki_api.py`
over `/ui/api/wiki/*`.

Design notes live in `spec/manthana-org-wiki.md` §3b.

## Run it locally

Two processes. The server first:

```sh
# from the repo root — seed a demo org if you don't have real data
uv run python validation/seed_demo_org.py
uv run python validation/seed_demo_notes.py

MANTHANA_SERVER_DB_URL=sqlite:///./manthana-demo.db \
MANTHANA_SERVER_JWT_SECRET=$(openssl rand -hex 32) \
MANTHANA_SERVER_ADMIN_TOKEN=demo-admin-token \
uv run uvicorn manthana.server.app:build_default_app --factory --port 8000
```

Then the client:

```sh
cd web
npm install
npm run dev        # http://localhost:3000
```

Sign in at `/login` with the admin token (or an engineer token minted from the
founder console). To see it as an engineer sees it — which is the point of the
redesign — mint one with `POST /v1/engineer-tokens`.

## Why it must be same-origin

The session cookie is `httponly` and scoped `path=/ui`, so the browser will only
attach it to requests that go to the same origin as the page. That is why
`next.config.mjs` proxies `/ui/*` to the FastAPI server in dev, and why
`deploy/Caddyfile` routes by path in production rather than giving the client its
own hostname. No CORS configuration can substitute: the browser refuses to send
an httponly cookie cross-origin regardless of what the server allows.

## Checks

```sh
npm run build       # also type-checks
npx tsc --noEmit
```

## Layout

```
app/          routes (App Router; every page is a client component + SWR)
components/   primitives.tsx (chips, badges, cards), Shell, ConnectionsPanel,
              TeachControls, AskBar, Loader
lib/          api.ts (fetch wrapper), types.ts (payload shapes + labels)
```

`lib/types.ts` is hand-written rather than generated. The API is small and
stable, and a generator would add a build step to a client whose main virtue is
that it barely has one. If a payload shape changes server-side, that file is the
one place to follow it — and `tests/test_wiki_api.py` pins the fields the client
reads.
