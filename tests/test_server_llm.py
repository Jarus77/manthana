"""Server LLM providers: AnthropicProvider + make_provider selection.

Fully hermetic — the AnthropicProvider tests inject a fake Messages client, so
no `anthropic` SDK install and no ANTHROPIC_API_KEY are needed. The integration
test drives the real founder pipeline through a fake-backed AnthropicProvider to
prove a real provider yields a grounded, cited narrative (vs the mock's
"insufficient data").

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore
from manthana.server.founder import run_query
from manthana.server.llm import (
    AnthropicProvider,
    MockProvider,
    ResilientProvider,
    make_provider,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


# ── fake Anthropic client (mimics messages.create -> message.content blocks) ──
class _Block:
    def __init__(self, text: str | None, kind: str = "text") -> None:
        self.type = kind
        if text is not None:
            self.text = text


class _Message:
    def __init__(self, blocks: list[_Block]) -> None:
        self.content = blocks


class _Messages:
    def __init__(self, blocks: list[_Block]) -> None:
        self._blocks = blocks
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        return _Message(self._blocks)


class _Client:
    def __init__(self, blocks: list[_Block]) -> None:
        self.messages = _Messages(blocks)


# ── AnthropicProvider ───────────────────────────────────────────────────────
def test_anthropic_provider_concatenates_text_blocks_and_passes_params() -> None:
    client = _Client([_Block("hello "), _Block("[c0]")])
    p = AnthropicProvider(model="claude-x", max_tokens=42, client=client)
    assert p.name == "anthropic"
    assert p.complete("prompt") == "hello [c0]"
    call = client.messages.calls[0]
    assert call["model"] == "claude-x"
    assert call["max_tokens"] == 42
    assert call["messages"] == [{"role": "user", "content": "prompt"}]


def test_anthropic_provider_ignores_non_text_blocks() -> None:
    # tool_use / thinking blocks have no .text and must be skipped, not crash.
    client = _Client([_Block(None, kind="tool_use"), _Block("real answer")])
    p = AnthropicProvider(model="m", client=client)
    assert p.complete("x") == "real answer"


def test_anthropic_provider_survives_text_block_missing_text_attr() -> None:
    # A malformed block typed "text" but without a .text attribute must not crash.
    client = _Client([_Block(None, kind="text"), _Block("ok")])
    p = AnthropicProvider(model="m", client=client)
    assert p.complete("x") == "ok"


# ── make_provider selection ─────────────────────────────────────────────────
def _cfg(**kw: Any) -> ServerConfig:
    return ServerConfig(jwt_secret="x" * 40, admin_token="adm", **kw)


def test_make_provider_defaults_to_mock() -> None:
    provider = make_provider(_cfg())
    assert isinstance(provider, MockProvider)
    assert provider.name == "mock"


def test_make_provider_selects_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    import manthana.server.llm as llm

    captured: dict[str, Any] = {}

    class _Stub:
        name = "anthropic"

        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(llm, "AnthropicProvider", _Stub)
    cfg = _cfg(llm_provider="anthropic", llm_model="claude-z", llm_max_tokens=7)
    provider = llm.make_provider(cfg)
    # the real provider is wrapped in ResilientProvider (retry/backoff)
    assert isinstance(provider, ResilientProvider) and provider.inner.name == "anthropic"
    assert captured == {"model": "claude-z", "max_tokens": 7}


def test_make_provider_falls_back_to_mock_when_anthropic_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import manthana.server.llm as llm

    def _boom(**_kw: Any) -> Any:  # SDK missing / no key → constructor raises
        raise RuntimeError("anthropic SDK not installed")

    monkeypatch.setattr(llm, "AnthropicProvider", _boom)
    provider = llm.make_provider(_cfg(llm_provider="anthropic"))
    assert isinstance(provider, MockProvider)  # degraded, did NOT crash


# ── ResilientProvider: retry on transient, never on auth ─────────────────────
class _Flaky:
    name = "flaky"

    def __init__(self, fail_times: int, exc: Exception) -> None:
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return "ok"


class RateLimitError(Exception):
    """Name matches the retryable set."""


class AuthenticationError(Exception):
    """Name matches the non-retryable (auth) set."""


def test_resilient_retries_transient_then_succeeds() -> None:
    inner = _Flaky(2, RateLimitError("429"))
    p = ResilientProvider(inner, retries=2, sleep=lambda _s: None)
    assert p.complete("x") == "ok"
    assert inner.calls == 3  # 2 failures + 1 success


def test_resilient_does_not_retry_auth_errors() -> None:
    inner = _Flaky(5, AuthenticationError("401"))
    p = ResilientProvider(inner, retries=3, sleep=lambda _s: None)
    with pytest.raises(AuthenticationError):
        p.complete("x")
    assert inner.calls == 1  # auth error → no retry, re-raised


def test_resilient_gives_up_after_retries() -> None:
    inner = _Flaky(99, RateLimitError("429"))
    p = ResilientProvider(inner, retries=2, sleep=lambda _s: None)
    with pytest.raises(RateLimitError):
        p.complete("x")
    assert inner.calls == 3  # initial + 2 retries, then re-raises


def test_invalid_llm_provider_rejected() -> None:
    with pytest.raises(ValueError):
        _cfg(llm_provider="gpt")


def test_config_rejects_dev_default_secrets() -> None:
    from manthana.server.config import _DEV_ADMIN_TOKEN, _DEV_JWT_SECRET

    with pytest.raises(ValueError):
        ServerConfig()  # both shipped placeholders
    with pytest.raises(ValueError):
        ServerConfig(jwt_secret="x" * 40, admin_token=_DEV_ADMIN_TOKEN)
    with pytest.raises(ValueError):
        ServerConfig(jwt_secret=_DEV_JWT_SECRET, admin_token="adm")


def test_config_rejects_out_of_range_numeric_bounds() -> None:
    with pytest.raises(ValueError):
        _cfg(llm_max_tokens=0)  # empty narrative
    with pytest.raises(ValueError):
        _cfg(llm_max_tokens=10_000_000)  # runaway cost typo
    with pytest.raises(ValueError):
        _cfg(k_anon_floor=0)  # would disable the privacy floor


# ── integration: a real provider produces a grounded, cited narrative ────────
def _comp(cid: str, actor: str) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=cid,
        actor=actor,
        surface=Surface.claude_code,
        project="scribe",
        started_at=_T0,
        ended_at=_T0,
        duration_seconds=1.0,
        task_intent=f"intent {cid}",
        approach="a",
        outcome=Outcome.success,
        est_cost_usd=0.5,
        tier_used="opus",
        released=True,
    )


class _BoomProvider:
    """Raises on every call — stands in for a rate-limited / down Anthropic API."""

    name = "boom"

    def complete(self, prompt: str) -> str:
        raise RuntimeError("api unavailable: sk-should-never-reach-client")


def test_run_query_degrades_gracefully_on_provider_error() -> None:
    # A provider exception must NOT 500 the endpoint or leak the SDK exception —
    # it degrades to "insufficient data" (rollup kept, narrative withheld).
    config = _cfg(k_anon_floor=1)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.ingest_compaction(_comp("c0", "e@x.com"), org_id="o1", team_id="t1")
    result = run_query(store, config, org_id="o1", query="what shipped?", provider=_BoomProvider())
    assert result.insufficient_data is True
    assert result.narrative == "insufficient data"
    assert result.citations == []


def _seed_one(store: ServerStore, cid: str, *, org: str = "o1") -> None:
    store.create_org(org, "Acme")
    store.ingest_compaction(_comp(cid, "e@x.com"), org_id=org, team_id="t1")


def test_citation_matches_abbreviated_uuid_prefix() -> None:
    # A real model abbreviates long ids — cite a leading prefix, still grounds.
    config = _cfg(k_anon_floor=1)
    store = ServerStore.open("sqlite://")
    full = "comp-a0565012-55fe-475c-a6aa-2b20144e0e16"
    _seed_one(store, full)
    provider = MockProvider("The team shipped the scribe work [comp-a0565012].")
    result = run_query(store, config, org_id="o1", query="x", provider=provider)
    assert result.insufficient_data is False
    assert result.citations == [full]  # resolved to the full id


def test_citation_matches_comma_grouped_bracket() -> None:
    config = _cfg(k_anon_floor=1)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    a, b = "comp-aaaa1111-x", "comp-bbbb2222-y"
    store.ingest_compaction(_comp(a, "e1@x.com"), org_id="o1", team_id="t1")
    store.ingest_compaction(_comp(b, "e2@x.com"), org_id="o1", team_id="t1")
    provider = MockProvider("Two efforts [comp-aaaa1111, comp-bbbb2222].")
    result = run_query(store, config, org_id="o1", query="x", provider=provider)
    assert result.insufficient_data is False
    assert set(result.citations) == {a, b}


def test_per_filter_k_anon_excludes_subfloor_outcome_from_narrative() -> None:
    # A project clears the floor (4 contributors on "success"), but one lone
    # "abandoned" session must NOT be citable in the narrative even though its
    # project survived — its outcome cohort is sub-floor.
    config = _cfg(k_anon_floor=4)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    for i in range(4):
        store.ingest_compaction(_comp(f"ok{i}", f"e{i}@x.com"), org_id="o1", team_id="t1")
    lone = _comp("aband0", "solo@x.com")
    lone.outcome = Outcome.abandoned  # single-contributor outcome cohort
    store.ingest_compaction(lone, org_id="o1", team_id="t1")
    # provider cites the lone abandoned compaction; grounding must reject it
    provider = MockProvider("The team mostly succeeded but one effort was abandoned [aband0].")
    result = run_query(store, config, org_id="o1", query="how's it going?", provider=provider)
    assert "aband0" not in result.citations  # sub-floor outcome cohort never cited


def test_ambiguous_prefix_citation_does_not_ground() -> None:
    # A prefix matching >1 compaction is ambiguous → grounds nothing (conservative).
    config = _cfg(k_anon_floor=1)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.ingest_compaction(_comp("comp-aaa111", "e1@x.com"), org_id="o1", team_id="t1")
    store.ingest_compaction(_comp("comp-aaa222", "e2@x.com"), org_id="o1", team_id="t1")
    provider = MockProvider("Vague claim [comp-aaa].")  # prefix of both
    result = run_query(store, config, org_id="o1", query="x", provider=provider)
    assert result.insufficient_data is True
    assert result.citations == []


def test_founder_query_grounded_with_anthropic_provider() -> None:
    config = _cfg(k_anon_floor=1)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.ingest_compaction(_comp("c0", "e@x.com"), org_id="o1", team_id="t1")
    # The fake returns a citing narrative for the narrative call (and "{}"-free
    # text for parse → empty filter → all rows), so grounding succeeds.
    provider = AnthropicProvider(
        model="m", client=_Client([_Block("Team shipped the scribe work [c0].")])
    )
    result = run_query(store, config, org_id="o1", query="what shipped?", provider=provider)
    assert result.insufficient_data is False
    assert result.citations == ["c0"]
    assert "[c0]" in result.narrative


class _CapturingProvider:
    """Records every prompt; returns a fixed citing reply (so parse → {} → all rows,
    and the narrative call cites the compaction)."""

    name = "capture"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def test_what_went_wrong_feeds_friction_and_query_to_narrative() -> None:
    # A SUCCESS session that nonetheless hit friction must still answer "what went
    # wrong?" — the friction is fed to the narrative, and the question is too.
    from manthana.schemas import FrictionCategory, FrictionPoint

    config = _cfg(k_anon_floor=1)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    comp = _comp("c0", "e@x.com").model_copy(
        update={
            "friction_points": [
                FrictionPoint(
                    category=FrictionCategory.tool_error,
                    description="flaky DB timeout on the integration suite",
                    turn_refs=["12"],
                )
            ]
        }
    )
    store.ingest_compaction(comp, org_id="o1", team_id="t1")

    provider = _CapturingProvider("A flaky DB timeout blocked the suite [c0].")
    result = run_query(store, config, org_id="o1", query="what went wrong?", provider=provider)

    assert result.insufficient_data is False  # NOT a dead-end anymore
    assert result.citations == ["c0"]
    # the narrative prompt (2nd call) must carry BOTH the question and the friction
    narrative_prompt = provider.prompts[1]
    assert "what went wrong?" in narrative_prompt
    assert "flaky DB timeout on the integration suite" in narrative_prompt


def test_parse_filter_does_not_force_outcome_on_failure_query() -> None:
    # With a model that returns no outcome, the pipeline must not invent one.
    from manthana.server.founder import parse_filter

    spec = parse_filter("what went wrong this week?", MockProvider("{}"))
    assert spec.outcome is None


def test_privacy_open_returns_individual_that_k_anon_suppresses() -> None:
    # The core privacy assertion: under privacy_mode="k_anon" a single-person query is
    # suppressed by the floor; an org that waived anonymization ("open" →
    # allow_individual=True) gets the named answer.
    config = _cfg(k_anon_floor=4)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    for i in range(3):
        store.ingest_compaction(_comp(f"s{i}", "suraj@acme.demo"), org_id="o1", team_id="t1")
    citing = MockProvider("Suraj shipped the work [s0].")

    k_anon = run_query(store, config, org_id="o1", query="what did suraj do?", provider=citing)
    assert k_anon.insufficient_data is True  # single contributor < floor → suppressed

    open_org = run_query(
        store, config, org_id="o1", query="what did suraj do?",
        provider=citing, allow_individual=True,
    )
    assert open_org.insufficient_data is False  # privacy_mode="open" bypasses the floor
    assert open_org.citations == ["s0"]


def test_resolve_actor_unique_ambiguous_none() -> None:
    from manthana.server.founder import _resolve_actor

    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.upsert_actor("suraj@acme.demo", "o1", "t1")
    store.upsert_actor("tarun@acme.demo", "o1", "t1")
    assert _resolve_actor(store, "o1", "Suraj") == "suraj@acme.demo"  # unique → resolved
    assert _resolve_actor(store, "o1", "acme") == "acme"  # matches both → unchanged
    assert _resolve_actor(store, "o1", "ghost") == "ghost"  # no match → unchanged
    assert _resolve_actor(store, "o1", None) is None
