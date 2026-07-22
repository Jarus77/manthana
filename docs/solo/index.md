# Solo & independent use

No company, no org server, no API key. Manthana runs entirely on your laptop and
becomes a memory of your own work.

```bash
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh
manthana solo
```

That's the whole setup.

## What `manthana solo` does

It's the counterpart to `manthana setup`, which needs an invite from an admin.
In one command it:

1. marks this install as solo (`[mode] solo = true` in `~/.manthana/manthana.toml`),
2. captures your existing Claude Code and Codex history,
3. compacts everything that has settled,
4. installs the capture daemon to run at login,
5. serves your personal wiki at `http://127.0.0.1:8765`.

```
✓ solo mode — Manthana runs entirely on this laptop
  captured 214 session(s) · compacted 187 · auto-capture: installed (runs at login)
  your wiki:  http://127.0.0.1:8765
  ask it:     manthana ask "what did I try for X?"
  check it:   manthana doctor
```

| Flag | Default | Meaning |
|---|---|---|
| `--no-service` | daemon installed | Skip the login daemon; run `manthana watch` yourself |
| `--no-dashboard` | wiki served | Set up and exit instead of serving the wiki |
| `--port <n>` | `8765` | Port for the personal wiki |

**It refuses to run if this install is already connected to an org server**, and
changes nothing:

```
✗ this install is already connected to an org server.
  server: https://manthana.acme.com
  Solo mode is for installs with no org. Nothing was changed.
```

That's deliberate. Silently flipping a team member to solo would stop their work
reaching the org wiki with no visible symptom at all — no error, no warning, just
colleagues who stop seeing what they're doing. If you really do want to leave an
org, disconnect first by removing the `[server]` block from
`~/.manthana/manthana.toml`.

## Why `[mode] solo` exists

Everything on the solo path already worked. What was missing was a way to *say*
you're solo, so `manthana doctor` could tell "hasn't finished onboarding" from
"is one person and always will be".

Without it, `doctor` reported a perfectly healthy personal install as two critical
failures and exited non-zero — which is exactly the signal a new user reads as
"this thing is broken". With the flag set:

```
Manthana doctor (solo — no org server)
  ✓ configured — solo mode — everything stays on this laptop
  • no server — by design — `manthana dashboard` is your wiki
  ✓ model available (for `manthana ask`)
  ✓ auto-capture daemon installed
  • data: 214 sessions · 187 compactions (0 pending) · 0 synced · last sync never
```

Exit code 0. Safe to put in a health check.

## Using it

```bash
manthana dashboard                                   # http://127.0.0.1:8765
manthana insights --since 7d                         # rollups; no model call
manthana ask "how did I end up solving the X bug?"   # cited answer over your work
manthana related <session-id>                        # prior work like this session
manthana mine-skills --write                         # draft skills from your patterns
```

**`manthana ask` needs no API key.** It runs through the `claude` CLI you already
have installed, falling back to `codex`. No request is made to us at any point.
Confirm it's wired up with `manthana doctor` — look for
`✓ model available (for manthana ask)`.

Everything else — capture, compaction, `insights`, `related`, `mine-skills` — is
deterministic and calls no model at all.

## Where your data is

```bash
manthana datahome
# data_home: /Users/you/.manthana
# db_path:   /Users/you/.manthana/manthana.db
```

One directory: the SQLite store, `manthana.toml`, and the update-check cache.
Back it up, move it with `MANTHANA_DATA_HOME`, or `rm -rf` it to forget
everything. Nothing else on your machine is touched except the login service.

There is no server, so no sync path exists at all — the release and redaction
machinery still runs, but nothing has anywhere to go.

---

## Going further: your own wiki server

The local dashboard is a good personal wiki. If you want the *full* wiki layer —
living project articles, typed notes, cross-links, entity pages — run the server
locally too. Still no API key required.

```bash
# 1. Install the server
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh -s server

# 2. Run it, using the Claude CLI you already pay for as the model
export MANTHANA_SERVER_LLM=claude_cli
export MANTHANA_SERVER_ENABLE_ENRICHMENT=1
export MANTHANA_SERVER_ENABLE_CONSOLIDATION=1
export MANTHANA_SERVER_ENABLE_PROJECT_OVERVIEW=1
manthana-server serve --k-anon 1
#    → prints the admin token and http://127.0.0.1:8000

# 3. In another terminal, provision yourself and connect
manthana-server enroll me solo --open --server-url http://127.0.0.1:8000
manthana setup mia_… --actor you@example.com
```

Open `http://127.0.0.1:8000/ui` and sign in with the printed admin token.

Note that step 3 connects this install to a server, so it is no longer "solo" in
the `manthana solo` sense — and `manthana solo` will now refuse to run. That's
fine: it's still entirely your laptop. It just isn't the zero-server path any
more.

### `MANTHANA_SERVER_LLM=claude_cli` — bring your own model

The server shells out to a Claude CLI that is **installed and logged in as the
user running the server**. You spend the Claude subscription you already have
instead of buying a separate `ANTHROPIC_API_KEY`.

```bash
MANTHANA_SERVER_LLM=claude_cli
MANTHANA_SERVER_CLAUDE_CLI=claude     # the binary, if it isn't called `claude`
```

Two things to know:

