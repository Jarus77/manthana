"""Wiki JSON API — the read/write surface the Next.js wiki client talks to.

Mounted under ``/ui/api/wiki/*``, and that prefix is load-bearing: the console
cookie is set with ``path='/ui'``, so an API under ``/v1`` would never receive
it. Living under ``/ui`` means this API reuses the console's session contract
exactly — ``session_for`` + ``scope_org``, one implementation of tenant
isolation — instead of inventing a second auth scheme for the browser.

Two things differ from the founder console's own API surface:

  * **Any signed-in role may read.** Session digests are org-wide here. The
    engineer who released a digest already published it to their colleagues;
    hiding it from those colleagues while showing it to the founder made the
    wiki an oversight tool rather than a shared context. Raw transcripts are
    NOT here and never will be — tier-2 drill-down stays the audited,
    founder-only ``POST /v1/founder/drill``.
  * **Every detail payload carries its edges.** ``graph.py`` computes them from
    rows the handler already loaded, so the client never has to make a second
    round trip to find out what a page connects to.

Writes are the same four teaching verbs the HTML wiki exposes, calling the same
``teach`` functions — this module adds a transport, not a second set of rules.

**Named by design.** No k-anonymity is applied here, matching ``pages.py``: the
wiki serves the consented-startup segment, and its person pages already list a
named colleague's session digests. De-identifying the org-wide session list
while ``/people/{actor}`` stays named would be a half-measure that protects
nobody and makes two views of one dataset disagree. The k-anon pipeline in
``founder.py`` remains for the original contract; ``privacy_mode`` still gates
the founder console's oversight surfaces.

NOTE: like ``ui.py``/``wiki_ui.py``, this module intentionally does NOT use
``from __future__ import annotations`` — FastAPI must resolve ``Cookie``/``Body``
parameter annotations at runtime.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import logging
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Cookie, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from manthana.schemas import KnowledgeNote, NoteKind
from manthana.skills.projections import (
    activity_rollup,
    project_rollups,
    session_card,
    session_cards,
)
from pydantic import BaseModel, Field

from .ask import ask
from .config import ServerConfig
from .graph import project_neighbors, related_people, session_related
from .llm import LLMProvider
from .metering import QuotaExceededError
from .pages import (
    HOME_WINDOW_DAYS,
    PROJECT_WINDOW_DAYS,
    SECTION_ORDER,
    discovery_feed,
    note_page,
    person_page,
    project_page,
)
from .store import ServerStore
from .teach import NoteNotFoundError, confirm, create, edit, revert
from .ui import COOKIE, ConsoleSession, scope_org, session_for

_log = logging.getLogger(__name__)

API = "/ui/api/wiki"

#: Page size for the cursor-paginated browse endpoints. One screenful plus a
#: little, so "load more" is rare on a small org but bounded on a large one.
PAGE = 30
MAX_PAGE = 100

#: The org-wide window the connection graph is computed over. Wider than the
#: home feed: a collaborator you shared a project with three weeks ago is still
#: a collaborator, and edges from too narrow a window flicker week to week.
GRAPH_WINDOW_DAYS = 45


class ApiError(Exception):
    """Raised inside handlers; converted to a JSON error response."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _jsonable(value: Any) -> Any:
    """Dataclasses, pydantic notes, datetimes and enums → JSON-safe primitives.

    ``asdict`` is not used on the outer object: it would recurse into pydantic
    notes and mangle them, so the walk is explicit and each type is converted by
    the rule that type deserves.
    """
    if isinstance(value, KnowledgeNote):
        return value.model_dump(mode="json")
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _page_envelope(items: list[Any], cursor_of: Callable[[Any], str], limit: int) -> dict[str, Any]:
    """Cursor pagination: the next page starts at the last item's timestamp.

    ``next_cursor`` is only set when the page came back full — a short page is
    the end of the data, and offering a cursor there would cost the client a
    round trip to learn nothing.
    """
    return {
        "items": _jsonable(items),
        "next_cursor": cursor_of(items[-1]) if len(items) >= limit and items else None,
    }


