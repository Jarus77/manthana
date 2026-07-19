"""Server-side enrichment of pending digests.

Agents emit deterministic ``source="pending"`` digests; this package fills the
qualitative fields on the operator's metered key, preferring the coding agent's
own compaction summary over rehydrating the raw transcript.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from .enricher import (
    EnrichStats,
    apply_enrichment,
    enrich_org,
    enrich_provider_for,
    rehydrate_turns,
    run_enrichment_pass,
)
from .prompt import PROMPT_VERSION, build_prompt, serialize_turns

__all__ = [
    "EnrichStats",
    "apply_enrichment",
    "enrich_org",
    "enrich_provider_for",
    "rehydrate_turns",
    "run_enrichment_pass",
    "build_prompt",
    "serialize_turns",
    "PROMPT_VERSION",
]
