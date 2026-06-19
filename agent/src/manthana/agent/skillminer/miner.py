"""Skill miner orchestrator: compactions → clusters → proposed SKILL.md.

v0 targets **personal** mining (the engineer's own compactions; recurrence gate =
>=N distinct sessions, 1 contributor) writing to ``~/.claude/skills/personal/``.
The same core powers **org-level** cross-engineer mining later (gate = >=4 distinct
contributors via the k-anonymity floor, ``include_contributors=False``).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from manthana.schemas import BaseCompaction

from ..llm import LLMProvider
from .cluster import (
    DEFAULT_MIN_CLUSTER_SIZE,
    DEFAULT_THRESHOLD,
    CompactionCluster,
    cluster_compactions,
    recurring,
)
from .embed import Embedder, default_embedder
from .provenance import Provenance, make_provenance, render_provenance
from .skillmd import SkillDraft, render_skill_md
from .synthesize import synthesize


@dataclass
class SkillProposal:
    draft: SkillDraft
    skill_md: str
    provenance: Provenance
    cluster: CompactionCluster


class SkillMiner:
    def __init__(
        self,
        *,
        embedder: Embedder | None = None,
        provider: LLMProvider | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    ) -> None:
        self.embedder = embedder or default_embedder()
        self.provider = provider
        self.threshold = threshold
        self.min_cluster_size = min_cluster_size

    def mine(
        self,
        compactions: Sequence[BaseCompaction],
        *,
        min_contributors: int = 1,
        min_sessions: int = 1,
        include_contributors: bool = True,
        now: datetime | None = None,
    ) -> list[SkillProposal]:
        now = now or datetime.now(UTC)
        clusters = cluster_compactions(
            compactions,
            self.embedder,
            threshold=self.threshold,
            min_cluster_size=self.min_cluster_size,
        )
        proposals: list[SkillProposal] = []
        for cluster in recurring(
            clusters, min_contributors=min_contributors, min_sessions=min_sessions
        ):
            draft = synthesize(cluster, self.provider)
            skill_md = render_skill_md(draft)
            provenance = make_provenance(
                cluster, skill_md, now=now, include_contributors=include_contributors
            )
            proposals.append(SkillProposal(draft, skill_md, provenance, cluster))
        return proposals


def write_proposal(proposal: SkillProposal, skills_dir: Path | str) -> Path:
    """Write ``<skills_dir>/<name>/{SKILL.md,provenance.json}``; return the dir."""
    target = Path(skills_dir) / proposal.draft.name
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(proposal.skill_md)
    (target / "provenance.json").write_text(render_provenance(proposal.provenance))
    return target


def mine_personal(
    store: object,
    *,
    provider: LLMProvider | None = None,
    min_sessions: int = 3,
    embedder: Embedder | None = None,
) -> list[SkillProposal]:
    """Mine the engineer's OWN compactions into personal skill proposals."""
    compactions = store.list_compactions(limit=1_000_000)  # type: ignore[attr-defined]
    miner = SkillMiner(embedder=embedder, provider=provider)
    return miner.mine(
        compactions, min_contributors=1, min_sessions=min_sessions, include_contributors=True
    )


__all__ = ["SkillMiner", "SkillProposal", "write_proposal", "mine_personal"]
