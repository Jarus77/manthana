"""Founder MCP gateway — navigable, read-only tools over ONE org's sessions.

Server-side analogue of the agent's ``mcp_server.py``: a founder connects their own
Claude Code (Version A — their harness does the reasoning) and drives
retrieval/drill-down through these tools instead of us pre-baking answers. The tools
mirror local-disk exploration (list / search / grep / read / drill) so the agent
navigates the full corpus exactly as it would local files — no accuracy drop, only
per-call latency (spec: manthana-founder-mcp.md).

The bodies here are pure + org-scoped + unit-testable. The thin ``build_*`` layer adds
the MCP/HTTP transport, founder-token auth (token → org), and audit — it degrades to
an install hint until the ``mcp`` extra is present.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import contextvars
import json
import re
from typing import TYPE_CHECKING, Any

from manthana.skills.embed import Embedder, default_embedder

from .founder import _index_and_rank
from .founder import thread as _thread

if TYPE_CHECKING:
    from .config import ServerConfig
    from .storage import ObjectStore
    from .store import ServerStore

# The authenticated org for the in-flight MCP request, set by the auth wrapper at the
# transport edge and read by the tools. Defense-in-depth: a tool with no org in context
# fails closed rather than reading anything.
_current_org: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "manthana_mcp_org", default=None
)


def _require_org() -> str:
    org = _current_org.get()
    if not org:
        raise RuntimeError("no authenticated org in MCP request context")
    return org


INSTALL_HINT = "MCP SDK not installed — run: uv sync --extra mcp"
TOOLS = [
    "list_sessions", "search", "grep", "read_session",
    "read_raw", "thread", "list_projects", "list_engineers",
]

# Bound the grep sweep so one call can't scan an unbounded corpus (each session is
# an object-store fetch). Surfaced in the result so truncation is never silent.
_GREP_MAX_SESSIONS = 500
_GREP_MAX_HITS = 50


def available() -> bool:
    try:
        import mcp.server.fastmcp  # type: ignore[import-not-found]  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 - extra not installed
        return False


def _brief(c: Any) -> dict[str, Any]:
    """A one-line session summary (the 'ls' row) — enough to decide whether to drill."""
    return {
        "id": c.id,
        "session_id": c.session_id,
        "engineer": c.actor,
        "project": c.project,
        "outcome": str(c.outcome),
        "started_at": str(c.started_at),
        "intent": c.task_intent,
    }


# ── tool bodies (pure, org-scoped, testable) ────────────────────────────────
def tool_list_sessions(
    store: ServerStore,
    org_id: str,
    *,
    project: str | None = None,
    engineer: str | None = None,
    outcome: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Browse the org's sessions (newest first), optionally filtered — the 'ls/glob'."""
    comps = store.query_compactions(
        org_id=org_id, project=project, actor=engineer, outcome=outcome,
        since=since, until=until, limit=limit,
    )
    return [_brief(c) for c in comps]


def tool_read_session(store: ServerStore, org_id: str, compaction_id: str) -> dict[str, Any] | None:
    """The full structured digest of one session — 'read the summary'."""
    c = store.get_compaction(compaction_id, org_id)
    return c.model_dump(mode="json") if c is not None else None


def tool_search(
    store: ServerStore, org_id: str, query: str, *, k: int = 20, embedder: Embedder | None = None
) -> dict[str, Any]:
    """Semantic + keyword ranking across the org's sessions — 'grep by meaning'. A
    starting point for drill-down, never the final answer (coverage is reported so the
    agent knows how much of the corpus it has seen)."""
    comps = store.query_compactions(org_id=org_id, limit=100_000)
    top, coverage = _index_and_rank(store, org_id, query, comps, embedder or default_embedder(), k)
    return {
        "results": [_brief(c) for c in top],
        "coverage": coverage.note() if coverage else None,
    }


