# Manthana Decisions Lock

*Single-page reference of locked decisions for v1 build. Companion to* `manthana-spec.md`, `manthana-ecc-reuse-list.md`, `manthana-actions.md`. *If something here conflicts with the longer docs, this document wins for v1.*

---

## Identity

- **Name:** Manthana
- **License:** AGPL-3.0 (server), Apache-2.0 (collectors, client tooling, SDKs)
- **Repo:** mono-repo; sub-packages for `agent/`, `server/`, `collectors/`, `schemas/`, `tests/`, `docs/`
- **Attribution to ECC:** `LICENSES/MIT-ECC.txt` retains original MIT copyright; `NOTICE` file credits ECC for vendored components

## Language and stack

- **Python 3.11+** for everything (local agent, server, collectors, CLI)
- **FastAPI** for the server (async, OpenAPI built-in)
- **SQLModel** for ORM (combines SQLAlchemy and Pydantic; one model class for both validation and DB)
- **asyncio** for concurrency
- **Pydantic v2** for all schema definitions in Python
- **JSON Schema** mirrored from Pydantic models for cross-language reuse and CI validation
- **typer** for CLI
- **uv** for package management
- **ruff** for lint; **pyright** for type-check
- **pytest** + **pytest-asyncio** for tests
- **anthropic** and **openai** SDKs available but not required (compactor uses engineer's existing CLI access, not direct SDK calls)
- **sentence-transformers** for local embeddings
- **sqlite-vec** for local vector store; **pgvector** on the server

## Storage

- **Local agent:** SQLite, single file at `$MANTHANA_DATA_HOME/manthana.db`
- **Org server:** Postgres for compactions and metadata; S3-compatible object store (MinIO for self-hosted; AWS S3 / GCS / R2 for cloud) for raw transcripts released on explicit approval

## Data model (v1)

**Entities:** `Turn`, `Session`, `BaseCompaction`, `EngineeringCompaction`, `Action`, `ConsentEntry`

**Turn fields:** `id, session_id, actor, timestamp, role (user|assistant|tool), content, tool_name?, tool_input?, tool_output?, model?, tokens_in?, tokens_out?, cache_creation_tokens?, cache_read_tokens?, error?`

**Session fields:** `id, started_at, ended_at?, actor, surface (claude_code|cursor|codex), project (string), repo_root, turn_count, mode (work|personal)`

**BaseCompaction fields:** `session_id, actor, surface, project, started_at, ended_at, duration_seconds, task_intent, approach, artifacts, outcome (success|partial|abandoned), friction_points, tier_used, est_cost_usd, reusable_pattern: bool, released: bool, released_at?, action_triggers: list[str]`

**EngineeringCompaction extends with:** `files_touched, prs_opened, tests_added, dead_end_branches, languages, frameworks`

**FrictionPoint shape:** `{category: enum[loop, tool_error, abandon, retry, deadend], description: str, turn_refs: list[turn_id]}`

**Polymorphism:** `BaseCompaction` is the parent class; role-specific extensions deferred (Sales, Design) to v2; HR indefinitely deferred

## Capture

- **v1 surfaces:** Claude Code CLI, Codex CLI; IDE collector (Cursor first) deferred to v1.5; web collector deferred to v2
- **v1 assumption:** full access to raw transcripts at known paths (`~/.claude/projects/*.jsonl`, `~/.codex/sessions/`, etc.); no permission negotiation in v1
  - *Historical note (2026-06-19): no Codex rollout JSONL was present on the first verified machine, so the initial collector shipped as a stub.*
  - *Update (2026-07-18): Codex Desktop 0.144/0.145 rollout JSONL was verified at `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` plus `~/.codex/archived_sessions/`. The collector now handles messages, tool calls/results, token counts, metadata, and context compactions.*
- **Project inference:** `git rev-parse --show-toplevel` with cwd basename fallback; no `manthana init` required per project
- **Session boundary rule:** a session is a contiguous block of turns. New session triggered by:
  1. >30 minute gap since last turn in current session, OR
  2. Clean exit from Claude Code / Codex (Stop hook fires), OR
  3. >6 hours of continuous activity since session start (forced cap)
- **`--resume` semantics:** within the 30-minute window, extends existing session; outside the window, creates new session linked to prior via `resumed_from: session_id` reference

## Trust contract

- Employee owns the local store; org sees only what employee releases
- **Personal mode never syncs** — invariant enforced by a single dedicated test that must exist before any sync code lands
- Review-before-sync inbox surfaces every compaction with diff view before release
- `released: bool` flag on compactions; raw transcripts uploaded to object store only on explicit release (`released = true` triggers raw upload)
- K-anonymity floor on the server: no team-level aggregate produced where contributor count < 4
- Personal-mode sessions excluded from all actions, period (no opt-in carve-out in v1)

## Compactor

- Compactor invokes the engineer's existing model access, not a bundled API key:
  - **Claude Code:** shells out to `claude -p "<compaction prompt>" --output-format json`
  - **Codex:** shells out to `codex exec "<compaction prompt>"`
  - **Other surfaces (v2):** same pattern, surface-specific invocation
- Manthana ships no API key, has no hidden cost, and inherits whichever model tier the engineer has configured
- Compaction prompt is a fixed template plus the session's normalized turns serialized as compact JSON; the LLM is instructed to return a `BaseCompaction`-shaped JSON object

## Embeddings

- Local model: **`BAAI/bge-large-en-v1.5`** as default; configurable via `manthana.toml`
- All embeddings run on the engineer's laptop; no third-party embedding API in v1
- Vector store: `sqlite-vec` locally; `pgvector` server-side for cross-engineer skill clustering

## Daemon model

- Auto-start on every boot via system service:
  - **macOS:** `launchd` plist under `~/Library/LaunchAgents/com.manthana.agent.plist`
  - **Linux:** `systemd` user unit at `~/.config/systemd/user/manthana-agent.service`
  - **Windows:** Windows Service registered via `sc create`
- Daemon runs continuously; watches transcript directories with platform-native file-watchers (FSEvents on macOS, inotify on Linux, ReadDirectoryChangesW on Windows)
- No manual `manthana start` after install
- Single-binary install via `pip install manthana` triggers a post-install hook that registers the service and starts it

## Founder query

- **Structured-query-first, narrative-second:** every natural-language query is first parsed (by LLM) into a structured filter `(team?, time_range?, project?, outcome?, actor?, surface?)`, then SQL runs over compactions matching the filter, then a separate LLM call writes the narrative grounded in the SQL result
- Every claim in the narrative cites specific compaction IDs
- The grounded-citation requirement is non-optional; queries that cannot be grounded return "insufficient data" instead of hallucinating

## Test commitments

- **Personal-mode leak test** lives at `tests/test_personal_mode_invariant.py` from commit one, must pass before any sync code is merged
- Other test infrastructure (adversarial redaction suite, compaction fidelity baseline, etc.) deferred to v1.1 explicitly
- CI runs lint + type-check + the personal-mode test on every PR; broader test gates land in v1.1

## Actions

- **8 v1 actions** committed:
  1. Auto-surface prior work at session start (engineer, read, silent)
  2. Surface own forgotten solutions (engineer, read, silent)
  3. Loop detection warning (engineer, warn, opt-out)
  4. Auto-tag sessions (engineer, write, silent)
  5. Founder natural-language query (org, read, silent)
  6. Founder weekly digest (org, notify, silent)
  7. Cost transparency dashboard (engineer, notify, silent)
  8. Weekly team digest (org, notify, opt-out)
- All actions respect the consent override hierarchy (engineer opt-out wins for own data; org opt-out wins for boundary-crossing actions)
- Personal-mode sessions excluded from all actions, no carve-out

## Architectural seams (v1 must build)

These exist in v1 even though most actions are v1.5+:

- **Action dispatcher** in the local agent — component that listens for trigger events and routes to registered handlers
- **`action_triggers: list[str]` field** on every `Compaction`
- **Action queue table** on the server for pending actions awaiting human approval
- **Action audit log** for every fired action with trigger condition, confidence score, outcome
- **Consent registry** table for per-engineer and per-admin opt-in/opt-out state

## ECC reuse

- **Direct vendor** with attribution: `schemas/state-store.schema.json`, the validator pattern, cross-platform utilities (`utils.js` ported to Python), secret-detection regex patterns from `governance-capture.js`, cost-tracker token-summation logic, agent-data-home resolution pattern, session-aliases
- **Pattern reuse without direct code copy:** state-store layer (rewritten for SQLite + Postgres), session adapter system (rewritten for Manthana's flatter `Turn` schema), skill-versioning/provenance framework
- **Do NOT clone:** `continuous-learning-v2` skill (rebuild for cross-engineer mining with k-anonymity), ECC's installer (Manthana's is simpler), ECC's 262 skills/64 agents/84 command shims (irrelevant)
- **Outreach to Affaan Mustafa** on GitHub as courtesy before vendoring begins

## Naming (to confirm before first push)

- **PyPI / package name:** `manthana` (verify availability)
- **CLI binary:** `manthana` (full name; aliases `mant` or `mn` only if conflict)
- **Service name in service files:** `com.manthana.agent` (macOS), `manthana-agent` (Linux/Windows)
- **GitHub repo:** TBD (`manthana` or `manthana-platform`)
- **Environment variable for data home:** `MANTHANA_DATA_HOME`

## Open questions (not blocking v1 start)

These are catalogued but do not block the first 2,000 lines of code:

- Server authentication and multi-tenancy mechanism (likely JWT + team-scoped tokens; lock before server work begins)
- Distribution mechanism beyond `pip install` (Homebrew formula, curl one-liner)
- Design partner identification (need 2-3 startups; IIT Bombay and Actioneer networks as starting point)
- Local dashboard UI framework (FastAPI-served static HTML+HTMX recommended for v1; can swap to React in v2 if needed)
- Specific compaction prompt template (will iterate after first 20 real compactions; treat as v0 prompt to refine)
- Action versioning strategy (semver actions; defer until first action is shipped)
- Cross-org action federation (v3+; do not design for in v1)
- Engineer-level custom action authorship (security surface; defer to v3+)

---

## Order of operations for v1 build

1. **Week 1:** lock schemas in `schemas/` (Pydantic + mirrored JSON Schema); write the personal-mode invariant test against placeholder code; reach out to Affaan Mustafa
2. **Week 2:** local SQLite store + normalized `Turn` storage; cross-platform utilities ported from ECC
3. **Week 3:** `cli-collector` for Claude Code; session boundary inference; project inference
4. **Week 4:** redaction pipeline (vendoring `governance-capture.js` patterns); Work/Personal mode toggle; review-before-sync inbox
5. **Week 5-6:** compactor (shelling to `claude -p` / `codex exec`); cost tracking; local dashboard scaffold
6. **Week 7-8:** server (FastAPI), ingestion API, Postgres schema, k-anonymity floor enforcement
7. **Week 9-10:** founder structured-query-first interface; first 4 actions (auto-tag, cost dashboard, loop detection, prior-work surfacing)
8. **Week 11-12:** remaining 4 actions (founder query/digest, team digest, forgotten solutions); end-to-end deployment to one design partner
9. **Week 13-16:** harden on the design partner's real usage; iterate; prepare for second design partner

Sixteen weeks. Sequential, not parallel. One engineer.

---

## Build decisions log — session 2026-06-19 (Phase 0)

*Realized decisions from the first build session. See `manthana-architecture.md`
for the code-level mapping (file paths, schema reference, ECC reuse map).*

- **Build scope (this engagement):** Foundation + vertical slice (local side:
  capture → store → compact → view → act). No server in this engagement.
  Phase-by-phase review between phases.
- **Surfaces this build:** Claude Code first (built against real transcripts at
  `~/.claude/projects/`); Codex initially registered as a stub, then implemented
  against verified rollout JSONL on 2026-07-18.
- **Monorepo realized as a `uv` workspace** of four distributions sharing the
  PEP 420 namespace `manthana`: `manthana-schemas`, `manthana-collectors`,
  `manthana` (agent + CLI), `manthana-server`. Build backend `hatchling`.
  Dual-licensed: server AGPL-3.0, the rest Apache-2.0; ECC attribution in
  `NOTICE` + `LICENSES/MIT-ECC.txt`.
- **Python pinned to 3.12** via `.python-version` (packages still declare
  `>=3.11`); rationale: torch/sentence-transformers may lack 3.14 wheels, so
  embeddings will ship as an optional extra at the skill-miner phase.
- **Tenancy locked:** Org > Team > Actor, with Project as a cross-cutting tag;
  the agent authenticates to the server with a team-scoped JWT.
- **Sync chokepoint:** `manthana.agent.sync.eligible_for_sync` is the single gate
  all egress passes through; `tests/test_personal_mode_invariant.py` guards it
  from commit one (personal never syncs; release-gated; fail-closed).
- **ECC reuse approach:** clone for reference (sibling `../ecc-upstream`, outside
  the repo); copy literals verbatim with per-literal attribution
  (governance-capture secret patterns → Phase 3; cost-tracker `RATE_TABLE` →
  Phase 4); re-express patterns (agent-data-home → `agent/.../datahome.py`;
  session-adapters → `collectors/.../base.py`; state-store → Phase 1).

### Open item added — server-side LLM provider

The decisions above specify CLI-shelling (`claude -p` / `codex exec`) for the
*local* compactor only. The *server's* founder-query narrative also needs a model
but the server has no engineer Claude account. **Decision:** dev uses a mock
provider; **v1.5 the org provisions a server-side API key** behind its own
`LLMProvider` implementation. Tracked in `manthana-architecture.md` §9.
### Open item added — per-filter k-anonymity in the founder query (v1.5)

The Phase-11/founder-UI adversarial review (arch §22) flagged that `founder.py`
applies the global k-anon floor + per-project/per-outcome sub-bucket suppression,
but does not enforce a contributor floor on *every* active filter combination.
Today an `actor` filter collapses to one contributor → "insufficient", and
sub-buckets below the floor are dropped, so the practical exposure is low.
**Decision:** v1.5 adds an explicit per-filter contributor-floor check (reject /
"insufficient" if any applied filter narrows to < k-anon contributors) rather
than patching the reviewed `founder.py` in the UI pass. Tracked in arch §22.

### LLM-provider review — fixes + deferrals (v1.5 items)

Adversarial review of the real founder-narrative provider (arch §23/§24; 23 raw →
13 confirmed). Fixed now: provider-exception graceful degradation in `founder.py`
(both `provider.complete` calls → empty filter / "insufficient data" instead of a
500 that could surface the SDK exception), `ui_mine` guarded, defensive text-block
parsing, and config numeric bounds (`k_anon_floor >= 1`, `1 <= llm_max_tokens <=
100000`).

**Deferred to v1.5 (tracked, not built in this pass):**
- **Founder-query audit log** (#4): record who queried which org, with which
  provider, which compaction ids were cited, and surface an `/v1/audit` view.
  Reuse the existing action-queue/audit seam.
- **Server-side personal-mode reject** (#10): defense-in-depth — carry `mode` on
  the compaction to the server and reject `personal` at `ingest_compaction`. The
  primary invariant already holds at the agent chokepoint
  (`eligible_for_sync` + `test_personal_mode_invariant.py`); this is belt-and-
  suspenders, deferred to avoid a broad schema change in the provider pass.

**Rejected:** an `llm_model` whitelist (#9 / critic-3) — hardcoding valid model
IDs would reject legitimate *future* models (e.g. a new Opus). An unknown model
now fails the API call, which degrades gracefully via the exception handling
above. `llm_model` stays free-form + configurable.

### Secret handling — .env, never the command line

Secrets (`MANTHANA_SERVER_JWT_SECRET`, `MANTHANA_SERVER_ADMIN_TOKEN`,
`ANTHROPIC_API_KEY`) MUST live in a gitignored `.env`, not on the command line
(CLI args leak into shell history, `ps`, and logs). Committed template:
`.env.example` (kept tracked via a `!.env.example` negation in `.gitignore`).
Loader: `scripts/serve.sh` (`set -a; source .env; set +a; uv run manthana-server
serve`). Documented in `README.md` → "Running the server". Decided after an API
key was pasted on a `!` command line during the live LLM-provider demo (rotate
any key that touches a command line / shared transcript).

### Org wiki — two wikis, no k-anon in the wiki layer (2026-07-20)

Full spec: **`spec/manthana-org-wiki.md`** (locked). The short version, because
two of these reverse earlier locks:

- **Two wikis, not one.** The engineer's laptop dashboard is a *personal* wiki
  (projects → sessions → full compaction cards → local search) that sees
  everything including personal-mode and unreleased work, and is **zero-LLM**.
  The org server hosts the *shared* wiki (notes + live rollups + Q&A + teaching).
- **REVERSES "k-anonymity everywhere" for the wiki layer.** The k-anon floor made
  the founder's flagship question ("what is Suraj working on and what did he
  decide?") unanswerable for a consented ~10-person startup. The new pages/Q&A
  paths (`pages.py`, `ask.py`, `wiki_ui.py`) apply **no** floor and treat Person
  pages as first-class. The original contract survives untouched in `founder.py` /
  `/v1/founder/query`; **no new code threads `k_anon_floor`**.
- **REVISES the "Founder query" section above** for the wiki path: `/v1/founder/ask`
  answers from consolidated notes first and drills into session digests only when
  the notes are thin. The grounded-citation rule is unchanged and still
  non-optional — citations now resolve to note ids *or* compaction ids.
- **Human notes outrank AI notes.** The consolidator may dispute a `source="human"`
  note with evidence but can never supersede it (enforced in
  `consolidate.apply_verdicts`, not in a prompt). This is the mechanism behind
  "correct it once and it sticks for everyone".
- **Auto-publish, revert later**, and history is append-only — including revert,
  which writes a new version rather than rewinding.

### Engineer console logins — the team teaches, not just the founder (2026-07-20)

Resolves the v1 limitation logged with the org wiki. A third console scope,
`engineer`, carries **org + actor** and grants the WIKI only (read, ask, and
teach); the founder's oversight surfaces redirect it to `/ui/home`.

- **`engineer` is NOT the `agent` scope.** An agent token is a laptop sync
  credential in a config file; making it a browser login would turn file-read
  into console access, and it has no human identity to attribute edits to. Each
  `verify_*` rejects the other scopes (pinned both directions in tests).
- **Notes are authored under the engineer's actor id**, so revision history shows
  which colleague corrected what — teaching becomes attributable team behaviour
  rather than anonymous curation.
- **Founders mint their own team's logins** (`POST /v1/engineer-tokens` or the
  console's "Team access" panel), scoped to their own org. Operator-only minting
  would leave the shared context with a single reader.
- Adds `MANTHANA_SERVER_PUBLIC_URL`, used **only** to print the shareable login
  link — never for routing or auth.