- **It works on a laptop and not in a container.** It needs both the binary and a
  logged-in `$HOME`, so `manthana-server serve` run as you is fine and the
  published container images are not. That's why it's opt-in rather than an
  automatic fallback.
- **If the binary is missing it logs loudly and falls back to the mock**, which
  returns `{}`. Your server keeps running and your wiki stays honest — it just
  stays empty. Check the server's log if articles never appear.

Cost is metered from what the CLI *actually reports*, not estimated from a price
table, so the usage numbers reflect your real subscription usage.

### Or a model on your own machine

If you run Ollama, LM Studio or vLLM locally, the server can use it. They all
speak the OpenAI chat-completions API, and so does the `openai` provider — the
only difference is where you point it:

```bash
export MANTHANA_SERVER_LLM=openai
export MANTHANA_SERVER_LLM_BASE_URL=http://127.0.0.1:11434/v1   # Ollama
export MANTHANA_SERVER_LLM_API_KEY=ollama                       # any non-empty string
export MANTHANA_SERVER_LLM_MODEL=qwen3:8b
export MANTHANA_SERVER_ENRICH_MODEL=qwen3:8b
export MANTHANA_SERVER_CONSOLIDATE_MODEL=qwen3:8b
```

LM Studio's server is `http://127.0.0.1:1234/v1`; a local vLLM is usually
`http://127.0.0.1:8000/v1` — change the port if the Manthana server already has
`8000`. Nothing leaves your laptop, and nothing is billed.

Be honest with yourself about the model, though. Enrichment and consolidation ask
for structured JSON back, and a small local model is worse at it than Haiku is —
expect thinner articles. Turn on `ENABLE_ENRICHMENT` alone, read a few sessions in
the wiki, and only then add the other two.

### Or your own OpenAI / OpenRouter key

```bash
export MANTHANA_SERVER_LLM=openrouter        # or: openai
export OPENROUTER_API_KEY=sk-or-…            # or: OPENAI_API_KEY
export MANTHANA_SERVER_LLM_MODEL=openai/gpt-4o-mini
export MANTHANA_SERVER_ENRICH_MODEL=openai/gpt-4o-mini
export MANTHANA_SERVER_CONSOLIDATE_MODEL=openai/gpt-4o-mini
```

Neither needs an extra package installed — both go over stdlib HTTP.

**Set all three model ids, whichever of these you choose.** They default to
Anthropic ids, and leaving them that way on a non-Anthropic provider makes every
call fail — quietly, because a failed pass degrades to "no data" rather than
erroring. Note the `openai/` prefix on OpenRouter ids; direct OpenAI ids have none
(`gpt-4o-mini`), and a local server wants whatever name *it* uses.

Confirm what the server actually picked before you wonder why the wiki is empty:

```bash
manthana-server doctor
#   ✓ LLM: openai (http://127.0.0.1:11434/v1) — key present
```

The alternatives are unchanged: `MANTHANA_SERVER_LLM=anthropic` with an
`ANTHROPIC_API_KEY`, or the default `mock`, which honestly returns "insufficient
data" rather than inventing something. Even on `mock` the server stores and
serves your digests faithfully — the primary sources are produced locally and
cost nothing.

### `--k-anon 1` — read this before you copy it

The k-anonymity floor withholds cross-contributor views until at least 4 distinct
people have contributed, so nobody can be re-identified inside an aggregate.
**With exactly one contributor there is nobody to de-identify from, and the floor
only hides your own data from you.**

`1` is therefore correct for a genuine single-person install, and wrong for
anything else. If you ever add one other person, put it back:

```bash
manthana-server serve      # defaults to 4
```

The server warns at startup whenever the floor is below 4.

### Secrets, if you set them

If you don't set `MANTHANA_SERVER_JWT_SECRET` and `MANTHANA_SERVER_ADMIN_TOKEN`,
the server generates a pair once, persists them to
`~/.manthana-server/server-secrets.toml` (mode `0600`), and uses the SQLite
database in that same directory. That's the easy path — just leave them unset.

If you *do* set both, the CLI stops using its persisted pair **and its default
data directory**, so pin the database too, or `serve` and `enroll` will each use
a `./manthana-server.db` relative to wherever you ran them:

```bash
export MANTHANA_SERVER_JWT_SECRET=$(openssl rand -hex 32)
export MANTHANA_SERVER_ADMIN_TOKEN=$(openssl rand -hex 24)
export MANTHANA_SERVER_DB_URL="sqlite:///$HOME/.manthana-server/manthana-server.db"
```

Changing the JWT secret invalidates the token your agent already has, so set it
once, before you `enroll`, and keep the exports in a file you `source`.

### Keeping it local

`serve` binds `127.0.0.1`, so nothing is exposed. To reach your own wiki from a
phone or a second machine, use Tailscale rather than opening a port:

```bash
manthana-server serve --tailscale --k-anon 1
```

## Silencing the update notice

The agent tells you on stderr when a newer version is available. It only prints
to a real terminal, never in CI, never when output is piped, and it never makes a
network call on the command you actually ran.

```bash
export MANTHANA_NO_UPDATE_NOTIFIER=1
```

Or permanently, in `~/.manthana/manthana.toml`:

```toml
[update]
notifier = false
```

## Next

→ [Daily use](../engineers/daily.md) — the commands, in more depth
→ [How Manthana works](../reference/architecture.md)
→ [Self-hosting](../self-hosting/index.md) — if your "org of one" grows
