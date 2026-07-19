"""LLM provider abstraction.

These providers invoke the engineer's *existing* model access rather than a
bundled API key (decisions doc): Claude Code via
``claude -p "<prompt>" --output-format json`` and Codex via
``codex exec "<prompt>"``. ``complete()`` returns the model's result text
(envelope-unwrapped for the Claude CLI). A deterministic ``MockProvider`` backs
CI/tests so no model access or token spend is required.

SCOPE — these are for USER-INITIATED commands only (``manthana ask``, skill
mining): anything the engineer explicitly asks for and expects to spend tokens
on. NOTHING automatic may reach them. Compaction in particular used to, and each
``claude -p`` call wrote a new Claude Code transcript that the watcher then
captured and compacted, recursing without bound. Compaction is now deterministic
and model-free (see ``compactor/compactor.py``); do not reintroduce a provider
into any background path.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Protocol, runtime_checkable


class LLMError(RuntimeError):
    """Raised when an LLM provider invocation fails."""


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def available(self) -> bool:
        """Whether this provider can run in the current environment."""
        ...

    def complete(self, prompt: str) -> str:
        """Return the model's result text for a prompt."""
        ...


class ClaudeCLIProvider:
    """Shells out to the engineer's Claude Code CLI."""

    name = "claude-cli"

    def __init__(self, binary: str = "claude", timeout: int = 180) -> None:
        self.binary = binary
        self.timeout = timeout
        # Cost/usage reported by the CLI for the LAST complete() call — the real
        # token spend of that invocation (e.g. a compaction). None until a call runs
        # or if the envelope omits them.
        self.last_cost_usd: float | None = None
        self.last_usage: dict[str, int] | None = None

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def complete(self, prompt: str) -> str:
        self.last_cost_usd = None
        self.last_usage = None
        try:
            out = subprocess.run(
                [self.binary, "-p", prompt, "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LLMError(f"claude CLI invocation failed: {exc}") from exc
        if out.returncode != 0:
            raise LLMError(f"claude CLI exited {out.returncode}: {out.stderr.strip()[:500]}")
        # `claude -p --output-format json` returns an envelope with a `result` field
        # plus `total_cost_usd` + `usage` — capture those for cost tracking.
        try:
            envelope = json.loads(out.stdout)
        except json.JSONDecodeError:
            return out.stdout
        if isinstance(envelope, dict):
            cost = envelope.get("total_cost_usd")
            if isinstance(cost, int | float):
                self.last_cost_usd = float(cost)
            usage = envelope.get("usage")
            if isinstance(usage, dict):
                self.last_usage = {k: int(v) for k, v in usage.items() if isinstance(v, int)}
            if "result" in envelope:
                return str(envelope["result"])
        return out.stdout


class CodexCLIProvider:
    """Shells out to the engineer's Codex CLI (``codex exec``)."""

    name = "codex-cli"

    def __init__(self, binary: str = "codex", timeout: int = 180) -> None:
        self.binary = binary
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def complete(self, prompt: str) -> str:
        try:
            out = subprocess.run(
                [self.binary, "exec", prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LLMError(f"codex CLI invocation failed: {exc}") from exc
        if out.returncode != 0:
            raise LLMError(f"codex CLI exited {out.returncode}: {out.stderr.strip()[:500]}")
        return out.stdout


class MockProvider:
    """Deterministic provider for CI/tests. Returns a fixed response."""

    name = "mock"

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    def available(self) -> bool:
        return True

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


def default_provider() -> LLMProvider:
    """Pick the engineer's available CLI, falling back to an empty Mock.

    Real use resolves to the Claude (then Codex) CLI; if neither exists the Mock
    returns ``{}`` so callers degrade instead of crashing. Call this ONLY from
    user-initiated commands — see the module docstring.
    """
    claude = ClaudeCLIProvider()
    if claude.available():
        return claude
    codex = CodexCLIProvider()
    if codex.available():
        return codex
    return MockProvider("{}")


__all__ = [
    "LLMProvider",
    "LLMError",
    "ClaudeCLIProvider",
    "CodexCLIProvider",
    "MockProvider",
    "default_provider",
]