def tool_read_raw(
    store: ServerStore,
    object_store: ObjectStore,
    org_id: str,
    compaction_id: str,
    *,
    start: int = 0,
    end: int | None = None,
) -> list[dict[str, Any]]:
    """The redacted raw turns behind a session (paginated) — 'read the file'. Ground
    truth the agent drills to when a summary is thin. Empty if no raw was synced."""
    key = store.get_raw_key(compaction_id, org_id)
    if not key:
        return []
    blob = object_store.get(key)
    if not blob:
        return []
    turns: list[dict[str, Any]] = []
    for line in blob.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            turns.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # tolerate a malformed line rather than fail the whole drill
    return turns[start:end]


def tool_grep(
    store: ServerStore,
    object_store: ObjectStore,
    org_id: str,
    pattern: str,
    *,
    max_hits: int = _GREP_MAX_HITS,
    max_sessions: int = _GREP_MAX_SESSIONS,
) -> dict[str, Any]:
    """Literal/regex search across the org's raw turns — the 'grep' for when semantic
    search isn't precise enough. Bounded sweep; ``truncated`` flags a hit/scan cap so
    a partial result is never mistaken for 'no more matches'."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return {"error": f"invalid pattern: {exc}", "hits": [], "truncated": False}
    hits: list[dict[str, Any]] = []
    comps = store.query_compactions(org_id=org_id, limit=max_sessions)
    scanned = 0
    for c in comps:
        key = store.get_raw_key(c.id, org_id)
        if not key:
            continue
        blob = object_store.get(key)
        if not blob:
            continue
        scanned += 1
        for line_no, line in enumerate(blob.decode("utf-8", "replace").splitlines()):
            if rx.search(line):
                hits.append({
                    "compaction_id": c.id, "session_id": c.session_id,
                    "engineer": c.actor, "line_no": line_no, "text": line[:500],
                })
                if len(hits) >= max_hits:
                    return {"hits": hits, "truncated": True, "scanned_sessions": scanned}
    return {
        "hits": hits,
        "truncated": len(comps) >= max_sessions,
        "scanned_sessions": scanned,
    }


def tool_thread(store: ServerStore, org_id: str, session_id: str) -> list[dict[str, Any]]:
    """The arc of one transcript across its resumed slices — 'follow the thread'."""
    return [_brief(c) for c in _thread(store, org_id, session_id)]


def tool_list_projects(store: ServerStore, org_id: str) -> list[str]:
    """The org's project slugs — orientation before a targeted query."""
    return store.list_projects(org_id)


def tool_list_engineers(store: ServerStore, org_id: str) -> list[dict[str, Any]]:
    """The org's contributors (id + display name) — who to filter/ask about."""
    return [
        {"id": a.id, "name": a.display_name or a.id.split("@")[0]}
        for a in store.list_actors(org_id)
    ]


