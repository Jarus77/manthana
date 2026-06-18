"""Local dashboard (FastAPI + HTMX), served from the agent.

Employee-facing view over their own local store: sessions (with one-click
Work/Personal toggle), cost summary, and the action audit log. Server-rendered
HTML + htmx (no build step). The decisions doc's review-before-sync inbox and
redaction diff build on this in later phases.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import html

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from manthana.agent.cost import estimate_cost
from manthana.agent.store import Store
from manthana.schemas import Mode, Session

_HTMX = '<script src="https://unpkg.com/htmx.org@2.0.3"></script>'
_STYLE = (
    "<style>body{font:14px system-ui;margin:2rem;max-width:1100px}"
    "table{border-collapse:collapse;width:100%}"
    "td,th{border:1px solid #ddd;padding:6px 8px;text-align:left}"
    "th{background:#f5f5f5}a{color:#06c}nav a{margin-right:1rem}"
    ".personal{color:#a00;font-weight:600}.work{color:#070}"
    "button{cursor:pointer}</style>"
)


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>Manthana — {title}</title>"
        f"{_HTMX}{_STYLE}</head><body>"
        f"<h1>Manthana</h1><nav><a href='/'>Sessions</a>"
        f"<a href='/cost'>Cost</a><a href='/actions'>Actions</a></nav>{body}</body></html>"
    )


def _e(value: object) -> str:
    return html.escape(str(value))


def _mode_cell(session: Session) -> str:
    other = Mode.work if session.mode is Mode.personal else Mode.personal
    cls = "personal" if session.mode is Mode.personal else "work"
    return (
        f"<span class='{cls}'>{_e(session.mode)}</span> "
        f"<button hx-post='/session/{_e(session.id)}/mode/{other.value}' "
        f"hx-target='#row-{_e(session.id)}' hx-swap='outerHTML'>→ {other.value}</button>"
    )


def _session_row(session: Session) -> str:
    tags = ", ".join(f"{_e(k)}={_e(v)}" for k, v in session.tags.items()) or "—"
    return (
        f"<tr id='row-{_e(session.id)}'>"
        f"<td>{_e(session.id)}</td><td>{_e(session.project)}</td>"
        f"<td>{_e(session.surface)}</td><td>{session.turn_count}</td>"
        f"<td>{tags}</td><td>{_mode_cell(session)}</td></tr>"
    )


def create_app(store: Store) -> FastAPI:
    app = FastAPI(title="Manthana Dashboard")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        rows = "".join(_session_row(s) for s in store.list_sessions(limit=200))
        table = (
            "<table><tr><th>session</th><th>project</th><th>surface</th>"
            "<th>turns</th><th>tags</th><th>mode</th></tr>"
            f"{rows or '<tr><td colspan=6>no sessions captured yet</td></tr>'}</table>"
        )
        return _page("Sessions", table)

    @app.post("/session/{session_id}/mode/{value}", response_class=HTMLResponse)
    def toggle_mode(session_id: str, value: str) -> str:
        try:
            store.set_session_mode(session_id, Mode(value))
        except ValueError:
            pass
        session = store.get_session(session_id)
        return _session_row(session) if session else "<tr><td>gone</td></tr>"

    @app.get("/cost", response_class=HTMLResponse)
    def cost() -> str:
        total = 0.0
        rows = []
        for s in store.list_sessions(limit=200):
            breakdown = estimate_cost(store.get_turns(s.id))
            total += breakdown.usd
            rows.append(
                f"<tr><td>{_e(s.id)}</td><td>{_e(s.project)}</td>"
                f"<td>{_e(breakdown.tier)}</td><td>${breakdown.usd:.4f}</td></tr>"
            )
        table = (
            "<table><tr><th>session</th><th>project</th><th>tier</th><th>est. cost</th></tr>"
            f"{''.join(rows)}</table><p><b>Total: ${total:.4f}</b></p>"
        )
        return _page("Cost", table)

    @app.get("/actions", response_class=HTMLResponse)
    def actions() -> str:
        rows = "".join(
            f"<tr><td>{_e(a.fired_at)}</td><td>{_e(a.action_id)}</td>"
            f"<td>{_e(a.outcome)}</td><td>{_e(a.trigger_condition)}</td>"
            f"<td>{_e(a.actor)}</td></tr>"
            for a in store.list_audit(limit=200)
        )
        table = (
            "<table><tr><th>fired_at</th><th>action</th><th>outcome</th>"
            "<th>trigger</th><th>actor</th></tr>"
            f"{rows or '<tr><td colspan=5>no actions fired yet</td></tr>'}</table>"
        )
        return _page("Actions", table)

    return app


__all__ = ["create_app"]
