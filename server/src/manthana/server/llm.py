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

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .config import ServerConfig

_log = logging.getLogger(__name__)

# Exception class names that are worth retrying (transient): rate limits, connection /
# timeout blips, and 5xx. Auth / bad-request errors are NOT retried (they won't recover).
_RETRYABLE_NAMES = {
    "RateLimitError", "APIConnectionError", "APITimeoutError", "APIConnectionTimeoutError",
    "InternalServerError", "ServiceUnavailableError", "OverloadedError",
}
_AUTH_NAMES = {"AuthenticationError", "PermissionDeniedError"}


def _is_retryable(exc: BaseException) -> bool:
    """A best-effort, SDK-agnostic classifier (we don't import anthropic — it's optional).
    Retry on known-transient error class names or a 429 / 5xx ``status_code``; never on auth."""
    name = type(exc).__name__
    if name in _AUTH_NAMES:
        return False
    if name in _RETRYABLE_NAMES:
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status == 429 or 500 <= status < 600)


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


class AnthropicProvider:
    """Real server-side provider — the Anthropic Messages API (arch §9).

    The org provisions ``ANTHROPIC_API_KEY``; the SDK ships as the optional
    ``manthana-server[llm]`` extra so dev/tests (which use the mock) stay
    dependency-free. Tests inject a fake ``client`` to avoid any network/key.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        model: str,
        max_tokens: int = 1024,
        api_key: str | None = None,
        client: Any = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        # (input_tokens, output_tokens) of the most recent call — read by the
        # per-org metering wrapper for quota accounting.
        self.last_usage: tuple[int, int] | None = None
        if client is not None:
            self._client = client
            return
        try:
            from anthropic import Anthropic  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "AnthropicProvider requires the 'anthropic' SDK — "
                "install the extra: pip install 'manthana-server[llm]'"
            ) from exc
        # Anthropic() reads ANTHROPIC_API_KEY from the environment when api_key is None.
        self._client = Anthropic(api_key=api_key) if api_key else Anthropic()

    def complete(self, prompt: str) -> str:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = getattr(message, "usage", None)
        if usage is not None:
            self.last_usage = (
                int(getattr(usage, "input_tokens", 0) or 0),
                int(getattr(usage, "output_tokens", 0) or 0),
            )
        # Concatenate only text blocks (tool-use / thinking blocks have no .text);
        # getattr-default guards a malformed text block missing .text.
        parts = [
            getattr(block, "text", "")
            for block in message.content
            if getattr(block, "type", None) == "text"
        ]
        return "".join(parts).strip()


class ClaudeCLIProvider:
    """Shells out to a Claude CLI already installed and logged in as this user.

    This is the bring-your-own-model path, and it exists for one specific person:
    someone running Manthana entirely on their own laptop. They already pay for
    Claude Code. Asking them for a second, separately-billed ``ANTHROPIC_API_KEY``
    to summarise sessions they just paid to have is an absurd toll, and it was the
    only thing standing between a solo user and a complete local install.

    ``claude -p`` is headless — no TTY, no interactive login — but it DOES read the
    credentials in ``$HOME/.claude``. So this provider works when the server runs as
    a human's own user (``manthana-server serve`` on a laptop) and will not work in
    the shipped container images, which have neither the binary nor a logged-in
    home. That asymmetry is the whole reason it is opt-in via config rather than
    an automatic fallback: silently degrading to it in a container would turn a
    misconfiguration into a mystery.

    Copied from the agent's provider rather than imported: the AGPL server stays
    decoupled from the Apache-2.0 agent, and that boundary is worth more than
    forty lines of deduplication.
    """

    name = "claude-cli"

    def __init__(self, *, model: str = "", binary: str = "claude", timeout: int = 180) -> None:
        # Recorded for cost attribution, not passed to the CLI: the binary uses
        # whatever model its own configuration selects, and pretending otherwise
        # would put a number in the usage table that nothing produced.
        self.model = model
        self.binary = binary
        self.timeout = timeout
        #: (input_tokens, output_tokens) of the most recent call, in the shape
        #: MeteredProvider expects. None when the envelope omitted usage — the
        #: meter then falls back to its own heuristic rather than recording zero.
        self.last_usage: tuple[int, int] | None = None
        #: What the CLI itself said the call cost. This is the ENGINEER's spend on
        #: their own subscription, not ours, which is exactly why a BYO deploy
        #: should leave llm_monthly_cap_usd at 0 — our cap has no business
        #: throttling a bill we are not paying.
        self.last_cost_usd: float | None = None

    def available(self) -> bool:
        import shutil

        return shutil.which(self.binary) is not None

    def complete(self, prompt: str) -> str:
        import json
        import subprocess  # noqa: S404 - invoking a user-installed CLI is the point

        self.last_usage = None
        self.last_cost_usd = None
        try:
            out = subprocess.run(  # noqa: S603 - fixed argv, no shell, prompt is not a command
                [self.binary, "-p", prompt, "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"claude CLI invocation failed: {exc}") from exc
        if out.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {out.returncode}: {out.stderr.strip()[:500]}"
            )
        try:
            envelope = json.loads(out.stdout)
        except json.JSONDecodeError:
            return out.stdout
        if not isinstance(envelope, dict):
            return out.stdout
        cost = envelope.get("total_cost_usd")
        if isinstance(cost, int | float):
            self.last_cost_usd = float(cost)
        usage = envelope.get("usage")
        if isinstance(usage, dict):
            self.last_usage = (
                int(usage.get("input_tokens", 0) or 0),
                int(usage.get("output_tokens", 0) or 0),
            )
        if "result" in envelope:
            return str(envelope["result"])
        return out.stdout


class ResilientProvider:
    """Wraps a real provider with bounded retry/backoff on TRANSIENT failures (rate
    limit, connection, 5xx). Auth / bad-request errors are not retried. After exhausting
    retries it re-raises — the founder/digest pipeline already degrades a raised provider
    error to ``insufficient data`` (never a 500 / leak), so this only improves the success
    rate on blips without changing the failure contract."""

    name = "resilient"

    def __init__(
        self,
        inner: LLMProvider,
        *,
        retries: int = 2,
        backoff: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner = inner
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleep

    def complete(self, prompt: str) -> str:
        last: BaseException | None = None
        for attempt in range(self.retries + 1):
            try:
                return self.inner.complete(prompt)
            except Exception as exc:  # noqa: BLE001 - classify, maybe retry, else re-raise
                last = exc
                if attempt == self.retries or not _is_retryable(exc):
                    break
                _log.warning(
                    "server LLM transient error (%s); retry %d/%d",
                    type(exc).__name__, attempt + 1, self.retries,
                )
                self._sleep(self.backoff * (2**attempt))
        _log.exception("server LLM call failed (%s)", type(last).__name__ if last else "?")
        raise last if last else RuntimeError("LLM call failed")


def _build(config: ServerConfig, *, model: str, max_tokens: int, pass_name: str) -> LLMProvider:
    """Construct the configured provider for one pass, degrading rather than crashing.

    Every pass shares this because the failure contract matters more than the
    per-pass differences: a missing SDK, key, or CLI binary must fall back to the
    mock with a loud log, never take the server down at boot. A mock returns
    ``{}``, which every caller already treats as "no data" — the wiki stays
    honest, it just stays empty.
    """
    if config.llm_provider == "anthropic":
        try:
            return ResilientProvider(AnthropicProvider(model=model, max_tokens=max_tokens))
        except Exception as exc:  # noqa: BLE001 - missing SDK/key → degrade, don't crash boot
            _log.warning(
                "anthropic %s provider unavailable (%s); falling back to mock — set the "
                "'manthana-server[llm]' extra + ANTHROPIC_API_KEY to enable",
                pass_name, exc,
            )
            return MockProvider("{}")
    if config.llm_provider == "claude_cli":
        cli = ClaudeCLIProvider(model=model, binary=config.claude_cli_binary)
        if not cli.available():
            _log.warning(
                "claude_cli %s provider selected but %r is not on PATH; falling back to "
                "mock — this mode requires the Claude CLI installed and logged in as the "
                "user running the server, so it does not work inside the container images",
                pass_name, config.claude_cli_binary,
            )
            return MockProvider("{}")
        return ResilientProvider(cli)
    return MockProvider("{}")


def make_provider(config: ServerConfig) -> LLMProvider:
    """Select the founder-narrative provider from config (arch §9).

    Defaults to the deterministic mock so dev/tests need no API key; a hosted org
    flips ``MANTHANA_SERVER_LLM=anthropic`` (server-wide ``ANTHROPIC_API_KEY``),
    and a solo self-hoster flips ``claude_cli`` to spend their own Claude
    subscription instead of buying a second, separately-billed one."""
    return _build(
        config, model=config.llm_model, max_tokens=config.llm_max_tokens, pass_name="narrative"
    )


def make_enrich_provider(config: ServerConfig) -> LLMProvider:
    """Provider for server-side digest enrichment (arch: enrichment pass).

    Deliberately SEPARATE from ``make_provider``: enrichment is bulk structured
    summarization and runs on a cheap model (``enrich_model``), while the founder
    narrative may stay on a stronger tier. Same degrade-don't-crash contract — a
    missing SDK/key falls back to the mock so the server still boots.
    """
    return _build(
        config,
        model=config.enrich_model,
        max_tokens=config.enrich_max_tokens,
        pass_name="enrichment",
    )


def make_consolidate_provider(config: ServerConfig) -> LLMProvider:
    """Provider for knowledge consolidation (compactions → org-wiki notes).

    Mirror of ``make_enrich_provider``: bulk adjudication runs on a cheap model
    (``consolidate_model``), separate from the founder-narrative tier. Same
    degrade-don't-crash contract.
    """
    return _build(
        config,
        model=config.consolidate_model,
        max_tokens=config.consolidate_max_tokens,
        pass_name="consolidation",
    )


__all__ = [
    "LLMProvider",
    "MockProvider",
    "ScriptedProvider",
    "AnthropicProvider",
    "ClaudeCLIProvider",
    "ResilientProvider",
    "make_provider",
    "make_enrich_provider",
    "make_consolidate_provider",
]
