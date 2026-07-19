"""Org wiki — the shared, browsable, teachable context layer.

Mounted alongside ``mount_ui`` and sharing its cookie session exactly (the
console cookie is scoped ``path='/ui'``, so every route here lives under
``/ui/...`` or it would not authenticate at all). Tenant isolation is the same
one implementation: ``session_for`` + ``scope_org`` — a founder is forced to
their own org regardless of what the URL asks for.

Pages are projections (see ``pages.py``), not documents. Read routes render
them; the teaching routes (edit/confirm/revert) write ``source="human"`` note
versions that the AI consolidator may dispute but never overwrite.

NOTE: like ``ui.py``/``app.py``, this module intentionally does NOT use ``from
__future__ import annotations`` — FastAPI must resolve ``Form``/``Cookie``
parameter annotations at runtime.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from manthana.schemas import KnowledgeNote, NoteKind, NoteSource, NoteStatus

from .ask import ask
from .config import ServerConfig
from .llm import LLMProvider
from .metering import QuotaExceededError
from .pages import note_page, org_home, person_page, project_page
from .store import ServerStore
from .teach import NoteNotFoundError, confirm, create, edit, revert
from .ui import ConsoleSession, _e, _page, _quota_page, scope_org, session_for

_log = logging.getLogger(__name__)

# Status/source badges. The editorial contract is legible at a glance: amber =
# an AI wrote this and nobody has checked it; green = a human vouched for it;
# red = evidence disagrees.
_BADGE_STYLE = {
    NoteStatus.candidate: ("unreviewed", "#a60"),
    NoteStatus.established: ("established", "#555"),
    NoteStatus.disputed: ("disputed", "#c00"),
    NoteStatus.stale: ("stale", "#888"),
    NoteStatus.superseded: ("superseded", "#888"),
}


def _badge(text: str, color: str) -> str:
    return (
        f"<span style='display:inline-block;padding:1px 6px;margin-right:4px;"
        f"border:1px solid {color};color:{color};border-radius:3px;font-size:11px'>"
        f"{_e(text)}</span>"
    )


def _badges(note: KnowledgeNote) -> str:
    out = ""
    label, color = _BADGE_STYLE.get(note.status, (str(note.status), "#555"))
    out += _badge(label, color)
    if note.source == NoteSource.human:
        out += _badge("human", "#06c")
    if note.confirmed_by:
        out += _badge("confirmed", "#0a0")
    if note.version > 1:
        out += _badge(f"v{note.version}", "#888")
    return out


def _note_link(note: KnowledgeNote, org_id: str) -> str:
    return f"<a href='/ui/note/{_e(note.id)}?org_id={_e(org_id)}'>{_e(note.title)}</a>"


def _author(sess: ConsoleSession) -> str:
    """Who a human write is attributed to — an engineer's own actor id when they
    have one, else the role. This is what makes team teaching auditable: the
    history shows which colleague corrected a claim, not just "someone"."""
    return sess.author


def _confirm_button(note: KnowledgeNote, org_id: str, back: str) -> str:
    """Offered only while a claim is unvouched — once confirmed, the badge says so."""
    if note.confirmed_by or note.source == NoteSource.human:
        return ""
    return (
        "<form method='post' action='/ui/note/confirm'>"
        f"<input type='hidden' name='org_id' value='{_e(org_id)}'>"
        f"<input type='hidden' name='note_id' value='{_e(note.id)}'>"
        f"<input type='hidden' name='back' value='{_e(back)}'>"
        "<button title='Mark this as correct — it becomes authoritative for future "
        "answers'>Confirm</button></form> "
    )


def _edit_form(note: KnowledgeNote, org_id: str, back: str) -> str:
    """The teaching surface: correcting the text here makes it authoritative
    everywhere — pages, Q&A, and against the AI consolidator."""
    return (
        "<details><summary>Edit this note</summary>"
        "<form method='post' action='/ui/note/edit'>"
        f"<input type='hidden' name='org_id' value='{_e(org_id)}'>"
        f"<input type='hidden' name='note_id' value='{_e(note.id)}'>"
        f"<input type='hidden' name='back' value='{_e(back)}'>"
        f"<p><input name='title' size='60' value='{_e(note.title)}'></p>"
        f"<p><textarea name='body' rows='6' cols='80'>{_e(note.body)}</textarea></p>"
        "<button>Save as authoritative</button> "
        "<span class='muted'>Saved edits outrank anything the AI writes later.</span>"
        "</form></details>"
    )


def _new_note_form(org_id: str, back: str, *, project: str = "") -> str:
    kinds = "".join(f"<option value='{_e(k)}'>{_e(k)}</option>" for k in NoteKind)
    return (
        "<details><summary>+ add a note</summary>"
        "<form method='post' action='/ui/note/new'>"
        f"<input type='hidden' name='org_id' value='{_e(org_id)}'>"
        f"<input type='hidden' name='project' value='{_e(project)}'>"
        f"<input type='hidden' name='back' value='{_e(back)}'>"
        f"<p><select name='kind'>{kinds}</select> "
        "<input name='title' size='50' placeholder='what is true'></p>"
        "<p><textarea name='body' rows='5' cols='80' "
        "placeholder='why, and what follows from it'></textarea></p>"
        "<button>Add</button></form></details>"
    )


def _note_block(note: KnowledgeNote, org_id: str, back: str = "") -> str:
    """One note as it appears on a page: badges, title, body, provenance.

    The body is HTML-escaped — note text is written by both a model and humans
    and must never be able to inject markup into the console.
    """
    cites = (
        f"<span class='muted'>{len(note.evidence)} session(s)</span>"
        if note.evidence
        else "<span class='muted'>no evidence</span>"
    )
    metric = (
        f" <span class='muted'>· {_e(note.metric)}: <b>{_e(note.value)}</b></span>"
        if note.metric and note.value
        else ""
    )
    actions = _confirm_button(note, org_id, back) if back else ""
    return (
        "<div style='margin:0 0 1rem;padding:.6rem;border:1px solid #eee;border-radius:6px'>"
        f"<div>{_badges(note)}{_note_link(note, org_id)}{metric}</div>"
        f"<pre>{_e(note.body)}</pre>"
        f"<div class='muted'>{cites} · updated {_e(str(note.updated_at)[:16])} {actions}</div>"
        "</div>"
    )


def _org_picker(store: ServerStore, sess: ConsoleSession, org_id: str) -> str:
    """Admins pick an org; founders and engineers see their own and no chooser."""
    if sess.org_id is not None:
        who = f" · signed in as {_e(sess.actor)}" if sess.actor else ""
        return f"<p class='muted'>org: {_e(org_id)}{who}</p>"
    options = "".join(
        f"<option value='{_e(o.id)}'{' selected' if o.id == org_id else ''}>{_e(o.name)}</option>"
        for o in store.list_orgs()
    )
    return (
        "<div class='bar'><form method='get' action='/ui/home'>"
        f"<select name='org_id'>{options or '<option>—</option>'}</select> "
        "<button>Switch org</button></form></div>"
    )


def _default_org(store: ServerStore, sess: ConsoleSession, requested: str) -> str:
    """A founder's own org; else what was asked for; else the first org so the
    admin's first visit isn't an empty page."""
    org_id = scope_org(sess, requested)
    if org_id:
        return org_id
    orgs = store.list_orgs()
    return orgs[0].id if orgs else ""


def mount_wiki_ui(
    app: FastAPI,
    config: ServerConfig,
    store: ServerStore,
    provider: LLMProvider | None = None,
    provider_for: Callable[[str], LLMProvider] | None = None,
) -> None:
    def _sess(cookie: str) -> ConsoleSession | None:
        return session_for(config, cookie)

    def _provider(org_id: str) -> LLMProvider | None:
        # Per-org metered provider when the app supplies one (hosted quotas);
        # the shared provider otherwise.
        if provider_for is not None:
            return provider_for(org_id)
        return provider

    @app.get("/ui/home", response_class=HTMLResponse)
    def wiki_home(org_id: str = "", manthana_admin: Annotated[str, Cookie()] = "") -> Response:
        """This week across the org — the founder's 30-second scan."""
        sess = _sess(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _default_org(store, sess, org_id)
        if not org_id:
            return HTMLResponse(_page("Home", "<p>No orgs yet.</p>"))
        feed = org_home(store, org_id)

        projects = "".join(
            f"<tr><td><a href='/ui/page/project/{_e(r.project)}?org_id={_e(org_id)}'>"
            f"{_e(r.project)}</a></td>"
            f"<td>{r.sessions}</td>"
            f"<td>{_e(', '.join(a.split('@')[0] for a in r.actors))}</td>"
            f"<td class='muted'>{_e(r.top_intent[:70])}</td>"
            f"<td class='muted'>{_e(str(r.last_active)[:16])}</td></tr>"
            for r in feed.projects
        )
        people = "".join(
            f"<tr><td><a href='/ui/page/person/{_e(a.actor)}?org_id={_e(org_id)}'>"
            f"{_e(a.actor.split('@')[0])}</a></td>"
            f"<td>{a.sessions}</td><td>{_e(', '.join(a.projects))}</td>"
            f"<td class='muted'>{_e(a.intents[0][:70] if a.intents else '—')}</td></tr>"
            for a in feed.people
        )
        decisions = "".join(_note_block(n, org_id) for n in feed.new_decisions[:8])
        benches = "".join(
            "<li>"
            + _badges(d.note)
            + _note_link(d.note, org_id)
            + (
                f" <b>{_e(d.previous_value)} → {_e(d.note.value)}</b>"
                if d.moved
                else (f" <b>{_e(d.note.value)}</b>" if d.note.value else "")
            )
            + "</li>"
            for d in feed.benchmarks[:8]
        )
        unreviewed = (
            f"<p class='muted'>{feed.unreviewed} unreviewed note(s) — "
            "AI-written, awaiting a human look.</p>"
            if feed.unreviewed
            else ""
        )
        body = (
            _org_picker(store, sess, org_id)
            + "<div class='bar'><form method='post' action='/ui/ask'>"
            f"<input type='hidden' name='org_id' value='{_e(org_id)}'>"
            "<input name='query' size='60' "
            "placeholder='ask anything — e.g. what is Suraj working on?'> "
            "<button>Ask</button></form></div>"
            f"<p class='muted'>since {_e(feed.since[:10])}</p>{unreviewed}"
            "<h3>Projects</h3><table><tr><th>project</th><th>sessions</th><th>who</th>"
            "<th>latest work</th><th>last active</th></tr>"
            f"{projects or '<tr><td colspan=5>nothing this week</td></tr>'}</table>"
            "<h3>Who's active</h3><table><tr><th>person</th><th>sessions</th>"
            "<th>projects</th><th>latest work</th></tr>"
            f"{people or '<tr><td colspan=4>nobody this week</td></tr>'}</table>"
            f"<h3>Benchmarks</h3><ul>{benches or '<li class=muted>none moved</li>'}</ul>"
            f"<h3>New decisions</h3>{decisions or '<p class=muted>none this week</p>'}"
        )
        return HTMLResponse(_page("Home", body))

    @app.post("/ui/ask", response_class=HTMLResponse)
    def wiki_ask(
        org_id: Annotated[str, Form()],
        query: Annotated[str, Form()],
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        """Ask the wiki. Answers from notes first, drilling to sessions only when
        the notes don't cover it — so citations link to whichever the answer used."""
        sess = _sess(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _default_org(store, sess, org_id)
        model = _provider(org_id)
        if model is None:
            return HTMLResponse(
                _page("Ask", "<p class='warn'>No model provider configured.</p>"),
                status_code=503,
            )
        try:
            result = ask(store, config, org_id=org_id, query=query, provider=model)
        except QuotaExceededError as exc:
            return HTMLResponse(_quota_page(exc, back=f"/ui/home?org_id={org_id}"), 429)
        store.record_founder_query(
            org_id=org_id, query=query, insufficient=result.insufficient_data,
            citations=result.citations, individual=True,
        )
        notes = "".join(
            f"<li><a href='/ui/note/{_e(nid)}?org_id={_e(org_id)}'>{_e(nid)}</a></li>"
            for nid in result.note_citations
        )
        sessions = "".join(
            f"<li><a href='/ui/session?org_id={_e(org_id)}&compaction_id={_e(cid)}'>"
            f"{_e(cid)}</a></li>"
            for cid in result.compaction_citations
        )
        cites = (
            (f"<h4>From notes</h4><ul>{notes}</ul>" if notes else "")
            + (f"<h4>From sessions</h4><ul>{sessions}</ul>" if sessions else "")
        ) or "<p class='muted'>no citations</p>"
        body = (
            f"<p class='muted'>{_e(query)} · org {_e(org_id)}</p>"
            f"<pre>{_e(result.narrative)}</pre>"
            f"<p class='muted'>{_e(result.coverage_note())}</p>"
            f"{cites}"
            "<div class='bar'><form method='post' action='/ui/ask'>"
            f"<input type='hidden' name='org_id' value='{_e(org_id)}'>"
            "<input name='query' size='60' placeholder='ask another'> "
            "<button>Ask</button></form></div>"
            f"<p><a href='/ui/home?org_id={_e(org_id)}'>← home</a></p>"
        )
        return HTMLResponse(_page("Ask", body))

    @app.get("/ui/page/project/{project}", response_class=HTMLResponse)
    def wiki_project(
        project: str, org_id: str = "", manthana_admin: Annotated[str, Cookie()] = ""
    ) -> Response:
        sess = _sess(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _default_org(store, sess, org_id)
        page = project_page(store, org_id, project)

        if page.rollup is None:
            head = "<p class='muted'>no sessions in the last 14 days</p>"
        else:
            r = page.rollup
            head = (
                f"<p class='muted'>{r.sessions} session(s) · "
                f"{_e(', '.join(a.split('@')[0] for a in r.actors))} · "
                f"outcomes {_e(r.outcome_mix)} · last active {_e(str(r.last_active)[:16])}</p>"
            )
        back = f"/ui/page/project/{project}?org_id={org_id}"
        sections = "".join(
            f"<h3>{_e(str(kind).replace('_', ' ').title())}</h3>"
            + "".join(_note_block(n, org_id, back) for n in notes)
            for kind, notes in page.sections
        )
        sessions = "".join(
            f"<tr><td class='muted'>{_e(str(c.started_at)[:16])}</td>"
            f"<td><a href='/ui/page/person/{_e(c.actor)}?org_id={_e(org_id)}'>"
            f"{_e(c.actor.split('@')[0])}</a></td>"
            f"<td><a href='/ui/session?org_id={_e(org_id)}&compaction_id={_e(c.id)}'>"
            f"{_e(c.task_intent[:70])}</a></td>"
            f"<td>{_e(c.outcome)}</td></tr>"
            for c in page.sessions[:30]
        )
        body = (
            f"<h2>{_e(project)}</h2>{head}"
            f"{sections or '<p class=muted>no notes yet for this project</p>'}"
            f"{_new_note_form(org_id, back, project=project)}"
            "<h3>Recent sessions</h3><table><tr><th>when</th><th>who</th>"
            "<th>what</th><th>outcome</th></tr>"
            f"{sessions or '<tr><td colspan=4>none</td></tr>'}</table>"
            f"<p><a href='/ui/home?org_id={_e(org_id)}'>← home</a></p>"
        )
        return HTMLResponse(_page(f"Project — {project}", body))

    @app.get("/ui/page/person/{actor}", response_class=HTMLResponse)
    def wiki_person(
        actor: str, org_id: str = "", manthana_admin: Annotated[str, Cookie()] = ""
    ) -> Response:
        sess = _sess(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _default_org(store, sess, org_id)
        page = person_page(store, org_id, actor)

        if page.activity is None:
            head = "<p class='muted'>no sessions in the last 14 days</p>"
        else:
            a = page.activity
            intents = "".join(f"<li>{_e(i)}</li>" for i in a.intents)
            head = (
                f"<p class='muted'>{a.sessions} session(s) · projects "
                f"{_e(', '.join(a.projects) or '—')} · outcomes {_e(a.outcome_mix)}</p>"
                f"<h3>Currently working on</h3><ul>{intents or '<li>—</li>'}</ul>"
            )
        notes = "".join(_note_block(n, org_id) for n in page.notes[:20])
        sessions = "".join(
            f"<tr><td class='muted'>{_e(str(c.started_at)[:16])}</td>"
            f"<td><a href='/ui/page/project/{_e(c.project)}?org_id={_e(org_id)}'>"
            f"{_e(c.project)}</a></td>"
            f"<td><a href='/ui/session?org_id={_e(org_id)}&compaction_id={_e(c.id)}'>"
            f"{_e(c.task_intent[:70])}</a></td>"
            f"<td>{_e(c.outcome)}</td></tr>"
            for c in page.sessions[:30]
        )
        body = (
            f"<h2>{_e(actor)}</h2>{head}"
            f"<h3>Their decisions &amp; findings</h3>"
            f"{notes or '<p class=muted>no notes cite this person yet</p>'}"
            "<h3>Recent sessions</h3><table><tr><th>when</th><th>project</th>"
            "<th>what</th><th>outcome</th></tr>"
            f"{sessions or '<tr><td colspan=4>none</td></tr>'}</table>"
            f"<p><a href='/ui/home?org_id={_e(org_id)}'>← home</a></p>"
        )
        return HTMLResponse(_page(f"Person — {actor}", body))

    @app.get("/ui/note/{note_id}", response_class=HTMLResponse)
    def wiki_note(
        note_id: str, org_id: str = "", manthana_admin: Annotated[str, Cookie()] = ""
    ) -> Response:
        sess = _sess(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _default_org(store, sess, org_id)
        found = note_page(store, org_id, note_id)
        if found is None:
            return HTMLResponse(
                _page("Note", "<p class='warn'>not found in this org</p>"), status_code=404
            )
        note, evidence, disputing = found

        def _sessions(items: list[object]) -> str:
            return "".join(
                f"<li><a href='/ui/session?org_id={_e(org_id)}&compaction_id={_e(c.id)}'>"  # type: ignore[attr-defined]
                f"{_e(c.task_intent[:70])}</a> "  # type: ignore[attr-defined]
                f"<span class='muted'>{_e(c.actor)} · {_e(str(c.started_at)[:10])}</span></li>"  # type: ignore[attr-defined]
                for c in items
            )

        dispute = (
            "<h4 class='warn'>Conflicting evidence</h4>"
            + (
                "<p class='muted'>An AI reading of these sessions disagrees with this note. "
                "The text above stands until a human resolves it.</p>"
                if note.source == NoteSource.human
                else "<p class='muted'>These sessions contradict this note.</p>"
            )
            + f"<ul>{_sessions(list(disputing))}</ul>"
            if note.disputed_by
            else ""
        )
        author = (
            f" · written by {_e(note.author)}"
            if note.author
            else " · written by consolidation"
        )
        back = f"/ui/note/{note.id}?org_id={org_id}"
        body = (
            f"<div>{_badges(note)}</div><h2>{_e(note.title)}</h2>"
            f"<p class='muted'>{_e(str(note.kind))} · {_e(note.scope)}{author} · "
            f"updated {_e(str(note.updated_at)[:16])}</p>"
            f"<pre>{_e(note.body)}</pre>"
            f"<p>{_confirm_button(note, org_id, back)}</p>"
            f"{_edit_form(note, org_id, back)}"
            f"{dispute}"
            f"<h4>Evidence</h4><ul>{_sessions(list(evidence)) or '<li>none</li>'}</ul>"
            f"<p><a href='/ui/note/{_e(note.id)}/history?org_id={_e(org_id)}'>"
            f"History ({note.version} version(s))</a> · "
            f"<a href='/ui/home?org_id={_e(org_id)}'>← home</a></p>"
        )
        return HTMLResponse(_page("Note", body))

    @app.get("/ui/note/{note_id}/history", response_class=HTMLResponse)
    def wiki_note_history(
        note_id: str, org_id: str = "", manthana_admin: Annotated[str, Cookie()] = ""
    ) -> Response:
        """The version chain. Nothing is ever deleted, so this is the full record
        of how a claim changed and who changed it."""
        sess = _sess(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _default_org(store, sess, org_id)
        chain = store.note_history(note_id, org_id)
        if not chain:
            return HTMLResponse(
                _page("History", "<p class='warn'>not found in this org</p>"), status_code=404
            )
        current = chain[0]

        def _revert_button(n: KnowledgeNote) -> str:
            # Reverting writes a NEW version rather than rewinding, so the
            # current version has nothing to restore to.
            if n.id == current.id:
                return "<span class='muted'>current</span>"
            return (
                "<form method='post' action='/ui/note/revert'>"
                f"<input type='hidden' name='org_id' value='{_e(org_id)}'>"
                f"<input type='hidden' name='note_id' value='{_e(current.id)}'>"
                f"<input type='hidden' name='to_version_id' value='{_e(n.id)}'>"
                "<button title='Restore this text as a new authoritative version'>"
                "Revert to this</button></form>"
            )

        rows = "".join(
            f"<tr><td>v{n.version}</td><td>{_badges(n)}</td>"
            f"<td class='muted'>{_e(n.author or str(n.source))}</td>"
            f"<td class='muted'>{_e(str(n.updated_at)[:16])}</td>"
            f"<td><pre>{_e(n.body)}</pre></td>"
            f"<td>{_revert_button(n)}</td></tr>"
            for n in chain
        )
        body = (
            f"<h2>History — {_e(current.title)}</h2>"
            "<p class='muted'>Nothing is ever deleted: an edit or a revert appends a "
            "new version, so what was published and what a human did about it both "
            "stay on the record.</p>"
            "<table><tr><th>version</th><th>status</th><th>author</th><th>when</th>"
            f"<th>body</th><th></th></tr>{rows}</table>"
            f"<p><a href='/ui/note/{_e(current.id)}?org_id={_e(org_id)}'>← note</a></p>"
        )
        return HTMLResponse(_page("History", body))

    # ── teaching: human writes that outrank the AI ───────────────────────
    def _teach_guard(
        cookie: str, org_id: str
    ) -> tuple[ConsoleSession, str] | Response:
        """Shared auth + tenant scoping for every write route."""
        sess = _sess(cookie)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        return sess, _default_org(store, sess, org_id)

    def _after(back: str, org_id: str) -> Response:
        # Redirect back to wherever the founder was teaching from, but only to a
        # console path — never to a caller-supplied absolute URL.
        target = back if back.startswith("/ui/") else f"/ui/home?org_id={org_id}"
        return RedirectResponse(url=target, status_code=303)

    @app.post("/ui/note/edit")
    def wiki_note_edit(
        org_id: Annotated[str, Form()],
        note_id: Annotated[str, Form()],
        title: Annotated[str, Form()],
        body: Annotated[str, Form()],
        back: Annotated[str, Form()] = "",
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        """Correct a claim. The saved version is human-authored and authoritative
        — the consolidator may later dispute it with evidence, but can never
        overwrite it."""
        guard = _teach_guard(manthana_admin, org_id)
        if isinstance(guard, Response):
            return guard
        sess, org_id = guard
        try:
            edit(store, org_id, note_id, title=title, body=body, author=_author(sess))
        except NoteNotFoundError:
            return HTMLResponse(
                _page("Edit", "<p class='warn'>not found in this org</p>"), status_code=404
            )
        return _after(back, org_id)

    @app.post("/ui/note/new")
    def wiki_note_new(
        org_id: Annotated[str, Form()],
        kind: Annotated[str, Form()],
        title: Annotated[str, Form()],
        body: Annotated[str, Form()],
        project: Annotated[str, Form()] = "",
        back: Annotated[str, Form()] = "",
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        """Add knowledge the sessions never produced — what was only in a head."""
        guard = _teach_guard(manthana_admin, org_id)
        if isinstance(guard, Response):
            return guard
        sess, org_id = guard
        try:
            note_kind = NoteKind(kind)
        except ValueError:
            return HTMLResponse(
                _page("Add note", "<p class='warn'>unknown note kind</p>"), status_code=422
            )
        if not title.strip() or not body.strip():
            return HTMLResponse(
                _page("Add note", "<p class='warn'>title and body are required</p>"),
                status_code=422,
            )
        create(
            store, org_id, kind=note_kind, title=title, body=body,
            author=_author(sess), project=project,
        )
        return _after(back, org_id)

    @app.post("/ui/note/confirm")
    def wiki_note_confirm(
        org_id: Annotated[str, Form()],
        note_id: Annotated[str, Form()],
        back: Annotated[str, Form()] = "",
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        """Vouch for an AI note as-is. Not a new version — nothing changed but
        the trust, and that is what later answers read as authoritative."""
        guard = _teach_guard(manthana_admin, org_id)
        if isinstance(guard, Response):
            return guard
        sess, org_id = guard
        try:
            confirm(store, org_id, note_id, author=_author(sess))
        except NoteNotFoundError:
            return HTMLResponse(
                _page("Confirm", "<p class='warn'>not found in this org</p>"), status_code=404
            )
        return _after(back, org_id)

    @app.post("/ui/note/revert")
    def wiki_note_revert(
        org_id: Annotated[str, Form()],
        note_id: Annotated[str, Form()],
        to_version_id: Annotated[str, Form()],
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        """Undo a bad edit by restoring earlier text as a NEW human version."""
        guard = _teach_guard(manthana_admin, org_id)
        if isinstance(guard, Response):
            return guard
        sess, org_id = guard
        try:
            new = revert(
                store, org_id, note_id, to_version_id=to_version_id, author=_author(sess)
            )
        except NoteNotFoundError:
            return HTMLResponse(
                _page("Revert", "<p class='warn'>not found in this org</p>"), status_code=404
            )
        return RedirectResponse(url=f"/ui/note/{new.id}?org_id={org_id}", status_code=303)


__all__ = ["mount_wiki_ui"]
