"""Skill miner tests — embeddings, clustering, SKILL.md format, synthesis,
provenance, and the end-to-end miner. Deterministic (HashingEmbedder + a mock /
no LLM), so no torch or model access is needed.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from manthana.agent.llm import MockProvider
from manthana.agent.skillminer import (
    HashingEmbedder,
    SkillMiner,
    cluster_compactions,
    community_detection,
    make_provenance,
    recurring,
    render_skill_md,
    validate_draft,
    write_proposal,
)
from manthana.agent.skillminer.embed import cosine
from manthana.agent.skillminer.skillmd import (
    SkillDraft,
    repair_draft,
    slugify_name,
    validate_description,
    validate_name,
)
from manthana.agent.skillminer.synthesize import fallback_draft, synthesize
from manthana.schemas import EngineeringCompaction, Outcome, Surface

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _comp(
    cid: str, session: str, actor: str, intent: str, approach: str = "do it"
) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=session,
        actor=actor,
        surface=Surface.claude_code,
        project="demo",
        started_at=_T0,
        ended_at=_T0,
        duration_seconds=1.0,
        task_intent=intent,
        approach=approach,
        outcome=Outcome.success,
    )


# ── embeddings ────────────────────────────────────────────────────────────
def test_hashing_embedder_similar_texts_cluster_together() -> None:
    e = HashingEmbedder()
    a, b, c = e.embed(
        ["fix flaky pytest timeout", "fix the flaky pytest timeout error", "design a logo in figma"]
    )
    assert cosine(a, b) > cosine(a, c)
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6  # L2-normalized


# ── clustering ────────────────────────────────────────────────────────────
def test_community_detection_groups_and_separates() -> None:
    e = HashingEmbedder()
    vecs = e.embed(
        [
            "fix flaky pytest timeout",
            "fix flaky pytest timeout again",
            "fix flaky pytest timeout once more",
            "unrelated brand color palette work",
        ]
    )
    clusters = community_detection(vecs, threshold=0.5, min_community_size=2)
    assert any(len(c) >= 3 for c in clusters)  # the three pytest items group
    assert all(3 not in c for c in clusters if len(c) >= 3)  # the unrelated one excluded


def test_recurrence_gate_requires_distinct_sessions() -> None:
    # same engineer, same problem, but only across 2 sessions -> below floor of 3
    comps = [
        _comp("c1", "s1", "eng", "fix flaky pytest timeout"),
        _comp("c2", "s1", "eng", "fix flaky pytest timeout"),  # same session
        _comp("c3", "s2", "eng", "fix flaky pytest timeout"),
    ]
    clusters = cluster_compactions(comps, HashingEmbedder(), threshold=0.5)
    assert recurring(clusters, min_sessions=3) == []  # only 2 distinct sessions
    assert recurring(clusters, min_sessions=2)  # 2 sessions clears a floor of 2


# ── SKILL.md format ───────────────────────────────────────────────────────
def test_name_validation() -> None:
    assert validate_name("fix-flaky-tests") == []
    assert validate_name("Fix Tests")  # uppercase + space invalid
    assert validate_name("claude-helper")  # reserved word
    assert validate_name("x" * 65)  # too long


def test_description_validation() -> None:
    assert validate_description("Fixes flaky tests; use when pytest times out.") == []
    assert validate_description("")  # empty invalid
    assert validate_description("has <xml> tag")  # XML tags invalid
    assert validate_description("x" * 1025)  # too long


def test_repair_and_render() -> None:
    draft = repair_draft(SkillDraft(name="Fix Claude Tests!", description="ok <b>x</b>", body="do"))
    assert validate_name(draft.name) == []  # slugified + reserved word removed
    assert "claude" not in draft.name
    assert "<" not in draft.description
    md = render_skill_md(draft)
    assert md.startswith("---\n")
    assert f"name: {draft.name}\n" in md
    assert "description: \"" in md


def test_slugify_fallback() -> None:
    assert slugify_name("") == "mined-skill"
    assert slugify_name("Fix Flaky Tests") == "fix-flaky-tests"


# ── synthesis ─────────────────────────────────────────────────────────────
def _cluster():
    comps = [
        _comp("c1", "s1", "eng", "fix flaky pytest timeout"),
        _comp("c2", "s2", "eng", "fix flaky pytest timeout"),
        _comp("c3", "s3", "eng", "fix flaky pytest timeout"),
    ]
    return cluster_compactions(comps, HashingEmbedder(), threshold=0.5)[0]


def test_synthesize_with_llm_produces_valid_draft() -> None:
    good = json.dumps(
        {
            "name": "fix-flaky-tests",
            "description": "Stabilizes flaky tests; use when CI tests time out intermittently.",
            "body": "## Steps\n\n1. Reproduce.\n2. Add retry/await.\n",
        }
    )
    draft = synthesize(_cluster(), MockProvider(good))
    assert draft.name == "fix-flaky-tests"
    assert validate_draft(draft) == []


def test_synthesize_falls_back_on_garbage_or_no_llm() -> None:
    assert validate_draft(synthesize(_cluster(), MockProvider("not json"))) == []
    assert validate_draft(synthesize(_cluster(), None)) == []  # offline fallback


def test_fallback_draft_is_always_valid() -> None:
    assert validate_draft(fallback_draft(_cluster())) == []


# ── provenance ────────────────────────────────────────────────────────────
def test_provenance_records_evidence_and_hashes() -> None:
    cluster = _cluster()
    md = render_skill_md(fallback_draft(cluster))
    prov = make_provenance(cluster, md, now=_T0)
    assert prov.source == "manthana-skill-miner"
    assert prov.session_count == 3
    assert set(prov.evidence) == {"c1", "c2", "c3"}
    assert prov.content_hash.startswith("sha256:")
    assert make_provenance(cluster, md, now=_T0).content_hash == prov.content_hash  # deterministic
    # k-anonymized variant drops contributor names
    assert make_provenance(cluster, md, now=_T0, include_contributors=False).contributors is None


# ── end-to-end miner ──────────────────────────────────────────────────────
def test_miner_proposes_and_writes_skill(tmp_path: Path) -> None:
    comps = [
        _comp("c1", "s1", "eng", "fix flaky pytest timeout"),
        _comp("c2", "s2", "eng", "fix flaky pytest timeout"),
        _comp("c3", "s3", "eng", "fix flaky pytest timeout"),
        _comp("c4", "s4", "eng", "completely unrelated brand palette"),
    ]
    miner = SkillMiner(embedder=HashingEmbedder(), provider=None, threshold=0.5)
    proposals = miner.mine(comps, min_sessions=3, now=_T0)
    assert len(proposals) == 1  # only the recurring pytest pattern (3 sessions)
    p = proposals[0]
    assert validate_draft(p.draft) == []

    out = write_proposal(p, tmp_path)
    assert (out / "SKILL.md").read_text().startswith("---\n")
    assert json.loads((out / "provenance.json").read_text())["session_count"] == 3
