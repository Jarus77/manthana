"""The compactor: a session + its turns -> a validated EngineeringCompaction.

Compaction on the agent is DETERMINISTIC and local — it never invokes a model.
It fills the fields Manthana can know from its own data (ids, timestamps,
duration, cost/tier from the cost module, and ``files_touched`` from the turns'
real tool calls) and leaves the qualitative fields (approach, artifacts,
friction, languages/frameworks, …) empty with ``source="pending"``. The server
enriches those later on the operator's metered API key.

Why: the agent used to shell out to ``claude -p`` for the qualitative fields,
but that invocation itself created a Claude Code transcript on disk, which the
watcher then captured and compacted — unbounded recursion that burned the
engineer's own tokens. Agents must never call an LLM.

``native_summary`` carries the session's OWN compaction summary (Claude Code's
``isCompactSummary`` turn / Codex's ``compacted`` payload) when it has one, so
server-side enrichment can digest that instead of the whole transcript.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from datetime import UTC, datetime

from manthana.schemas import (
    EngineeringCompaction,
    Outcome,
    Session,
    Turn,
)

from ..cost import estimate_cost

# The compaction prompt template lives in the SERVER package now
# (``manthana.server.enrich.prompt``) — the agent never renders it. Only the
# version string stays here, stamped onto the deterministic digest so the
# server's enriched output remains traceable to a template version. Keep this
# value in step with ``manthana.server.enrich.prompt.PROMPT_VERSION``.
PROMPT_VERSION = "v2"

# Claude Code tool names + Codex's `apply_patch` (synthesized by the Codex
# collector from `patch_apply_end`, which is where Codex records real file edits).
_FILE_TOOLS = frozenset({"Edit", "Write", "Read", "MultiEdit", "NotebookEdit", "apply_patch"})


def files_from_turns(turns: list[Turn]) -> list[str]:
    """File paths actually read/written, from the turns' own tool calls.

    Authoritative and complete (covers the whole session, not just a prompt
    window), and needs no model — this is why files_touched survives the move to
    deterministic compaction intact. Order-preserving, de-duplicated.
    """
    seen: dict[str, None] = {}
    for turn in turns:
        if turn.tool_name in _FILE_TOOLS and turn.tool_input:
            fp = turn.tool_input.get("file_path") or turn.tool_input.get("notebook_path")
            if isinstance(fp, str) and fp:
                seen.setdefault(fp, None)
    return list(seen)


def _fallback_intent(turns: list[Turn]) -> str:
    for turn in turns:
        if turn.role.value == "user" and turn.content:
            return turn.content[:200]
    return "unknown"


def _duration_seconds(session: Session) -> float:
    if session.ended_at is not None:
        return max(0.0, (session.ended_at - session.started_at).total_seconds())
    return 0.0


class Compactor:
    """Produces an EngineeringCompaction from a session and its turns.

    Takes no provider: compaction is local, free, and model-free by construction.
    """

    def __init__(self, prompt_version: str = PROMPT_VERSION) -> None:
        self.prompt_version = prompt_version

    def compact(
        self, session: Session, turns: list[Turn], *, native_summary: str | None = None
    ) -> EngineeringCompaction:
        """Build the deterministic digest. Never calls a model.

        ``task_intent`` is grounded in the first user turn rather than inferred;
        the remaining qualitative fields stay empty until the server enriches
        them (``source="pending"`` is the signal that they are unwritten).
        """
        cost = estimate_cost(turns)
        return EngineeringCompaction(
            id=f"comp-{session.id}",
            session_id=session.id,
            actor=session.actor,
            surface=session.surface,
            project=session.project,
            started_at=session.started_at,
            ended_at=session.ended_at or session.started_at,
            duration_seconds=_duration_seconds(session),
            task_intent=_fallback_intent(turns),
            approach="",
            artifacts=[],
            outcome=Outcome.partial,  # unknown until enrichment; partial is the neutral value
            friction_points=[],
            reusable_pattern=False,
            tier_used=cost.tier,
            est_cost_usd=cost.usd,
            total_tokens=cost.total_tokens,
            input_tokens=cost.input_tokens,
            output_tokens=cost.output_tokens,
            cache_write_tokens=cost.cache_write_tokens,
            cache_read_tokens=cost.cache_read_tokens,
            created_at=datetime.now(UTC),  # when this digest was built (auto-release window)
            prompt_version=self.prompt_version,
            source="pending",
            native_summary=native_summary,
            files_touched=files_from_turns(turns),
            prs_opened=[],
            tests_added=[],
            dead_end_branches=[],
            languages=[],
            frameworks=[],
        )


__all__ = ["Compactor", "files_from_turns", "PROMPT_VERSION"]
