# Troubleshooting

Start here:

```bash
manthana doctor            # engineer's laptop
manthana-server doctor     # the org server
```

Both exit non-zero on a critical failure, and both print more than they check —
data counts, last sync time, backlog states. Read the whole output.

---

## Engineer symptoms

### `doctor` says "configured ✗ · server=(unset)" and exits 1

The agent has never been connected. Run the `manthana setup mia_…` line your
admin sent.

**If you have no org and never will**, that's not a failure — you just haven't
told Manthana. Run `manthana solo` once. It sets `[mode] solo = true`, after
which `doctor` prints `Manthana doctor (solo — no org server)`, treats the
missing server as by-design, and exits 0. This is the single most common thing a
new solo user misreads as breakage. See [Solo use](solo/index.md).

### `doctor` says "server reachable ✗"

The URL in your config isn't answering. Check it with `manthana config`, then
confirm with your admin that the server is up and that you're on the right
network — a Tailscale deployment is invisible until you've run `tailscale up`.

### `doctor` says "token accepted ✗"

The server is reachable but rejected your credential. Usually one of: your token
expired (365 days), the server's JWT secret was rotated, or the server's database
was replaced. Ask your admin for a fresh invite and re-run `manthana setup`.

### `doctor` says "raw transcripts sync ✗ — N rejected as unknown"

The server doesn't have the digests those transcripts belong to. If your org's
server was wiped or re-onboarded:

```bash
manthana resync --confirm
manthana sync
```

If it wasn't, they were purged server-side and this is expected — nothing to do.

### No sessions are being captured

```bash
manthana service status     # is the daemon running?
manthana capture            # force one ingest now
manthana sessions           # did anything land?
```

Manthana reads `~/.claude/projects`. If you use Claude Code somewhere unusual, or
only Codex, confirm transcripts are actually being written there. Daemon logs:
`~/Library/Logs/manthana-watch.log` (macOS),
`journalctl --user -u manthana-watch.service` (Linux).

### Sessions are captured but never compacted

Compaction waits for the **settle window** — 30 minutes with no new transcript
activity. A session you're still in won't compact. Force one:

```bash
manthana compact <session-id>
```

Also check you aren't running `watch --no-auto-compact`, and that the session
isn't personal-mode (`manthana sessions` shows the mode).

### Compactions exist but never sync

In order:

1. Are they released? Auto-release takes 10 minutes; held and personal ones never
   release. `manthana dashboard` → Compactions.
2. `manthana sync --check` — reachable and accepted?
3. Are you running `watch --no-sync`?
4. `manthana sync` by hand and read the counts it prints.

### `manthana ask` returns nothing useful

```bash
manthana doctor
#   • model available (for `manthana ask`) — no claude/codex CLI on PATH
```

`ask` runs through your local `claude` CLI, falling back to `codex`. With neither
on `PATH` it degrades to an empty provider rather than erroring. Install one.

If the model is available but answers are ungrounded, you may not have enough
compacted history yet — `manthana insights` shows how much there is.

### `manthana optimize` says it isn't installed

It's optional. `pip install "headroom-ai[proxy,mcp]"`.

### `manthana solo` refuses to run

```
✗ this install is already connected to an org server.
```

By design, and it changed nothing. Flipping a connected engineer to solo would
stop their work reaching the org wiki with no visible symptom. If you genuinely
want to leave the org, remove the `[server]` block from
`~/.manthana/manthana.toml` first.

### I'm on an old version

`manthana doctor` shows an `agent version` line, and the CLI prints a notice on
stderr when your org server runs a newer build than you. To upgrade:

```bash
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh
manthana version
```

Installers from before 2026-07-19 silently skipped upgrading when Manthana was
already present. If your version looks frozen at whatever you first installed,
that's why — the line above fixes it permanently.

To silence the notice: `MANTHANA_NO_UPDATE_NOTIFIER=1`, or `[update] notifier =
false` in `~/.manthana/manthana.toml`.

---

## Server symptoms

