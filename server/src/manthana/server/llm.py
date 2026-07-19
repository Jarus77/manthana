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


def make_provider(config: ServerConfig) -> LLMProvider:
    """Select the founder-narrative provider from config (arch §9).

    Defaults to the deterministic mock so dev/tests need no API key; the org flips
    ``MANTHANA_SERVER_LLM=anthropic`` (single server-wide ``ANTHROPIC_API_KEY``) for a real
    model. If the anthropic SDK / key is missing, FALL BACK to the mock with a clear log
    rather than crashing the server; the real provider is wrapped in ``ResilientProvider``."""
    if config.llm_provider == "anthropic":
        try:
            inner = AnthropicProvider(model=config.llm_model, max_tokens=config.llm_max_tokens)
        except Exception as exc:  # noqa: BLE001 - missing SDK/key → degrade, don't crash boot
            _log.warning(
                "anthropic provider unavailable (%s); falling back to mock — set the "
                "'manthana-server[llm]' extra + ANTHROPIC_API_KEY to enable",
                exc,
            )
            return MockProvider("{}")
        return ResilientProvider(inner)
    return MockProvider("{}")


def make_enrich_provider(config: ServerConfig) -> LLMProvider:
    """Provider for server-side digest enrichment (arch: enrichment pass).

    Deliberately SEPARATE from ``make_provider``: enrichment is bulk structured
    summarization and runs on a cheap model (``enrich_model``), while the founder
    narrative may stay on a stronger tier. Same degrade-don't-crash contract — a
    missing SDK/key falls back to the mock so the server still boots.
    """
    if config.llm_provider == "anthropic":
        try:
            inner = AnthropicProvider(
                model=config.enrich_model, max_tokens=config.enrich_max_tokens
            )
        except Exception as exc:  # noqa: BLE001 - missing SDK/key → degrade, don't crash boot
            _log.warning(
                "anthropic enrichment provider unavailable (%s); falling back to mock", exc
            )
            return MockProvider("{}")
        return ResilientProvider(inner)
    return MockProvider("{}")


__all__ = [
    "LLMProvider",
    "MockProvider",
    "ScriptedProvider",
    "AnthropicProvider",
    "ResilientProvider",
    "make_provider",
    "make_enrich_provider",
]
