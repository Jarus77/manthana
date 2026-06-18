"""Server-side LLM provider abstraction.

Open item (tracked in manthana-decisions.md / architecture §9): the server has no
engineer's Claude account, so the founder-query narrative needs its own provider.
Dev/tests use the deterministic ``ScriptedProvider``/``MockProvider``; v1.5 the
org provisions a server API key behind this same interface. Kept server-local
(not imported from the agent) so the AGPL server stays decoupled from the local
agent + collectors.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(self, prompt: str) -> str:
        """Return the model's result text for a prompt."""
        ...


class MockProvider:
    """Always returns the same response (single-call use)."""

    name = "mock"

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


class ScriptedProvider:
    """Returns queued responses in order (multi-call pipelines, e.g. founder query)."""

    name = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._responses:
            return ""
        return self._responses.pop(0)


__all__ = ["LLMProvider", "MockProvider", "ScriptedProvider"]
