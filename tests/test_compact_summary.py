"""Reusing Claude Code's own compaction summaries.

Covers: the collector captures the NEWEST summary + skips summary/boundary lines
from turns; read_summary scans cheaply; the compactor uses the summary as a cheap
input and tags source=claude_summary; compact_session reads it on demand.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from manthana.agent.compact import compact_session
from manthana.agent.compactor import Compactor
from manthana.agent.compactor.prompt import build_prompt
from manthana.agent.llm import MockProvider
from manthana.agent.store import Store
from manthana.collectors import ClaudeCodeCollector
from manthana.schemas import Role, Session, Surface, Turn

_GOOD = json.dumps({"task_intent": "ship it", "approach": "patched", "outcome": "success"})


def _line(**kw: object) -> str:
    return json.dumps(kw)


def _transcript(path: Path) -> None:
    """A transcript with two Claude compaction summaries (newest = SUMMARY TWO)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _line(type="user", uuid="u1", parentUuid=None, timestamp="2026-06-01T10:00:00Z",
              cwd="/x", gitBranch="main", message={"role": "user", "content": "do thing"}),
        _line(type="assistant", uuid="a1", parentUuid="u1", timestamp="2026-06-01T10:01:00Z",
              message={"role": "assistant", "content": [{"type": "text", "text": "working"}],
                       "model": "claude", "usage": {"input_tokens": 10, "output_tokens": 5}}),
        _line(type="system", subtype="compact_boundary", uuid="b1",
              timestamp="2026-06-01T10:02:00Z", content="Conversation compacted",
              compactMetadata={"trigger": "auto", "preTokens": 255012}),
        _line(type="user", uuid="s1", parentUuid=None, isCompactSummary=True,
              isVisibleInTranscriptOnly=True, timestamp="2026-06-01T10:02:01Z",
              message={"role": "user", "content": "SUMMARY ONE: did X"}),
        _line(type="user", uuid="u2", parentUuid=None, timestamp="2026-06-01T10:03:00Z",
              message={"role": "user", "content": "continue"}),
        _line(type="user", uuid="s2", parentUuid=None, isCompactSummary=True,
              isVisibleInTranscriptOnly=True, timestamp="2026-06-01T10:04:00Z",
              message={"role": "user", "content": "SUMMARY TWO: did X and Y"}),
    ]
    path.write_text("\n".join(rows) + "\n")


def test_collector_captures_newest_summary_and_skips_summary_turns(tmp_path: Path) -> None:
    f = tmp_path / "proj" / "sess.jsonl"
    _transcript(f)
    turns, meta = ClaudeCodeCollector(actor="e@x.com").read(str(f))
    # newest summary captured + boundary metadata
    assert meta.compact_summary is not None
    assert meta.compact_summary.text == "SUMMARY TWO: did X and Y"
    assert meta.compact_summary.trigger == "auto"
    assert meta.compact_summary.pre_tokens == 255012
    # the summary text never leaks in as a (duplicate) turn
    joined = " ".join((t.content or "") for t in turns)
    assert "SUMMARY ONE" not in joined and "SUMMARY TWO" not in joined
    assert "do thing" in joined and "continue" in joined  # real turns survive


def test_read_summary_scans_cheaply(tmp_path: Path) -> None:
    f = tmp_path / "proj" / "sess.jsonl"
    _transcript(f)
    s = ClaudeCodeCollector().read_summary(str(f))
    assert s is not None and s.text == "SUMMARY TWO: did X and Y" and s.pre_tokens == 255012


def test_read_summary_none_when_absent(tmp_path: Path) -> None:
    f = tmp_path / "proj" / "plain.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(_line(type="user", uuid="u1", message={"role": "user", "content": "hi"}) + "\n")
    assert ClaudeCodeCollector().read_summary(str(f)) is None


# ── compactor uses the summary (cheap input) + tags source ───────────────────
def _session(sid: str = "s1") -> Session:
    return Session(
        id=sid, actor="e@x.com", surface=Surface.claude_code, project="demo",
        started_at=datetime(2026, 6, 1, tzinfo=UTC), turn_count=3,
    )


def _turns(sid: str = "s1", n: int = 60) -> list[Turn]:
    return [
        Turn(id=f"{sid}-{i}", session_id=sid, actor="e", seq=i, role=Role.user, content=f"t{i}")
        for i in range(n)
    ]


