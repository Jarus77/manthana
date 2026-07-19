"""Compactor: session + turns -> typed EngineeringCompaction.

The prompt template no longer lives here — agents never call a model, so it
moved to the server package (``manthana.server.enrich.prompt``), where the
enrichment pass uses it. ``PROMPT_VERSION`` stays: the agent stamps it on the
deterministic digest so an enriched digest stays traceable to a template version.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from .compactor import PROMPT_VERSION, Compactor, files_from_turns

__all__ = ["Compactor", "files_from_turns", "PROMPT_VERSION"]
