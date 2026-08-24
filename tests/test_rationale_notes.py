"""What a HUMAN contributed, as durable knowledge.

Every other note kind records what the work established. This one records what a
person knew — the judgment, constraint or correction a session would not have
reached on its own. It exists because that is the one thing in the system that
cannot be re-derived: run the same session again and the agent's output comes
back, the person's reasoning does not.

The two properties worth protecting:

  * steering is not rationale — "go", "yes", "continue" carry no judgment, and a
    pass that mints a note per session has started calling them judgment;
  * a claim with no verbatim quote is dropped, because a paraphrase nobody can
    check against the original is exactly the failure this design risks.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

from manthana.schemas import (
    EngineeringCompaction,
    KnowledgeNote,
    NoteKind,
    NoteSource,
    Outcome,
    Surface,
)
from manthana.server.enrich import build_rationale_notes

_NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _comp() -> EngineeringCompaction:
    return EngineeringCompaction(
        id="c1",
        session_id="s1",
        actor="priya@acme.com",
        surface=Surface.claude_code,
        project="checkout",
        started_at=_NOW,
        ended_at=_NOW,
        duration_seconds=60.0,
        task_intent="fix the retry ladder",
        approach="swapped the backoff",
        outcome=Outcome.success,
        released=True,
        source="full",
    )


def test_a_rationale_carries_both_the_claim_and_the_words_it_came_from() -> None:
    notes = build_rationale_notes(
        _comp(),
        {
            "human_rationale": [
                {
                    "claim": "Exponential backoff is wrong here because the processor "
                    "charges per attempt.",
                    "quote": "no — back off linearly, the processor bills every retry",
                    "concepts": ["retries", "billing"],
                }
            ]
        },
        now=_NOW,
    )
    assert len(notes) == 1
    note = notes[0]
    assert note.kind == NoteKind.rationale
    # The claim is what makes it findable…
    assert "processor charges per attempt" in note.title
    # …the quote is what makes it checkable. Losing this was the known risk of
    # writing these from a model's reading rather than from the words themselves.
    assert "the processor bills every retry" in note.body
    assert note.evidence == ["c1"]
    # Attributed, never anonymised.
    assert note.actors == ["priya@acme.com"]
    assert note.source == NoteSource.ai
    assert note.entities.projects == ["checkout"]


def test_a_claim_with_no_quote_is_dropped_not_kept() -> None:
    """The failure mode this design is most exposed to: a confident paraphrase
    with nothing to check it against. Better to lose the claim."""
    notes = build_rationale_notes(
        _comp(),
        {"human_rationale": [{"claim": "They decided to use a composite key.", "quote": ""}]},
        now=_NOW,
    )
    assert notes == []


def test_nothing_is_minted_when_the_model_finds_no_judgment() -> None:
    """The common and correct answer. Most sessions are steering plus work."""
    payloads: list[dict[str, object]] = [
        {},
        {"human_rationale": []},
        {"human_rationale": "nope"},
    ]
    for payload in payloads:
        assert build_rationale_notes(_comp(), payload, now=_NOW) == []


def test_the_prompt_tells_the_model_that_steering_is_not_rationale() -> None:
    """Belt and braces on the field's whole failure mode. If these instructions
    are ever dropped the wiki fills with "go"/"continue" as knowledge, and the
    only signal would be a rationale count that suddenly matches the session
    count."""
    from manthana.server.enrich.prompt import _INSTRUCTIONS

    assert 'role="user"' in _INSTRUCTIONS
    for steering in ("continue", "ship it", "looks good"):
        assert steering in _INSTRUCTIONS
    assert "verbatim" in _INSTRUCTIONS
    assert "Return [] rather than stretching" in _INSTRUCTIONS


def test_rationale_is_browsable_but_the_adjudicator_cannot_mint_one() -> None:
    """Ownership shape, borrowed from project_overview: one pass writes it. But
    unlike an overview it IS knowledge a reader browses, and it leads the page —
    what a person knew is the scarcest thing on it."""
    from manthana.server.consolidate import ADJUDICABLE_KINDS
    from manthana.server.pages import SECTION_ORDER

    assert NoteKind.rationale not in ADJUDICABLE_KINDS
    assert SECTION_ORDER[0] == NoteKind.rationale


def test_a_rambling_claim_is_clipped_like_every_other_note() -> None:
    """`quote` is bounded where it is read, but `claim` is free text from a model
    and nothing else on this path would stop it. Every other writer clips
    (consolidate._clip_body, overview._clip); this one has to as well, or one bad
    generation puts an unbounded body in the store."""
    from manthana.schemas import body_char_cap

    cap = body_char_cap(NoteKind.rationale)
    notes = build_rationale_notes(
        _comp(),
        {"human_rationale": [{"claim": "x" * (cap * 3), "quote": "because I said so"}]},
        now=_NOW,
    )
    assert len(notes) == 1
    assert len(notes[0].body) <= cap
    assert len(notes[0].title) <= 200


# ── the review's blockers, each pinned so it cannot come back ──────────────
def test_the_adjudicator_is_never_handed_a_note_it_may_not_create() -> None:
    """ADJUDICABLE_KINDS gated CREATION only. Candidates were unfiltered, so a
    `refines` verdict could replace a rationale's body — destroying the verbatim
    quote while still labelling it a quote — and `supports` could graft a second
    engineer's name onto words they never said. The "one law" is no protection:
    it exempts source==human, and every kind reachable here is source==ai.

    This also closed a PRE-EXISTING hole of the same shape on project_overview,
    where a per-session verdict could replace a whole project article outside the
    overview pass's version chain."""
    from manthana.server.consolidate import ADJUDICABLE_KINDS, retrieve_candidates

    notes = [
        KnowledgeNote(
            id=f"kn-{k}", org_id="o1", kind=k, title="t", body="b",
            scope="project:checkout", evidence=["c1"], created_at=_NOW, updated_at=_NOW,
        )
        for k in (NoteKind.decision, NoteKind.rationale, NoteKind.project_overview)
    ]

    class _Store:
        def query_notes(self, org_id: str, **kw: object) -> list[KnowledgeNote]:
            return list(notes)

    class _Cfg:
        consolidate_note_scan = 500
        consolidate_top_k = 8

    class _Embedder:
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("degrade to entity overlap")

    got = retrieve_candidates(
        _Store(), _Cfg(), _comp(), org_id="o1", embedder=_Embedder()  # type: ignore[arg-type]
    )
    kinds = {n.kind for n in got}
    assert NoteKind.rationale not in kinds
    assert NoteKind.project_overview not in kinds
    assert kinds <= set(ADJUDICABLE_KINDS)