### The server refuses to start: "insecure dev defaults"

It's fail-closed on the shipped placeholder secrets, so a deployment can't
silently run with publicly-known credentials. Set real ones:

```bash
MANTHANA_SERVER_JWT_SECRET="$(openssl rand -hex 32)"
MANTHANA_SERVER_ADMIN_TOKEN="$(openssl rand -hex 24)"
```

An **empty** value is rejected for the same reason — it would authenticate anyone.

### "binding to a NON-loopback address without HTTPS"

Exactly what it says. Team tokens are bearer credentials and would travel in
plaintext. Put Caddy or `--tailscale` in front. See
[Self-hosting](self-hosting/index.md).

### Console logins don't stick

Behind TLS, set `MANTHANA_SERVER_COOKIE_SECURE=1`. On plain local HTTP, make sure
it's **off** — a `Secure` cookie is never sent over HTTP.

### The wiki is a 404 for everyone

Almost certainly `MANTHANA_SERVER_RETIRE_HTML_WIKI=1` without the Next.js client
being served in front of the server. The redirect targets (`/`, `/people/…`)
belong to the client. Either deploy the client behind a path-routing reverse
proxy, or set the flag back to `0`. See [the wiki client](self-hosting/web-client.md).

### The wiki client loads but every page is empty or 401s

Its API calls aren't reaching the server on the same origin. The session cookie
is `httponly` and scoped `path=/ui`; the browser will not send it cross-origin,
and no CORS setting changes that. Route `/ui*`, `/v1*`, `/docs*`, `/healthz`,
`/readyz` to the server and everything else to the client from **one** hostname —
[`deploy/Caddyfile`](../deploy/Caddyfile) does exactly this.

### The wiki has sessions but no articles, and everything says "pending"

The most common server misconfiguration, in likelihood order:

1. **The LLM passes are off.** They default to off.
   `MANTHANA_SERVER_ENABLE_ENRICHMENT=1`, then `ENABLE_CONSOLIDATION`, then
   `ENABLE_PROJECT_OVERVIEW`. Check what the server thinks:
   `GET /v1/admin/enrichment?org_id=…` reports `enabled`.
2. **No real model.** `MANTHANA_SERVER_LLM=anthropic` plus `ANTHROPIC_API_KEY`,
   `claude_cli` to use a Claude CLI you're already logged into, or
   `openai`/`openrouter` with the matching key. The default mock returns `{}` —
   honest, and empty.
3. **A provider is selected, but its key or binary isn't there.** Grep the server
   log for `falling back to mock`, or just run `manthana-server doctor`. Every
   provider degrades rather than crashing, which is right for uptime and awful for
   diagnosis: each pass "succeeds" and writes nothing. For `claude_cli` this means
   the binary **and** a logged-in `$HOME`, so it works when the server runs as your
   own user and **does not work in the container images**. For `openai` /
   `openrouter` it means `OPENAI_API_KEY` / `OPENROUTER_API_KEY`, or
   `MANTHANA_SERVER_LLM_API_KEY`.
4. **The model ids don't match the provider.** See the entry below — this is the
   most common mistake when moving off Anthropic.
5. **The budget cap is hit.** `manthana-server usage <org> --server-url …`, or
   `GET /v1/admin/usage?org_id=…` and look at `quota_blocked` and `spent_usd`. An
   exhausted cap raises no error anywhere a human looks — enrichment simply stops
   and every session stays `pending`, which reads as a bug rather than a bill.
   Raise it with `manthana-server set-quota <org> <usd> --server-url …`.
6. **Consolidation was enabled without enrichment.** It only reads digests
   enrichment has already filled in, so it has nothing to do.

### I switched to `openai` / `openrouter` and nothing is written

Almost always the model ids. `MANTHANA_SERVER_LLM_MODEL`,
`MANTHANA_SERVER_ENRICH_MODEL` and `MANTHANA_SERVER_CONSOLIDATE_MODEL` all default
to **Anthropic** ids (`claude-sonnet-4-6`, `claude-haiku-4-5`), which OpenAI and a
self-hosted endpoint cannot serve. Every call 404s, the pass degrades to "no data",
and nothing surfaces where a human is looking.

