# How Manthana works

One page, read once. Every other page assumes this vocabulary.

## The pipeline

```
 ENGINEER'S LAPTOP                                        ORG SERVER
 ────────────────────────────────────────────────────     ────────────────────────────

 Claude Code / Codex
   writes transcripts to ~/.claude/projects
        │
        │  manthana watch  (every 5s)
        ▼
   capture ──────────► local SQLite  (~/.manthana/manthana.db)
        │              sessions + turns. Never uploaded as-is.
        │
        │  30 min with no new transcript activity
        ▼
   compaction ───────► a typed digest of the session
        │              deterministic · local · spends no tokens
        │
        │  10 min grace window
        ▼
   auto-release ─────► marked "eligible to sync"
        │              SKIPPED for personal-mode or held sessions
        │
        │  redaction (secrets + PII stripped from free text)
        ▼
   sync ─────────────────────────────────────────────►  POST /v1/compactions
                                                        (+ the redacted raw transcript)
                                                              │
                                                              ▼
                                                        enrichment   (fills the
                                                        qualitative fields)
                                                              │
                                                              ▼
                                                        consolidation (typed notes:
                                                        decisions, conventions, gotchas)
                                                              │
                                                              ▼
                                                        project overviews (one living
                                                        article per project)
                                                              │
                                                              ▼
                                                        the org wiki
```

## The pieces

| Piece | Runs where | What it is |
|---|---|---|
| `manthana` | engineer's laptop | The local agent + CLI. Apache-2.0. Owns the local store. |
| `manthana watch` | engineer's laptop, as a login service | The daemon that does capture → compact → release → sync on a loop. |
| local dashboard | engineer's laptop, `127.0.0.1:8765` | The engineer's own view: sessions, cost, hold/release, personal mode. |
| `manthana-server` | one machine for the whole org | Receives synced digests, runs the server-side LLM passes, serves the wiki + founder console. AGPL-3.0. |
| `web/` (Next.js) | alongside the server | The browser wiki client engineers actually read. See [self-hosting/web-client.md](../self-hosting/web-client.md). |

## The two windows

These get confused constantly, so: they are unrelated, and they answer different
questions.

| Window | Default | Question it answers | Change it with |
|---|---|---|---|
| **Settle** | **30 minutes** | "Is this session over yet?" A transcript with no new activity for 30 minutes is treated as finished and gets compacted. Re-compacted if you later resume it. | `manthana watch --settle-min 45` |
| **Release grace** | **10 minutes** | "Do you want to stop this from being shared?" A freshly built compaction sits for 10 minutes before auto-releasing. | `manthana watch --release-min 30` |

The settle window matches the same 30-minute gap the collectors use to decide
where one session ends and the next begins — a shorter settle window would
compact a session that had not actually ended, producing a digest of half the
work.

Nothing in either window spends tokens. Compaction is deterministic string and
structure work over the transcript you already have.

## The two-layer model

Manthana deliberately keeps two layers, and it matters which one you read.

**Layer 1 — session digests (the primary sources).** One digest per session:
what was attempted, what changed, files touched, tests added, dead ends, outcome,
cost. These are facts with a provenance. Everything downstream cites them.

**Layer 2 — the wiki (one small living article per project).** A fresh reader
should absorb what a project *is* in ten seconds. Articles are written by the
server from the digests underneath them, and every claim links back down to the
sessions that support it.

The layering is the whole design: the top layer is short enough to actually read,
and never asks you to trust it, because the evidence is one click below.

## What spends money

| Step | Where | Model call? |
|---|---|---|
| capture | laptop | no |
| compaction | laptop | **no** — deterministic |
| release + sync | laptop | no |
| `manthana ask` | laptop | yes — via *your own* `claude` / `codex` CLI, no API key needed |
| `manthana insights`, `related`, `mine-skills` | laptop | no |
| enrichment / consolidation / project overviews | server | yes — on the operator's key or their own Claude CLI, **and all three are off by default** |

The server does nothing LLM-shaped until an operator explicitly turns it on. See
[Budgets & the LLM passes](../founders/privacy-and-budgets.md).

## Where data lives

| Data | Location | Who can read it |
|---|---|---|
| Raw transcripts | `~/.claude/projects` (written by Claude Code) | you |
| Sessions, turns, compactions | `~/.manthana/manthana.db` (override with `MANTHANA_DATA_HOME`) | you |
| Agent config + team token | `~/.manthana/manthana.toml`, mode `0600` | you |
| Released digests | org server database | your org's founder + engineers, per privacy mode |
| Released raw transcripts | org server object store (S3/MinIO/memory) | founder only, behind an audited drill-down |
| Server secrets | env vars, or `~/.manthana-server/server-secrets.toml` mode `0600` | the operator |

## Next

- [Privacy & security model](privacy.md) — the guarantees, and why they are shaped this way
- [CLI reference](cli.md) — every command
- Back to the [documentation index](../README.md)
