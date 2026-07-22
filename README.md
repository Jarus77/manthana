# Manthana

**Your team's AI coding sessions, turned into a wiki they'll actually read.**

Manthana captures the sessions your engineers are already having with Claude Code
and Codex, distills each finished one into a typed, cited digest, and grows a
small living article per project on top of them. Nobody writes documentation.
Nobody is surveilled.

Everything starts on the laptop: capture is local, compaction is local and
deterministic (it spends no tokens), and nothing syncs until the engineer
releases it. Personal-mode sessions never leave the machine at all. Free text is
redacted on the way out, and the founder's raw drill-down is audited every time
it's used.

**→ [Documentation](docs/README.md)**

| You are… | Start here |
|---|---|
| A **founder or admin** setting this up for a team | [docs/founders/](docs/founders/index.md) |
| An **engineer** who was sent an invite | [docs/engineers/](docs/engineers/index.md) |
| **On your own** — no server, no API key | [docs/solo/](docs/solo/index.md) |
| **Self-hosting** the server | [docs/self-hosting/](docs/self-hosting/index.md) |

Reference: [CLI](docs/reference/cli.md) ·
[Environment variables](docs/reference/environment.md) ·
[How it works](docs/reference/architecture.md) ·
[Privacy & security](docs/reference/privacy.md) ·
[Troubleshooting](docs/troubleshooting.md)

---

## Install

**Engineer:**

```bash
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh
manthana setup mia_…        # the one-liner your admin sent you
```

**Admin — a secure team server, no domain and no certificates, using Tailscale:**

```bash
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh -s server
manthana-server serve --tailscale
manthana-server enroll acme platform --open --server-url https://<machine>.<tailnet>.ts.net
```

Send the printed `manthana setup …` line to each engineer. That's their entire
onboarding. Other deploy paths — your own domain behind Caddy, the full Docker
stack, Kubernetes — are in [docs/self-hosting/](docs/self-hosting/index.md).

Try the whole flow locally with no infrastructure and no permanent changes:
`./scripts/quickstart_demo.sh`.

---

## Two things to know before you judge the output

**The server does no LLM work until you turn it on.** Enrichment, consolidation,
and project overviews all default to off, because each is a background loop that
spends real money. Out of the box you get faithful digests and no wiki articles.
See [privacy & budgets](docs/founders/privacy-and-budgets.md).

**Cross-engineer features need ≥4 contributors.** The k-anonymity floor withholds
aggregates that could re-identify someone, so with three people most org-wide
output is empty by design. Onboard the team, not one person.

---

## Repository layout

```
schemas/      manthana-schemas     (Apache-2.0)  Pydantic models + JSON Schema mirror
collectors/   manthana-collectors  (Apache-2.0)  per-surface transcript adapters
skills/       manthana-skills      (Apache-2.0)  skill miner (shared by agent + server)
agent/        manthana             (Apache-2.0)  local agent + `manthana` CLI
server/       manthana-server      (AGPL-3.0)    org server + founder console
web/          Next.js wiki client  (Apache-2.0)  the browser UI engineers read
deploy/       Caddyfile + k8s manifests
docs/         user documentation   ·  spec/  internal design log  ·  tests/  cross-package tests
```

All Python packages share the PEP 420 namespace `manthana` but are separately
distributable, so the AGPL (server) / Apache (everything the engineer runs) split
is real.

## Development

Requires [`uv`](https://docs.astral.sh/uv/) (Python 3.11+) and Node 22 for `web/`.

```bash
uv sync --all-packages                                     # install all members editable
uv run ruff check . && uv run pyright && uv run pytest     # the gate
uv sync --extra embeddings                                 # optional: bge-large embeddings
cd web && npm install && npm run build                     # the client (also type-checks)
```

## Licensing

Dual-licensed by component — see [`LICENSE`](LICENSE). The server is
**AGPL-3.0-or-later**; all client tooling is **Apache-2.0**. Portions derive from
[ECC](https://github.com/affaan-m/ecc) (MIT, © 2026 Affaan Mustafa); see
[`NOTICE`](NOTICE).