def build_founder_mcp(
    store: ServerStore, object_store: ObjectStore, config: ServerConfig
) -> Any:  # pragma: no cover - SDK/ASGI transport wiring (verified via live MCP client)
    """A FastMCP server exposing the tools above, scoped per-request to the founder
    token's org. Returns the FastMCP instance; the caller mounts
    ``streamable_http_app()`` (wrapped by ``founder_mcp_asgi``) and runs its
    session-manager lifespan. Import-guarded — call only when ``available()``."""
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
    from mcp.server.transport_security import (  # type: ignore[import-not-found]
        TransportSecuritySettings,
    )

    # DNS-rebinding protection validates the Host header. Behind the ALB the public
    # host is the deploy's own domain; allow it (+ localhost for local runs/tests).
    # config.mcp_allowed_hosts is comma-separated; "*" disables the check.
    hosts = [h.strip() for h in (config.mcp_allowed_hosts or "").split(",") if h.strip()]
    security = (
        TransportSecuritySettings(enable_dns_rebinding_protection=False)
        if "*" in hosts
        else TransportSecuritySettings(
            allowed_hosts=hosts or ["localhost", "127.0.0.1", "testserver"],
            allowed_origins=["*"],
        )
    )
    # streamable_http_path="/" so, mounted at "/mcp", the endpoint is a clean
    # https://<host>/mcp (not /mcp/mcp). stateless_http: no server-side session state,
    # so any task can serve any request (fits horizontal ECS scaling).
    server = FastMCP(
        "manthana-founder", stateless_http=True, streamable_http_path="/",
        transport_security=security,
    )

    def _audit(name: str, detail: str = "") -> None:
        try:
            store.record_founder_query(
                org_id=_require_org(), query=f"[mcp] {name} {detail}"[:500],
                insufficient=False, citations=[],
            )
        except Exception:  # noqa: BLE001 - auditing must never break a read
            pass

    @server.tool()
    def list_sessions(
        project: str | None = None, engineer: str | None = None, outcome: str | None = None,
        since: str | None = None, until: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Browse this org's engineering sessions (newest first), optionally filtered by
        project / engineer / outcome / date. The 'ls' — start here to orient."""
        org = _require_org()
        _audit("list_sessions")
        return tool_list_sessions(
            store, org, project=project, engineer=engineer, outcome=outcome,
            since=since, until=until, limit=limit,
        )

    @server.tool()
    def search(query: str, k: int = 20) -> dict[str, Any]:
        """Rank this org's sessions by relevance to a question (meaning + keywords).
        A starting point for drill-down; check `coverage` to see how much was searched."""
        org = _require_org()
        _audit("search", query)
        return tool_search(store, org, query, k=k)

    @server.tool()
    def grep(pattern: str, max_hits: int = 50) -> dict[str, Any]:
        """Exact/regex search across the raw session turns — use when `search` isn't
        precise enough. Returns matching lines with their session; `truncated` flags a cap."""
        org = _require_org()
        _audit("grep", pattern)
        return tool_grep(store, object_store, org, pattern, max_hits=max_hits)

    @server.tool()
    def read_session(compaction_id: str) -> dict[str, Any] | None:
        """The full structured digest of one session (intent, approach, outcome,
        friction, files, tokens). 'Read the summary.'"""
        org = _require_org()
        _audit("read_session", compaction_id)
        return tool_read_session(store, org, compaction_id)

    @server.tool()
    def read_raw(
        compaction_id: str, start: int = 0, end: int | None = None
    ) -> list[dict[str, Any]]:
        """The raw turns behind a session (paginated) — ground truth to drill into when
        the digest is thin. Secrets are already scrubbed."""
        org = _require_org()
        _audit("read_raw", compaction_id)
        return tool_read_raw(store, object_store, org, compaction_id, start=start, end=end)

    @server.tool()
    def thread(session_id: str) -> list[dict[str, Any]]:
        """The arc of one session across its resumed slices."""
        org = _require_org()
        _audit("thread", session_id)
        return tool_thread(store, org, session_id)

    @server.tool()
    def list_projects() -> list[str]:
        """The org's project slugs — orientation before a targeted query."""
        _audit("list_projects")
        return tool_list_projects(store, _require_org())

    @server.tool()
    def list_engineers() -> list[dict[str, Any]]:
        """The org's engineers (id + name) — who to filter or ask about."""
        _audit("list_engineers")
        return tool_list_engineers(store, _require_org())

    return server


def founder_mcp_asgi(
    server: Any, config: ServerConfig
) -> Any:  # pragma: no cover - ASGI transport wiring
    """Wrap the FastMCP streamable-HTTP app with founder-token auth at the transport
    edge: reject anything without a valid founder bearer token (401), and pin the
    request's org into the context the tools read. This is THE tenant boundary for the
    gateway."""
    from starlette.responses import JSONResponse

    from .auth import AuthError, verify_founder_token

    inner = server.streamable_http_app()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await inner(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        try:
            claims = verify_founder_token(config.jwt_secret, token)
        except AuthError:
            await JSONResponse(
                {"detail": "a founder bearer token is required"}, status_code=401
            )(scope, receive, send)
            return
        reset = _current_org.set(claims.org_id)
        try:
            await inner(scope, receive, send)
        finally:
            _current_org.reset(reset)

    return app


__all__ = [
    "available",
    "build_founder_mcp",
    "founder_mcp_asgi",
    "INSTALL_HINT",
    "TOOLS",
    "tool_list_sessions",
    "tool_read_session",
    "tool_search",
    "tool_read_raw",
    "tool_grep",
    "tool_thread",
    "tool_list_projects",
    "tool_list_engineers",
]
