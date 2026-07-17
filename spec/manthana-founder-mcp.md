# Founder MCP Gateway — "chat with your team's sessions" (v1, fast path)

Status: **locked design, pre-build** (2026-07-17). Lets a founder/eng-lead connect
their own Claude Code to the hosted server and chat over their org's engineer
sessions. Version A: the founder's Claude Code does the reasoning (their
subscription); we are a governed, navigable data source. The knowledge-consolidation
context layer ([[manthana-knowledge-consolidation]]) is a SEPARATE, later problem —
this v1 exposes the existing data only.

## Decisions (owner, 2026-07-17)

- **Both front doors:** web console (non-technical) + Claude Code via MCP (technical).
  Same data, two renders.
- **Version A** — founder's Claude Code reasons; our tools return data, not finished
  answers. Founder pays their own AI; the per-org $25 server-LLM cap is irrelevant to
  this path.
- **Anonymization OFF** for consenting-pilot orgs. k-anon floor and the separate
  manager role collapse into a single founder=manager role. Implement as a per-org
  `privacy_mode` (`open` | `k_anon`) so a future cautious customer isn't a rewrite —
  do NOT delete the k-anon machinery, gate it.
- **Egress = auto, everything except personal.** Every Claude Code session
  auto-compacts (BATCHED in the background — compact settled sessions periodically,
  not per-session, so each engineer's token spend stays smooth/predictable ~$0.21 ea)
  and auto-syncs to the server (summary + raw). Personal-mode remains
  the one carve-out that never leaves the laptop. This LOOSENS the current
  released-only egress invariant (`agent/.../sync.py::eligible_for_sync`) — a
  deliberate, owner-approved trust-model change for consenting startup pilots; must be
  stated in engineer onboarding.
- **Accuracy must not drop.** Achieved by giving Claude Code navigable *primitives*
  (list/search/grep/read/drill), never a pre-chewed lossy summary — it explores the
  full corpus exactly as it would local files; only latency differs (accepted).
- **What we store per session:** the Manthana compaction(s) = primary/clean layer;
  the secret-scrubbed raw transcript = drill-down layer beneath it. BOTH for every
  session. Multiple compactions per session kept separate + thread-linked, never
  pre-joined. Raw is the ground truth Claude Code drills to when a summary is thin —
  this is what preserves accuracy. S3 storage cost is negligible.
- **Secrets scrubbed** (credentials/keys/PII) even with anonymization off — reuse the
  agent redactor. Safety, not identity-hiding.

## Surface — hosted MCP gateway

`https://api.latentspaces.in/mcp` (MCP over HTTP via FastMCP, mounted in the existing
FastAPI app behind the ALB). Auth = the founder bearer JWT we already mint; the MCP
layer extracts org from the token and scopes every call. Read-only. Every call
audited (reuse `founder_query_audit`).

Tools (mirror local-disk exploration → no accuracy drop):

| Tool | Local equivalent | Returns |
|------|------------------|---------|
| `list_sessions(filter)` | ls/glob | sessions by project/engineer/date/outcome |
| `search(query, k)` | grep-by-meaning | semantic+keyword ranked sessions |
| `grep(pattern)` | grep | exact/regex over raw turns |
| `read_session(id)` | read summary | full compaction digest |
| `read_raw(id, start, end)` | read file | redacted raw turns (paginated) |
| `thread(id)` | follow thread | the arc across a session's slices |
| `list_projects()` / `list_engineers()` | orientation | the org's projects / people |

## Build sequence

1. **Server MCP gateway — BUILT + tested (2026-07-17).** `server/.../founder_mcp.py`:
   8 pure tool bodies (`list_sessions/search/grep/read_session/read_raw/thread/
   list_projects/list_engineers`) over `ServerStore` + object store, all org-scoped;
   `build_founder_mcp` wires them into a `FastMCP` streamable-HTTP app;
   `founder_mcp_asgi` is the auth wrapper (founder bearer token → org into a
   contextvar; 401 at the edge). Mounted at `/mcp` in `create_app` behind
   `enable_founder_mcp` (config, **default OFF** — the session-manager lifespan +
   Host-allowlist wiring are real, so the flag keeps them off the live app until
   enabled). `mcp` server extra added. Verified in-process with a REAL MCP client:
   full handshake, all 8 tools, `read_raw`/`grep`/`thread` work, and **cross-org
   isolation holds over the transport** (org-B founder never sees org-A), every call
   audited. Tests: `tests/test_founder_mcp.py` (8 body + 2 transport, skip w/o extra).
   To enable on a deploy: rebuild image with `--extra mcp`, set
   `MANTHANA_SERVER_ENABLE_FOUNDER_MCP=1` and `MANTHANA_SERVER_MCP_ALLOWED_HOSTS=api.latentspaces.in`.
   Founder connects: `claude mcp add --transport http manthana https://api.latentspaces.in/mcp/ --header "Authorization: Bearer <founder token>"`.
2. **Agent egress change (NEXT):** batched background auto-compaction of all
   non-personal sessions; loosen `eligible_for_sync`; sync summary + secret-scrubbed raw.
3. **Per-org `privacy_mode`** + founder=manager merge (gate the k-anon path).
4. **Onboarding copy update:** engineers told all non-personal Claude Code sessions
   are auto-shared with their founder.
5. Connect the operator's own Claude Code to a demo org; verify accuracy hands-on.

Staging environment (`staging.api.latentspaces.in`, scale-to-zero, seeded from prod
snapshots) is stood up LATER, when the context-layer (L3) experiments start — the fast
path is small enough to verify against synthetic demo orgs on prod. See
[[manthana-hosted-aws]].
