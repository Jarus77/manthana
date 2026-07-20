"""Project overviews — what a project IS, as a versioned note.

A project slug is only ever the git repo directory name, so the wiki could say
nothing about `scribe` beyond "a project in the LSIITB organisation".

The two properties these tests exist to protect:

  * the pass regenerates when the WORK changed, not when the clock moved —
    otherwise ten projects on an hourly loop is ~87,000 model calls a year;
  * a human-written description is never regenerated and never even costed.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from manthana.schemas import EngineeringCompaction, NoteKind, NoteSource, Outcome, Surface
from manthana.server import ServerConfig, ServerStore
from manthana.server.llm import LLMProvider, ScriptedProvider
from manthana.server.overview import (
    build_overview_note,
    contributors_hash,
    refresh_org_overviews,
)

_NOW = datetime(2026, 3, 1, tzinfo=UTC)
_GOOD = json.dumps(
    {
        "title": "scribe",
        "body": "scribe is a transcription service.\n\nIt wraps a Whisper model.",
        "libraries": ["whisper"],
        "concepts": ["speech to text"],
    }
)


class _CountingProvider(LLMProvider):
    """Wraps a script and counts calls — the assertion that matters here is how
    often a model is invoked, not what it said."""

    name = "counting"

    def __init__(self, responses: list[str]) -> None:
        self._inner = ScriptedProvider(responses)
        self.calls = 0

    def complete(self, prompt: str, **kw: object) -> str:
        self.calls += 1
        return self._inner.complete(prompt, **kw)


def _config(**kw: object) -> ServerConfig:
    return ServerConfig(
        jwt_secret="x" * 40, admin_token="adm", overview_min_sessions=2, **kw
    )  # type: ignore[arg-type]


def _comp(cid: str, *, project: str = "scribe", days_ago: int = 1) -> EngineeringCompaction:
    at = _NOW - timedelta(days=days_ago)
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor="a@x.com",
        surface=Surface.claude_code,
        project=project,
        started_at=at,
        ended_at=at,
        duration_seconds=60.0,
        task_intent="wire up the transcription endpoint",
        approach="added a Whisper wrapper",
        outcome=Outcome.success,
        released=True,
        source="full",
        files_touched=["src/scribe/api.py"],
    )


def _store(*comps: EngineeringCompaction) -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "Platform")
    for c in comps:
        store.ingest_compaction(c, org_id="o1", team_id="t1")
    return store


def _run(store: ServerStore, provider: LLMProvider, **kw: object):
    return refresh_org_overviews(
        store, provider, _config(), org_id="o1", limit=10, now=_NOW, **kw
    )  # type: ignore[arg-type]


def _overview(store: ServerStore):
    notes = store.query_notes(
        "o1", kind=str(NoteKind.project_overview), exclude_superseded=True
    )
    return notes[0] if notes else None


# ── the hash is the cost control ─────────────────────────────────────────
def test_hash_is_order_independent_and_id_only() -> None:
    a, b = _comp("c1"), _comp("c2")
    assert contributors_hash([a, b]) == contributors_hash([b, a])
    # Re-enriching a session in place must NOT count as the project changing —
    # a reworded approach is not a change to what the project is.
    reworded = b.model_copy(update={"approach": "completely different wording"})
    assert contributors_hash([a, b]) == contributors_hash([a, reworded])


def test_second_pass_makes_no_model_call_when_nothing_changed() -> None:
    """The single most important assertion here. Without it, every interval
    re-describes every project forever."""
    store = _store(_comp("c1"), _comp("c2"))
    provider = _CountingProvider([_GOOD])
    _run(store, provider)
    assert provider.calls == 1 and _overview(store) is not None

    stats = _run(store, provider)
    assert provider.calls == 1, "an unchanged project must not be re-described"
    assert stats.skipped_unchanged == 1


def test_new_work_triggers_exactly_one_regeneration() -> None:
    store = _store(_comp("c1"), _comp("c2"))
    provider = _CountingProvider([_GOOD, _GOOD])
    _run(store, provider)
    first = _overview(store)

    store.ingest_compaction(_comp("c3"), org_id="o1", team_id="t1")
    _run(store, provider)
    assert provider.calls == 2
    assert _overview(store).id != first.id


def test_below_min_sessions_never_calls_the_model() -> None:
    store = _store(_comp("c1"))  # min is 2
    provider = _CountingProvider([_GOOD])
    _run(store, provider)
    assert provider.calls == 0 and _overview(store) is None


# ── versioning and the human law ─────────────────────────────────────────
def test_refresh_supersedes_the_previous_overview() -> None:
    store = _store(_comp("c1"), _comp("c2"))
    provider = _CountingProvider([_GOOD, _GOOD])
    _run(store, provider)
    old = _overview(store)

    store.ingest_compaction(_comp("c3"), org_id="o1", team_id="t1")
    _run(store, provider)
    new = _overview(store)

    assert new.supersedes == old.id and new.version == 2
    # Append-only: the old version survives as history.
    assert store.get_note(old.id, "o1").superseded_by == new.id


def test_a_human_description_is_never_regenerated_or_costed() -> None:
    """Stronger than consolidate's law, deliberately: there is nothing to
    dispute about a page's own description, so the human's version simply wins —
    and skipping before the call saves the money too."""
    from manthana.server.teach import edit

    store = _store(_comp("c1"), _comp("c2"))
    provider = _CountingProvider([_GOOD, _GOOD])
    _run(store, provider)
    ai = _overview(store)

    edit(store, "o1", ai.id, title="scribe", body="Humans know best.", author="me@x.com")
    store.ingest_compaction(_comp("c3"), org_id="o1", team_id="t1")  # new work

    calls_before = provider.calls
    stats = _run(store, provider)
    assert provider.calls == calls_before, "a human description must not be re-costed"
    assert stats.skipped_human == 1
    current = _overview(store)
    assert current.source == NoteSource.human and current.body == "Humans know best."


# ── failure handling ─────────────────────────────────────────────────────
def test_insufficient_records_state_and_does_not_retry() -> None:
    store = _store(_comp("c1"), _comp("c2"))
    provider = _CountingProvider([json.dumps({"insufficient": True}), _GOOD])
    stats = _run(store, provider)
    assert stats.insufficient == 1 and _overview(store) is None
    _run(store, provider)
    assert provider.calls == 1, "insufficient must record the hash so it stops asking"


def test_unusable_payload_is_a_bounded_failure_then_abandoned() -> None:
    store = _store(_comp("c1"), _comp("c2"))
    provider = _CountingProvider(["not json"] * 10)
    cfg = _config(overview_max_attempts=2)
    for _ in range(3):
        refresh_org_overviews(store, provider, cfg, org_id="o1", limit=10, now=_NOW)
    states = {r.project: r.state for r in store.list_overview_state("o1")}
    assert states.get("scribe") == "abandoned"


def test_junk_project_slugs_are_never_described() -> None:
    store = _store(_comp("c1", project="unknown"), _comp("c2", project="unknown"))
    provider = _CountingProvider([_GOOD])
    _run(store, provider)
    assert provider.calls == 0


# ── the pure builder ─────────────────────────────────────────────────────
def test_build_overview_note_rejects_an_empty_body() -> None:
    assert (
        build_overview_note(
            {"title": "x", "body": "   "},
            prior=None, comps=[_comp("c1")], org_id="o1", project="scribe", now=_NOW,
        )
        is None
    )


def test_built_note_is_scoped_and_cites_its_evidence() -> None:
    comps = [_comp("c1"), _comp("c2")]
    note = build_overview_note(
        json.loads(_GOOD), prior=None, comps=comps, org_id="o1", project="scribe", now=_NOW
    )
    assert note.kind == NoteKind.project_overview
    assert note.scope == "project:scribe"
    assert set(note.evidence) == {"c1", "c2"}
    assert note.entities.libraries == ["whisper"]
