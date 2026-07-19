"""Codex rollout collector and capture-pipeline tests.

The synthetic fixture mirrors the field shapes observed in Codex Desktop
0.144/0.145 rollout JSONL without containing a real conversation.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
from pathlib import Path

from manthana.agent.capture import ingest_file
from manthana.agent.compact import compact_session
from manthana.agent.llm import MockProvider
from manthana.agent.store import Store
from manthana.collectors import CodexCollector
from manthana.schemas import Role, Surface

FIXTURE = str(Path(__file__).parent / "fixtures" / "codex" / "sample-rollout.jsonl")
SESSION_ID = "019f75a7-4eae-76c3-9c28-57d2ad3c9355"


def _collector(codex_dir: Path | None = None) -> CodexCollector:
    return CodexCollector(actor="eng@example.com", codex_dir=codex_dir)


def test_parse_messages_tools_metadata_and_summary() -> None:
    turns, meta = _collector().read(FIXTURE)

    assert meta.session_id == SESSION_ID
    assert meta.cwd == "/tmp/codex-demo"
    assert meta.model == "gpt-5.6-sol"
    assert meta.cli_version == "0.145.0-alpha.18"
    assert meta.originator == "codex_work_desktop"
    assert meta.source == "vscode"
    assert meta.compact_summary is not None
    assert meta.compact_summary.text.startswith("Earlier context:")
    assert meta.compact_summary.window_number == 2
    assert meta.compact_summary_at == 6

    assert [turn.role for turn in turns] == [
        Role.user,
        Role.assistant,
        Role.assistant,
        Role.tool,
        Role.assistant,
        Role.tool,
        Role.user,
    ]
    assert [turn.seq for turn in turns] == list(range(7))
    assert turns[0].id == f"{SESSION_ID}-000000"
    assert turns[0].source_parent_id == "turn-1"

    joined = " ".join(turn.content or turn.tool_output or "" for turn in turns)
    assert "Internal developer instructions" not in joined
    assert "Private chain of thought" not in joined
    assert joined.count("I will inspect the test.") == 1

    exec_call, exec_result = turns[2], turns[3]
    assert exec_call.tool_name == "exec"
    assert exec_call.tool_input == {"command": "pytest tests/test_parser.py"}
    assert exec_result.tool_name == "exec"
    assert exec_result.tool_use_id == "call-exec"
    assert exec_result.tool_output == "1 failed"

    patch_call, patch_result = turns[4], turns[5]
    assert patch_call.tool_name == "apply_patch"
    assert patch_call.tool_input == {"path": "parser.py", "patch": "handle None"}
    assert patch_result.tool_name == "apply_patch"
    assert patch_result.tool_output == "Done"


def test_incremental_usage_is_attached_once() -> None:
    turns, _meta = _collector().read(FIXTURE)
    assistant_text, exec_call = turns[1], turns[2]
    assert assistant_text.tokens_in == 100
    assert assistant_text.tokens_out == 20
    assert assistant_text.cache_read_tokens == 30
    assert assistant_text.cache_creation_tokens == 4
    assert exec_call.tokens_in == 40
    assert exec_call.tokens_out == 5
    assert sum(turn.tokens_in or 0 for turn in turns) == 140
    assert all(
        turn.model == "gpt-5.6-sol"
        for turn in turns
        if turn.role is Role.assistant
    )


def test_read_summary_scans_rollout_without_flattening_it() -> None:
    summary = _collector().read_summary(FIXTURE)
    assert summary is not None
    assert summary.text.startswith("Earlier context:")
    assert summary.window_number == 2


def test_discover_includes_active_and_archived_rollouts(tmp_path: Path) -> None:
    active = tmp_path / "sessions" / "2026" / "07" / "18" / "active.jsonl"
    archived = tmp_path / "archived_sessions" / "archived.jsonl"
    active.parent.mkdir(parents=True)
    archived.parent.mkdir(parents=True)
    active.write_text("{}\n")
    archived.write_text("{}\n")
    (tmp_path / "session_index.jsonl").write_text("{}\n")

    assert _collector(tmp_path).discover() == sorted([str(active), str(archived)])


def test_capture_persists_codex_surface_and_native_summary() -> None:
    store = Store.open_memory()
    result = ingest_file(store, FIXTURE, actor="eng@example.com", collector=_collector())

    assert result.session_count == 1
    session = store.get_session(SESSION_ID)
    assert session is not None
    assert session.surface is Surface.codex
    assert session.project == "codex-demo"
    assert session.has_compact_summary is True
    assert len(store.get_turns(SESSION_ID)) == 7

    response = json.dumps(
        {
            "task_intent": "fix the parser",
            "approach": "inspected and patched it",
            "outcome": "success",
        }
    )
    compaction = compact_session(store, SESSION_ID, provider=MockProvider(response))
    assert compaction is not None
    # Compatibility value: this means a surface-native summary, including Codex.
    assert compaction.source == "claude_summary"


def test_patch_apply_end_yields_files_touched(tmp_path) -> None:
    """Codex records real file edits in event_msg/patch_apply_end, not in the tool
    call — so without this the compactor's deterministic files_touched starves."""
    from manthana.agent.compactor.compactor import files_from_turns

    roll = tmp_path / "rollout-2026-07-18T10-00-00-019f759f-90dd-7f03-a472-6f6bf45dee71.jsonl"
    roll.write_text("\n".join([
        json.dumps({"type": "session_meta", "timestamp": "2026-07-18T10:00:00Z",
                    "payload": {"session_id": "s1", "cwd": "/repo", "cli_version": "0.144.2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-07-18T10:00:05Z",
                    "payload": {"type": "patch_apply_end", "success": True, "changes": {
                        "/repo/api/webhook.py": {"type": "update", "unified_diff": "@@"},
                        "/repo/api/new.py": {"type": "add", "unified_diff": "@@"}}}}),
    ]))
    turns, meta = CodexCollector(actor="e@x.com", codex_dir=tmp_path).read(str(roll))
    patch_turns = [t for t in turns if t.tool_name == "apply_patch"]
    assert len(patch_turns) == 2
    assert files_from_turns(turns) == ["/repo/api/webhook.py", "/repo/api/new.py"]
    assert meta.session_id == "s1"


def test_patch_apply_failure_marks_error(tmp_path) -> None:
    roll = tmp_path / "rollout-2026-07-18T11-00-00-019f759f-90dd-7f03-a472-6f6bf45dee72.jsonl"
    roll.write_text(json.dumps({
        "type": "event_msg", "timestamp": "2026-07-18T11:00:00Z",
        "payload": {"type": "patch_apply_end", "success": False,
                    "changes": {"/repo/x.py": {"type": "update"}}},
    }))
    turns, _meta = CodexCollector(actor="e@x.com", codex_dir=tmp_path).read(str(roll))
    assert [t.error for t in turns] == ["patch apply failed"]