def test_build_prompt_uses_summary_and_recent_turns_only() -> None:
    prompt = build_prompt(_session(), _turns(n=60), claude_summary="PRIOR STATE")
    assert "PRIOR_SUMMARY" in prompt and "PRIOR STATE" in prompt
    assert "t59" in prompt  # recent turn included
    assert "t0" not in prompt  # old turn dropped (tail only)


def test_compact_tags_source_claude_summary() -> None:
    c = Compactor(MockProvider(_GOOD)).compact(_session(), _turns(), claude_summary="PRIOR STATE")
    assert c.source == "claude_summary"
    assert c.prompt_version.endswith("-summary")


def test_compact_full_path_tags_source_full() -> None:
    c = Compactor(MockProvider(_GOOD)).compact(_session(), _turns())
    assert c.source == "full"
    assert not c.prompt_version.endswith("-summary")


def test_summary_compaction_redacts_on_release_but_keeps_source() -> None:
    # A summary-based compaction is a normal compaction: it redacts on egress
    # (secret scrubbed) but the `source` metadata is preserved for the org.
    from manthana.agent.redaction import Redactor
    from manthana.schemas import EngineeringCompaction, Outcome, Surface

    comp = EngineeringCompaction(
        id="comp-x", session_id="x", actor="e@x.com", surface=Surface.claude_code,
        project="demo", started_at=datetime(2026, 6, 1, tzinfo=UTC),
        ended_at=datetime(2026, 6, 1, tzinfo=UTC), duration_seconds=1.0,
        task_intent="used key AKIAIOSFODNN7EXAMPLE to fetch data", approach="a",
        outcome=Outcome.success, source="claude_summary",
    )
    red = Redactor().redact_compaction(comp)
    assert red.source == "claude_summary"  # metadata kept
    assert "AKIAIOSFODNN7EXAMPLE" not in red.task_intent  # secret scrubbed before egress


def test_compact_session_uses_claude_summary(tmp_path: Path) -> None:
    f = tmp_path / "proj" / "sess.jsonl"
    _transcript(f)
    store = Store.open_memory()
    store.upsert_session(
        Session(
            id="s1", actor="e@x.com", surface=Surface.claude_code, project="demo",
            started_at=datetime(2026, 6, 1, tzinfo=UTC), turn_count=2,
            source_path=str(f), has_compact_summary=True,
        )
    )
    store.add_turns(_turns("s1", n=3))
    c = compact_session(store, "s1", provider=MockProvider(_GOOD))
    assert c is not None and c.source == "claude_summary"


def test_summary_attaches_to_only_the_slice_with_the_boundary() -> None:
    # The cumulative summary must NOT bleed onto every slice of a split file.
    # Two work-blocks (a 45-min gap splits them); the newest summary boundary sits
    # in the SECOND block, so only that slice may carry has_compact_summary.
    from datetime import timedelta

    from manthana.collectors import sessionize

    base = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)

    def t(i: int, mins: int) -> Turn:
        return Turn(
            id=f"t{i}", session_id="s", actor="e", seq=i, role=Role.user,
            content="x", timestamp=base + timedelta(minutes=mins),
        )

    turns = [t(0, 0), t(1, 5), t(2, 60), t(3, 65)]  # gap between idx 1 and 2 → split
    out = sessionize(
        turns, surface=Surface.claude_code, actor="e", project="p", repo_root=None,
        base_session_id="s", source_path=None, fallback_time=base,
        summary_at_index=3,  # boundary lives in the second slice
    )
    assert len(out) == 2
    assert out[0][0].has_compact_summary is False  # first block must NOT inherit it
    assert out[1][0].has_compact_summary is True  # only the slice with the boundary


def test_no_summary_index_flags_no_slice() -> None:
    from datetime import timedelta

    from manthana.collectors import sessionize

    base = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    turns = [
        Turn(id=f"t{i}", session_id="s", actor="e", seq=i, role=Role.user, content="x",
             timestamp=base + timedelta(minutes=i))
        for i in range(3)
    ]
    out = sessionize(
        turns, surface=Surface.claude_code, actor="e", project="p", repo_root=None,
        base_session_id="s", source_path=None, fallback_time=base, summary_at_index=None,
    )
    assert all(s.has_compact_summary is False for s, _ in out)
