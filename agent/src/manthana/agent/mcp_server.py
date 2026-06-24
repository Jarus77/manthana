"""MCP server — Manthana's read-only query tools over the engineer's OWN local store.

The "lean on Claude Code behind the trust boundary" surface (architecture axis 4):
Claude Code (or any MCP client) drives retrieval / drill-down through these tools
instead of us building a bespoke UI. Scope is **engineer-self only** — own local data,
never the org — so there is no k-anon / redaction concern here.

Optional: requires the ``mcp`` extra (``uv sync --extra mcp``); ``manthana mcp``
degrades to an install hint when it's absent.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from typing import Any

from . import insights as _ins
from .store import Store

INSTALL_HINT = 'MCP SDK not installed — run: uv sync --extra mcp  (or pip install "manthana[mcp]")'
TOOLS = ["insights", "ask", "topics", "thread", "drill_raw"]


def available() -> bool:
    try:
        import mcp.server.fastmcp  # type: ignore[import-not-found]  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 - extra not installed
        return False


# ── tool bodies (plain + testable; each opens its own store) ─────────────────
def tool_insights(store: Store, since: str | None = None) -> dict[str, Any]:
    s = _ins.structural_insights(store, since=since)
    return {
        "session_count": s.session_count,
        "compaction_count": s.compaction_count,
        "by_project": s.by_project,
        "by_outcome": s.by_outcome,
        "est_cost_usd": s.est_cost_usd,
        "top_friction": s.top_friction,
    }


def tool_ask(store: Store, query: str) -> dict[str, Any]:
    r = _ins.ask(store, query)
    return {
        "answer": r.narrative,
        "citations": r.citations,
        "coverage": r.coverage.note() if r.coverage else None,
    }


def tool_topics(store: Store) -> list[dict[str, Any]]:
    return [
        {"label": t.label, "sessions": len(t.sessions), "sample_intents": t.sample_intents}
        for t in _ins.my_topics(store)
    ]


def tool_thread(store: Store, session_id: str) -> list[dict[str, Any]]:
    return [
        {"id": c.id, "project": c.project, "intent": c.task_intent, "outcome": str(c.outcome)}
        for c in _ins.thread(store, session_id)
    ]


def tool_drill_raw(
    store: Store, compaction_id: str, start: int = 0, end: int | None = None
) -> list[dict[str, Any]]:
    return [
        {"seq": t.seq, "role": str(t.role), "text": (t.content or t.tool_output or "")[:2000]}
        for t in _ins.drill_raw(store, compaction_id, start=start, end=end)
    ]


def build_server() -> Any:  # pragma: no cover - thin SDK wiring
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]

    server = FastMCP("manthana")

    @server.tool()
    def insights(since: str | None = None) -> dict[str, Any]:
        """Token-free rollups of your own captured work (projects, outcomes, friction)."""
        return tool_insights(Store.open(), since)

    @server.tool()
    def ask(query: str) -> dict[str, Any]:
        """A grounded, cited answer over your own compactions."""
        return tool_ask(Store.open(), query)

    @server.tool()
    def topics() -> list[dict[str, Any]]:
        """Your emergent topic clusters across sessions."""
        return tool_topics(Store.open())

    @server.tool()
    def thread(session_id: str) -> list[dict[str, Any]]:
        """The arc of one transcript across its resumed slices."""
        return tool_thread(Store.open(), session_id)

    @server.tool()
    def drill_raw(
        compaction_id: str, start: int = 0, end: int | None = None
    ) -> list[dict[str, Any]]:
        """The raw turns behind a compaction (your own data) — depth when the digest is thin."""
        return tool_drill_raw(Store.open(), compaction_id, start, end)

    return server


def run() -> None:  # pragma: no cover - blocking stdio loop
    if not available():
        raise RuntimeError(INSTALL_HINT)
    build_server().run()


__all__ = [
    "available",
    "run",
    "build_server",
    "INSTALL_HINT",
    "TOOLS",
    "tool_insights",
    "tool_ask",
    "tool_topics",
    "tool_thread",
    "tool_drill_raw",
]