def test_a_quote_can_never_be_minted_from_a_prompt_that_showed_no_turns() -> None:
    """~14% of sessions carry a native summary, and on that path `turns` is empty
    — the model is asked for a VERBATIM quote from text it was never shown, so it
    can only invent one. "A quote is present" does not catch an invention, so the
    field is not honoured there at all. Publishing fabricated words in a named
    engineer's voice is worse than publishing nothing."""
    import inspect

    from manthana.server.enrich import enricher

    src = inspect.getsource(enricher.enrich_org)
    assert "config.enable_rationale_notes and not summary" in src


def test_one_session_cannot_flood_the_page() -> None:
    payload: dict[str, object] = {
        "human_rationale": [
            {"claim": f"claim {i}", "quote": f"quote {i}"} for i in range(25)
        ]
    }
    assert len(build_rationale_notes(_comp(), payload, now=_NOW, limit=3)) == 3


def test_a_multiline_quote_stays_inside_the_blockquote() -> None:
    """The body renders through react-markdown. An unprefixed second line escapes
    the quote and reads as the claim; a line starting "#" or "-" becomes a heading
    or a list inside someone else's article."""
    notes = build_rationale_notes(
        _comp(),
        {"human_rationale": [{"claim": "C", "quote": "first line\n# not a heading\n- not a list"}]},
        now=_NOW,
    )
    quoted = notes[0].body.split("\n\n", 1)[1]
    assert all(line.startswith("> ") for line in quoted.splitlines())


def test_purging_a_session_removes_the_words_that_were_said() -> None:
    """Going stale is enough for a note of model prose about the work. It is not
    enough for a note whose body is a literal copy of what someone typed: purging
    is how that is removed, and a stale badge over their own sentence still leaves
    the sentence on the page."""
    from manthana.server import ServerStore

    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "T")
    store.ingest_compaction(_comp(), org_id="o1", team_id="t1")
    note = build_rationale_notes(
        _comp(), {"human_rationale": [{"claim": "C", "quote": "what I actually typed"}]},
        now=_NOW,
    )[0]
    store.upsert_note(note.model_copy(update={"org_id": "o1"}))
    assert store.get_note(note.id, "o1") is not None

    store.delete_compactions("o1", ["c1"])
    assert store.get_note(note.id, "o1") is None, "the quoted words survived a purge"