def _clamp(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE))


def _note_sections(notes: list[KnowledgeNote]) -> list[dict[str, Any]]:
    """Group notes into the fixed kind order, emitting only non-empty kinds."""
    return [
        {"kind": str(kind), "notes": _jsonable([n for n in notes if n.kind == kind])}
        for kind in SECTION_ORDER
        if any(n.kind == kind for n in notes)
    ]


def mount_wiki_api(
    app: FastAPI,
    config: ServerConfig,
    store: ServerStore,
    provider: LLMProvider | None = None,
    provider_for: Callable[[str], LLMProvider] | None = None,
) -> None:
    def _provider(org_id: str) -> LLMProvider | None:
        if provider_for is not None:
            return provider_for(org_id)
        return provider

    def _session(cookie: str) -> ConsoleSession:
        """Any signed-in role may read the wiki. Unlike the founder console
        there is no engineer bounce here — the wiki IS the engineer's product."""
        sess = session_for(config, cookie)
        if sess is None:
            raise ApiError(401, "not signed in")
        return sess

    def _org(sess: ConsoleSession, requested: str) -> str:
        """The org this request acts on. Founders and engineers are forced to
        their own regardless of the query string; an admin may name one, and
        falls back to the first org so their first visit isn't empty."""
        org_id = scope_org(sess, requested)
        if org_id:
            return org_id
        orgs = store.list_orgs()
        if not orgs:
            raise ApiError(404, "no orgs yet")
        return orgs[0].id

    def _graph_window(org_id: str) -> list[Any]:
        since = _since_days(GRAPH_WINDOW_DAYS)
        return store.query_compactions(org_id=org_id, since=since)

    # ── errors ───────────────────────────────────────────────────────────
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    # ── session ──────────────────────────────────────────────────────────
    class LoginBody(BaseModel):
        token: str = ""

    @app.post(f"{API}/login")
    def wiki_login(body: LoginBody) -> Response:
        sess = session_for(config, body.token)
        if sess is None:
            return JSONResponse({"detail": "invalid token"}, status_code=401)
        resp = JSONResponse(
            {"role": sess.role, "org_id": sess.org_id, "actor": sess.actor}
        )
        resp.set_cookie(
            COOKIE, body.token, httponly=True, samesite="lax", path="/ui",
            secure=config.cookie_secure,
        )
        return resp

    @app.post(f"{API}/logout")
    def wiki_logout() -> Response:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/ui")
        return resp

    @app.get(f"{API}/me")
    def wiki_me(manthana_admin: Annotated[str, Cookie()] = "") -> dict[str, Any]:
        sess = _session(manthana_admin)
        org_id = _org(sess, "")
        # Per-kind counts let the nav show how much is behind each link, and let
        # the client tell an empty section from an unbuilt one. `faq` is defined
        # in the taxonomy but nothing populates it yet, so without counts the nav
        # would advertise a page that is permanently blank.
        live = store.query_notes(org_id, exclude_superseded=True)
        counts = {str(k): sum(1 for n in live if n.kind == k) for k in SECTION_ORDER}
        return {
            "role": sess.role,
            "org_id": org_id,
            "actor": sess.actor,
            "author": sess.author,
            "can_switch_org": sess.org_id is None,
            "orgs": [o.id for o in store.list_orgs()] if sess.org_id is None else [org_id],
            "kinds": [str(k) for k in SECTION_ORDER],
            "kind_counts": counts,
            "total_notes": len(live),
        }

    # ── home ─────────────────────────────────────────────────────────────
    @app.get(f"{API}/home")
    def wiki_home(
        org_id: str = "", days: int = HOME_WINDOW_DAYS,
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """The discovery feed: what everyone did this week, plus whichever
        knowledge kinds actually moved."""
        sess = _session(manthana_admin)
        org_id = _org(sess, org_id)
        feed = discovery_feed(store, org_id, days=max(1, min(days, 90)))
        payload = _jsonable(feed)
        payload["stream"] = _jsonable(feed.stream)
        payload["sections"] = [
            {"kind": str(kind), "notes": _jsonable(notes)} for kind, notes in feed.sections
        ]
        return payload

    # ── people ───────────────────────────────────────────────────────────
    @app.get(f"{API}/people")
    def wiki_people(
        org_id: str = "", days: int = PROJECT_WINDOW_DAYS,
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """Everyone in the org — active people first, then the quiet ones, so
        the directory stays complete without burying who is working now."""
        sess = _session(manthana_admin)
        org_id = _org(sess, org_id)
        comps = store.query_compactions(org_id=org_id, since=_since_days(days))
        active = activity_rollup(comps)
        seen = {a.actor for a in active}
        quiet = [
            {"actor": row.id, "display_name": row.display_name}
            for row in store.list_actors(org_id)
            if row.id not in seen
        ]
        return {"active": _jsonable(active), "quiet": quiet, "org_id": org_id}

    @app.get(f"{API}/people/{{actor}}")
    def wiki_person(
        actor: str, org_id: str = "", manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        sess = _session(manthana_admin)
        org_id = _org(sess, org_id)
        page = person_page(store, org_id, actor)
        notes = store.query_notes(org_id, exclude_superseded=True)
        edges = related_people(_graph_window(org_id), notes, actor)
        return {
            "actor": page.actor,
            "activity": _jsonable(page.activity),
            "sections": _note_sections(page.notes),
            "sessions": _jsonable(page.sessions),
            "connections": _jsonable(edges),
            "org_id": org_id,
        }

    # ── projects ─────────────────────────────────────────────────────────
    @app.get(f"{API}/projects")
    def wiki_projects(
        org_id: str = "", days: int = PROJECT_WINDOW_DAYS,
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        sess = _session(manthana_admin)
        org_id = _org(sess, org_id)
        comps = store.query_compactions(org_id=org_id, since=_since_days(days))
        rollups = project_rollups(comps)
        seen = {r.project for r in rollups}
        return {
            "active": _jsonable(rollups),
            "quiet": [p for p in store.list_projects(org_id) if p not in seen],
            "org_id": org_id,
        }

    @app.get(f"{API}/projects/{{project}}")
    def wiki_project(
        project: str, org_id: str = "", manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        sess = _session(manthana_admin)
        org_id = _org(sess, org_id)
        page = project_page(store, org_id, project)
        edges = project_neighbors(_graph_window(org_id), project)
        return {
            "project": page.project,
            "rollup": _jsonable(page.rollup),
            "sections": [
                {"kind": str(kind), "notes": _jsonable(notes)} for kind, notes in page.sections
            ],
            "sessions": _jsonable(page.sessions),
            "note_count": page.note_count,
            "neighbors": _jsonable(edges),
            "org_id": org_id,
        }

    # ── sessions (digests only — never raw turns) ────────────────────────
    @app.get(f"{API}/sessions")
    def wiki_sessions(
        org_id: str = "",
        actor: str = "",
        project: str = "",
        outcome: str = "",
        until: str = "",
        limit: int = PAGE,
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """Org-wide browse over released session digests.

        This is the surface that makes the wiki a shared context rather than a
        founder dashboard: any colleague can see what a session was for and how
        it went. The raw transcript behind it is not reachable from here.
        """
        sess = _session(manthana_admin)
        org_id = _org(sess, org_id)
        limit = _clamp(limit)
        comps = store.query_compactions(
            org_id=org_id,
            actor=actor or None,
            project=project or None,
            outcome=outcome or None,
            until=until or None,
            limit=limit,
        )
        cards = session_cards(comps)
        return {
            **_page_envelope(cards, lambda c: c.started_at.isoformat(), limit),
            "total": store.count_compactions(org_id),
            "org_id": org_id,
        }

    @app.get(f"{API}/sessions/{{compaction_id}}")
    def wiki_session(
        compaction_id: str, org_id: str = "", manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        sess = _session(manthana_admin)
        org_id = _org(sess, org_id)
        comp = store.get_compaction(compaction_id, org_id)
        if comp is None:
            raise ApiError(404, "session not found in this org")
        notes = store.query_notes(org_id, exclude_superseded=True)
        links = session_related(comp, notes, _graph_window(org_id))
        card = session_card(comp)
        # The digest AS RELEASED, verbatim. `native_summary` is the coding
        # agent's own compaction summary — the prose the engineer's tool wrote
        # about the session, which the structured fields were derived from. It
        # is redacted on the way off the laptop like every other free-text field
        # (it is not in the redactor's KEEP set), so it is exactly as shareable
        # as `approach` already is. It is NOT the raw transcript, which stays
        # behind the audited founder drill-down.
        return {
            "session": _jsonable(card),
            "native_summary": getattr(comp, "native_summary", None),
            "source": getattr(comp, "source", None),
            "notes": _jsonable(links.notes),
            "disputes": _jsonable(links.disputes),
            "same_actor": _jsonable(session_cards(links.same_actor)),
            "same_project": _jsonable(session_cards(links.same_project)),
            "org_id": org_id,
        }

    # ── knowledge ────────────────────────────────────────────────────────
    @app.get(f"{API}/notes")
    def wiki_notes(
        org_id: str = "",
        kind: str = "",
        status: str = "",
        project: str = "",
        until: str = "",
        limit: int = PAGE,
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """All-time browse by kind. The feed only ever showed the last week;
        this is how knowledge older than the window stays reachable."""
        sess = _session(manthana_admin)
        org_id = _org(sess, org_id)
        limit = _clamp(limit)
        if kind and kind not in {str(k) for k in NoteKind}:
            raise ApiError(422, f"unknown note kind: {kind}")
        notes = store.query_notes(
            org_id,
            kind=kind or None,
            status=status or None,
            project=project or None,
            until=until or None,
            limit=limit,
        )
        return {
            **_page_envelope(notes, lambda n: n.updated_at.isoformat(), limit),
            "org_id": org_id,
        }

    @app.get(f"{API}/notes/{{note_id}}")
    def wiki_note(
        note_id: str, org_id: str = "", manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        sess = _session(manthana_admin)
        org_id = _org(sess, org_id)
        found = note_page(store, org_id, note_id)
        if found is None:
            raise ApiError(404, "note not found in this org")
        note, evidence, disputed = found
        return {
            "note": _jsonable(note),
            "evidence": _jsonable(session_cards(evidence)),
            "disputed_by": _jsonable(session_cards(disputed)),
            "org_id": org_id,
        }

    @app.get(f"{API}/notes/{{note_id}}/history")
    def wiki_note_history(
        note_id: str, org_id: str = "", manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        sess = _session(manthana_admin)
        org_id = _org(sess, org_id)
        versions = store.note_history(note_id, org_id)
        if not versions:
            raise ApiError(404, "note not found in this org")
        return {"versions": _jsonable(versions), "org_id": org_id}

    # ── teaching: human writes that outrank the AI ───────────────────────
    class EditBody(BaseModel):
        org_id: str = ""
        title: str
        body: str

    class CreateBody(BaseModel):
        org_id: str = ""
        kind: str
        title: str
        body: str
        project: str = ""

    class OrgBody(BaseModel):
        org_id: str = ""

    class RevertBody(BaseModel):
        org_id: str = ""
        to_version_id: str

    @app.post(f"{API}/notes")
    def wiki_create(
        body: CreateBody, manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """Add knowledge the sessions never produced — what was only in a head."""
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        try:
            kind = NoteKind(body.kind)
        except ValueError:
            raise ApiError(422, f"unknown note kind: {body.kind}") from None
        if not body.title.strip() or not body.body.strip():
            raise ApiError(422, "title and body are required")
        note = create(
            store, org_id, kind=kind, title=body.title, body=body.body,
            author=sess.author, project=body.project,
        )
        return {"note": _jsonable(note), "org_id": org_id}

    @app.post(f"{API}/notes/{{note_id}}/edit")
    def wiki_edit(
        note_id: str, body: EditBody, manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """Correct a claim. The saved version is human-authored and
        authoritative — the consolidator may dispute it, never overwrite it."""
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        try:
            note = edit(
                store, org_id, note_id, title=body.title, body=body.body, author=sess.author
            )
        except NoteNotFoundError:
            raise ApiError(404, "note not found in this org") from None
        return {"note": _jsonable(note), "org_id": org_id}

    @app.post(f"{API}/notes/{{note_id}}/confirm")
    def wiki_confirm(
        note_id: str, body: OrgBody, manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """Vouch for an AI note as-is — trust changes, the text does not."""
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        try:
            note = confirm(store, org_id, note_id, author=sess.author)
        except NoteNotFoundError:
            raise ApiError(404, "note not found in this org") from None
        return {"note": _jsonable(note), "org_id": org_id}

    @app.post(f"{API}/notes/{{note_id}}/revert")
    def wiki_revert(
        note_id: str, body: RevertBody, manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """Undo a bad edit by restoring earlier text as a NEW human version."""
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        try:
            note = revert(
                store, org_id, note_id, to_version_id=body.to_version_id, author=sess.author
            )
        except NoteNotFoundError:
            raise ApiError(404, "note not found in this org") from None
        return {"note": _jsonable(note), "org_id": org_id}

    # ── ask ──────────────────────────────────────────────────────────────
    class AskBody(BaseModel):
        org_id: str = ""
        query: str = Field(min_length=1)

    @app.post(f"{API}/ask")
    def wiki_ask(
        body: AskBody, manthana_admin: Annotated[str, Cookie()] = ""
    ) -> Response:
        """Ask the wiki. Notes answer first; sessions are read only when the
        notes don't cover it — so citations name whichever the answer used."""
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        model = _provider(org_id)
        if model is None:
            raise ApiError(503, "no model provider configured")
        try:
            result = ask(store, config, org_id=org_id, query=body.query, provider=model)
        except QuotaExceededError as exc:
            return JSONResponse(
                {
                    "detail": "monthly AI quota reached for this org",
                    "spent_usd": exc.spent_usd,
                    "cap_usd": exc.cap_usd,
                },
                status_code=429,
            )
        store.record_founder_query(
            org_id=org_id, query=body.query, insufficient=result.insufficient_data,
            citations=result.citations, individual=True,
        )
        # Citations are resolved to real objects here rather than returned as
        # bare ids: the client renders them as cards, and a second fetch per
        # citation would make every answer N+1.
        notes = [n for n in (store.get_note(i, org_id) for i in result.note_citations) if n]
        comps = [
            c for c in (store.get_compaction(i, org_id) for i in result.compaction_citations) if c
        ]
        return JSONResponse(
            {
                "query": body.query,
                "narrative": result.narrative,
                "coverage": result.coverage_note(),
                "insufficient_data": result.insufficient_data,
                "drilled": result.drilled,
                "notes": _jsonable(notes),
                "sessions": _jsonable(session_cards(comps)),
                "org_id": org_id,
            }
        )


def _since_days(days: int) -> str:
    from datetime import UTC, timedelta

    return (datetime.now(UTC) - timedelta(days=max(1, days))).isoformat()


__all__ = ["API", "GRAPH_WINDOW_DAYS", "PAGE", "ApiError", "mount_wiki_api"]
