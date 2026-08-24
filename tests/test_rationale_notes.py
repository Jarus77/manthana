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

from manthana.schemas import EngineeringCompaction, NoteKind, NoteSource, Outcome, Surface
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
