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
    OverviewStats,
    build_overview_note,
    contributors_hash,
    refresh_org_overviews,
)

_NOW = datetime(2026, 3, 1, tzinfo=UTC)
_GOOD = json.dumps(
    {
        "title": "scribe",
        "what_this_is": "scribe is a transcription service.",
        "current_state": [
            "Whisper wrapper wired to the API endpoint",
            "latency under two seconds on the eval set",
        ],
        "open_questions": ["should diarisation ship in v1?"],
        "change_summary": "wired the transcription endpoint",
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

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return self._inner.complete(prompt)


def _config(**kw: object) -> ServerConfig:
    return ServerConfig(jwt_secret="x" * 40, admin_token="adm", overview_min_sessions=2, **kw)  # type: ignore[arg-type]


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


def _run(store: ServerStore, provider: LLMProvider) -> OverviewStats:
    return refresh_org_overviews(
        store, provider, _config(), org_id="o1", limit=10, now=_NOW
    )


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
    latest = _overview(store)
    assert first is not None and latest is not None
    assert latest.id != first.id


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

    assert old is not None and new is not None
    assert new.supersedes == old.id and new.version == 2
    # Append-only: the old version survives as history.
    archived = store.get_note(old.id, "o1")
    assert archived is not None and archived.superseded_by == new.id


def test_a_human_description_is_never_regenerated_or_costed() -> None:
    """Stronger than consolidate's law, deliberately: there is nothing to
    dispute about a page's own description, so the human's version simply wins —
    and skipping before the call saves the money too."""
    from manthana.server.teach import edit

    store = _store(_comp("c1"), _comp("c2"))
    provider = _CountingProvider([_GOOD, _GOOD])
    _run(store, provider)
    ai = _overview(store)
    assert ai is not None

    edit(store, "o1", ai.id, title="scribe", body="Humans know best.", author="me@x.com")
    store.ingest_compaction(_comp("c3"), org_id="o1", team_id="t1")  # new work

    calls_before = provider.calls
    stats = _run(store, provider)
    assert provider.calls == calls_before, "a human description must not be re-costed"
    assert stats.skipped_human == 1
    current = _overview(store)
    assert current is not None
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
def test_build_overview_note_rejects_a_shapeless_payload() -> None:
    # No what_this_is or no current_state = no article.
    for bad in (
        {"title": "x"},
        {"what_this_is": "  ", "current_state": ["a"]},
        {"what_this_is": "a thing", "current_state": []},
    ):
        assert (
            build_overview_note(
                bad, prior=None, comps=[_comp("c1")], org_id="o1",
                project="scribe", now=_NOW,
            )
            is None
        )


def test_built_note_is_scoped_and_cites_its_evidence() -> None:
    comps = [_comp("c1"), _comp("c2")]
    note = build_overview_note(
        json.loads(_GOOD), prior=None, comps=comps, org_id="o1", project="scribe", now=_NOW
    )
    assert note is not None
    assert note.kind == NoteKind.project_overview
    assert note.scope == "project:scribe"
    assert set(note.evidence) == {"c1", "c2"}
    assert note.entities.libraries == ["whisper"]
    # The article structure is the body.
    assert "## What this is" in note.body and "## Current state" in note.body
    assert note.change_summary == "wired the transcription endpoint"


# ── the living-article discipline ────────────────────────────────────────
def test_current_state_is_rewritten_not_appended() -> None:
    """The key discipline from the product spec: 'Current state' is overwritten
    each update, which is what keeps the article at a few bullets forever
    instead of turning into another growing log."""
    second = json.dumps(
        {
            "what_this_is": "scribe is a transcription service.",
            "current_state": ["diarisation landed", "GPU batch path in review"],
            "open_questions": [],
            "change_summary": "diarisation landed",
        }
    )
    store = _store(_comp("c1"), _comp("c2"))
    provider = _CountingProvider([_GOOD, second])
    _run(store, provider)
    store.ingest_compaction(_comp("c3"), org_id="o1", team_id="t1")
    _run(store, provider)

    article = _overview(store)
    assert article is not None
    assert "diarisation landed" in article.body
    # The old bullets are GONE from the live article (still in the version chain).
    assert "Whisper wrapper wired" not in article.body


def test_changelog_falls_out_of_the_version_chain() -> None:
    """The changelog is the supersede chain's change_summary lines — append-only
    by construction, and it costs the article body nothing."""
    second = json.dumps(
        {
            "what_this_is": "scribe is a transcription service.",
            "current_state": ["diarisation landed"],
            "change_summary": "diarisation landed",
        }
    )
    store = _store(_comp("c1"), _comp("c2"))
    provider = _CountingProvider([_GOOD, second])
    _run(store, provider)
    store.ingest_compaction(_comp("c3"), org_id="o1", team_id="t1")
    _run(store, provider)

    current = _overview(store)
    assert current is not None
    history = store.note_history(current.id, "o1")
    summaries = [n.change_summary for n in sorted(history, key=lambda n: n.version)]
    assert summaries == ["wired the transcription endpoint", "diarisation landed"]


def test_article_body_may_exceed_the_ordinary_note_cap() -> None:
    from manthana.schemas import BODY_CHAR_CAP, OVERVIEW_BODY_CHAR_CAP

    long_bullets = [f"bullet {i}: " + "x" * 300 for i in range(6)]
    data = {
        "what_this_is": "a project with a lot of state.",
        "current_state": long_bullets,
        "change_summary": "big update",
    }
    note = build_overview_note(
        data, prior=None, comps=[_comp("c1")], org_id="o1", project="scribe", now=_NOW
    )
    assert note is not None
    assert len(note.body) > BODY_CHAR_CAP  # would have been clipped at 1600
    assert len(note.body) <= OVERVIEW_BODY_CHAR_CAP


def test_human_edit_of_an_article_survives_the_teach_clip() -> None:
    """A human correcting an article must not have their edit truncated at the
    ordinary 1600-char note cap."""
    from manthana.server.teach import edit

    store = _store(_comp("c1"), _comp("c2"))
    _run(store, _CountingProvider([_GOOD]))
    article = _overview(store)
    assert article is not None

    long_body = "## What this is\n\n" + "carefully written prose " + "y" * 2500
    edited = edit(store, "o1", article.id, title="scribe", body=long_body, author="me@x.com")
    assert len(edited.body) > 1600
    assert "…[truncated]" not in edited.body


def test_prior_article_is_fed_back_into_the_prompt() -> None:
    class _Capture(_CountingProvider):
        def __init__(self, responses):  # noqa: ANN001
            super().__init__(responses)
            self.prompts: list[str] = []

        def complete(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return super().complete(prompt)

    second = json.dumps(
        {
            "what_this_is": "scribe is a transcription service.",
            "current_state": ["new state"],
            "change_summary": "x",
        }
    )
    store = _store(_comp("c1"), _comp("c2"))
    provider = _Capture([_GOOD, second])
    _run(store, provider)
    store.ingest_compaction(_comp("c3"), org_id="o1", team_id="t1")
    _run(store, provider)
    assert "THE CURRENT ARTICLE" in provider.prompts[1]
    assert "Whisper wrapper wired" in provider.prompts[1]
