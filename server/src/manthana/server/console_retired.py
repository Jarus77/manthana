"""Redirects that stand where the server-rendered console used to.

Mounted INSTEAD of ``mount_ui``'s page routes when ``config.retire_html_console``
is on, so there is exactly one console live at a time — a flag that left both
renderers mounted would mean two things claiming the same URL.

Old links are kept working rather than 404'd. `/ui` is in welcome emails, in the
docs, in Slack messages, and is where `manthana-server` prints "console:" on
startup; a founder who follows one should land on the new page, not on an error
telling them the thing they wanted moved.

Same pattern and the same reasoning as ``mount_retired_wiki`` — including that the
redirects are same-origin relative paths. The session cookie is httponly and scoped
``path='/ui'``, which already forces the client to be served from this hostname; a
configurable absolute URL would invite a cross-origin deployment that silently
cannot authenticate.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse, Response


def mount_retired_console(app: FastAPI) -> None:
    def _to(path: str) -> Response:
        return RedirectResponse(url=path, status_code=303)

    def _org(path: str, org_id: str) -> Response:
        """Carry the org through. An admin's view is org-scoped by a query
        parameter on both sides, so dropping it would land them on someone else's
        tenant — or on nothing."""
        return _to(f"{path}?org={quote(org_id, safe='')}" if org_id else path)

    @app.get("/ui")
    def retired_console() -> Response:
        return _to("/console")

    @app.get("/ui/sessions")
    def retired_sessions(org_id: str = Query(default="")) -> Response:
        return _org("/console/sessions", org_id)

    @app.get("/ui/session")
    def retired_session(
        org_id: str = Query(default=""), compaction_id: str = Query(default="")
    ) -> Response:
        target = f"/console/sessions/{quote(compaction_id, safe='')}"
        return _org(target, org_id) if compaction_id else _org("/console/sessions", org_id)

    @app.get("/ui/topics")
    def retired_topics(org_id: str = Query(default="")) -> Response:
        return _org("/console/topics", org_id)

    @app.get("/ui/router")
    def retired_router(org_id: str = Query(default="")) -> Response:
        # "Cost $" in the old nav; the page merged with server AI spend.
        return _org("/console/cost", org_id)

    @app.get("/ui/digest")
    def retired_digest(org_id: str = Query(default="")) -> Response:
        return _org("/console/digest", org_id)

    @app.get("/ui/mine-status")
    def retired_mine_status(org_id: str = Query(default="")) -> Response:
        return _org("/console/mining", org_id)


__all__ = ["mount_retired_console"]