```bash
manthana-server doctor
#   • LLM: model ids match the provider — MANTHANA_SERVER_ENRICH_MODEL still set
#     to an Anthropic id — every call to openrouter would fail
```

Set all three. OpenRouter ids carry a vendor prefix (`openai/gpt-4o-mini`,
`anthropic/claude-3.5-sonnet`); OpenAI's own do not (`gpt-4o-mini`); a self-hosted
server wants whatever name it serves the model under.

You do **not** need to worry about `max_tokens` vs `max_completion_tokens` — newer
OpenAI models reject the former, and the provider detects that one `400`, flips,
and retries automatically.

### Which provider is the server actually using?

```bash
manthana-server doctor
#   ✓ LLM: openai (https://api.openai.com/v1) — key present
#   • LLM: openrouter (…) — no key — every pass would silently degrade to the mock
```

That line is the fastest way to tell an empty wiki caused by configuration from one
caused by anything else. It prints the resolved endpoint too, so a self-hosted
`MANTHANA_SERVER_LLM_BASE_URL` that didn't take is visible immediately. Note that
`doctor` reads the same env the server does — run it in the same environment
(inside the container, with the same `.env` sourced), or you'll be checking a
different configuration than the one that's running.

### Enrichment is stuck / digests are "abandoned"

```bash
curl -H "X-Admin-Token: $ADMIN" \
  "https://your-server/v1/admin/enrichment?org_id=acme"
```

A digest with neither a native summary nor an uploaded raw transcript **waits**
rather than burning a model call, and is abandoned after 5 attempts or 7 days. If
the raw has since arrived:

```bash
curl -X POST -H "X-Admin-Token: $ADMIN" \
  "https://your-server/v1/admin/enrichment/retry?org_id=acme&limit=200"
```

By default that only resets digests that could actually enrich now. Add
`&include_without_input=true` to reset the rest.

### Everything returns "insufficient data" / the digest is empty

You're below the k-anonymity floor of 4 distinct contributors, and this is the
system working. Onboard more of the team. For a genuine single-person install,
`--k-anon 1` is the right answer — and only then. See
[Privacy & security model](reference/privacy.md#k-anonymity).

### Raw transcripts vanish after a restart

`MANTHANA_SERVER_OBJECT_STORE=memory`. Fine for a pilot, wrong for anything real.
Set it to `s3` with a bucket. `manthana-server doctor` flags this automatically on
Postgres deployments.

### `docker compose pull` 404s on the image

The image is `ghcr.io/jarus77/manthana-server`, matching what
`.github/workflows/publish-image.yml` pushes — it tags
`ghcr.io/<repository-owner>/manthana-server:<version>`, so the owner segment
follows wherever the repository lives.

If you run a fork or a mirror, override the whole path rather than editing the
Compose file:

```bash
MANTHANA_IMAGE=ghcr.io/your-org/manthana-server \
MANTHANA_VERSION=0.6.3 \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
```

Set `MANTHANA_VERSION` explicitly for anything real — the default tracks the
current release and will move under you otherwise.

### A CLI command says the config is invalid, but `serve` works fine

`serve`, `enroll`, `invites`, `init`, and `doctor` fall back to the persisted
secrets in `~/.manthana-server`. The others — `create-org`, `create-team`,
`token`, `onboard`, `digest`, `router-analysis` — require the
`MANTHANA_SERVER_*` env vars to be set. Either export them, or use the HTTP-based
commands (`onboard-org`, `usage`, `set-quota`), which need only an admin token
and a URL.

---

## Still stuck?

Include this in your report — it's almost everything anyone would ask for:

```bash
manthana version
manthana doctor
manthana config
manthana-server doctor      # if you run the server
```

## Next

→ [CLI reference](reference/cli.md)
→ [Environment variables](reference/environment.md)
