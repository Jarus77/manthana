"""Codex CLI/Desktop collector.

Reads current Codex rollout JSONL from
``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`` and
``~/.codex/archived_sessions/``. The format was verified against Codex Desktop
0.144/0.145 transcripts in July 2026.

Only canonical ``response_item`` records become turns; the parallel
``event_msg`` user/agent display events are ignored so text is not duplicated.
Reasoning and developer instructions are intentionally skipped. Incremental
``event_msg.token_count`` usage is attached to exactly one assistant turn.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from manthana.schemas import Role, Surface, Turn

from .identity import resolve_actor

DEFAULT_CODEX_DIR = Path.home() / ".codex"
_UUID_AT_END = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


@dataclass(frozen=True)
class CodexSummary:
    """Codex's newest context-compaction summary in a rollout."""

    text: str
    trigger: str | None = "auto"
    pre_tokens: int | None = None
    window_number: int | None = None


@dataclass(frozen=True)
class CodexFileMeta:
    """Metadata extracted from one Codex rollout."""

    session_id: str
    cwd: str | None
    git_branch: str | None
    mtime: datetime
    model: str | None = None
    cli_version: str | None = None
    originator: str | None = None
    source: str | None = None
    compact_summary: CodexSummary | None = None
    compact_summary_at: int | None = None


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stringify(value: object) -> str | None:
    """Flatten Codex string/list tool output without losing structured results."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(parts)
    return json.dumps(value, ensure_ascii=False, default=str)


def _text_blocks(value: object) -> list[str]:
    """Extract visible text from input_text/output_text/text blocks."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            out.append(text)
    return out


def _tool_input(value: object) -> dict[str, Any] | None:
    """Normalize Codex's JSON-string tool arguments to Turn's dictionary field."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        if not value:
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    if value is None:
        return None
    return {"value": value}


def _source_parent(payload: dict[str, Any]) -> str | None:
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(metadata, dict) and isinstance(metadata.get("turn_id"), str):
        return metadata["turn_id"]
    turn_id = payload.get("turn_id")
    return turn_id if isinstance(turn_id, str) else None


def _make_turn(
    *,
    actor: str,
    base_id: str,
    role: Role,
    timestamp: datetime | None,
    payload: dict[str, Any],
    **fields: Any,
) -> Turn:
    event_id = payload.get("id")
    if not isinstance(event_id, str):
        event_id = payload.get("call_id")
    return Turn(
        id="",
        session_id=base_id,
        actor=actor,
        seq=0,
        timestamp=timestamp,
        role=role,
        source_event_id=event_id if isinstance(event_id, str) else None,
        source_parent_id=_source_parent(payload),
        **fields,
    )


def _session_id_from_path(path: Path) -> str:
    match = _UUID_AT_END.search(path.stem)
    return match.group(1) if match else path.stem


def _is_desktop_sandbox(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return "/.codex/.chatgpt-projects/" in normalized


def _prefer_cwd(
    current: str | None,
    current_rank: int,
    candidate: object,
    rank: int,
) -> tuple[str | None, int]:
    if not isinstance(candidate, str) or not candidate:
        return current, current_rank
    current_score = (0 if current is None or _is_desktop_sandbox(current) else 1, current_rank)
    candidate_score = (0 if _is_desktop_sandbox(candidate) else 1, rank)
    if current is None or candidate_score > current_score:
        return candidate, rank
    return current, current_rank


def _usage_fields(payload: dict[str, Any]) -> dict[str, int | None] | None:
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    usage = info.get("last_token_usage")
    if not isinstance(usage, dict):
        return None

    def number(*keys: str) -> int | None:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    fields = {
        "tokens_in": number("input_tokens"),
        "tokens_out": number("output_tokens"),
        "cache_creation_tokens": number(
            "cache_write_input_tokens", "cache_creation_input_tokens"
        ),
        "cache_read_tokens": number("cached_input_tokens", "cache_read_input_tokens"),
    }
    return fields if any(value is not None for value in fields.values()) else None


def _summary_from_entry(entry: dict[str, Any]) -> CodexSummary | None:
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    if entry.get("type") == "compacted":
        text = _stringify(payload.get("message"))
        window = payload.get("window_number")
        return (
            CodexSummary(
                text=text,
                window_number=window if isinstance(window, int) else None,
            )
            if text
            else None
        )
    if entry.get("type") == "event_msg" and payload.get("type") == "context_compacted":
        text = _stringify(payload.get("summary") or payload.get("message"))
        return CodexSummary(text=text) if text else None
    return None


class CodexCollector:
    """Collector for Codex CLI and Codex Desktop rollout transcripts."""

    surface: Surface = Surface.codex

    def __init__(self, actor: str | None = None, codex_dir: Path | None = None) -> None:
        self.actor = actor or resolve_actor()
        self.codex_dir = codex_dir or DEFAULT_CODEX_DIR

    def discover(self) -> list[str]:
        """Find active and archived rollout JSONL files."""
        paths: set[Path] = set()
        for dirname in ("sessions", "archived_sessions"):
            root = self.codex_dir / dirname
            if root.exists():
                paths.update(root.rglob("*.jsonl"))
        return sorted(str(path) for path in paths)

    def read(self, source: str) -> tuple[list[Turn], CodexFileMeta]:
        """Parse one rollout into ordered normalized turns plus file metadata."""
        path = Path(source)
        path_session_id = _session_id_from_path(path)
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            mtime = datetime.now().astimezone()

        turns: list[Turn] = []
        tool_names: dict[str, str] = {}
        session_id: str | None = None
        cwd: str | None = None
        cwd_rank = -1
        git_branch: str | None = None
        model: str | None = None
        cli_version: str | None = None
        originator: str | None = None
        source_surface: str | None = None
        summary: CodexSummary | None = None
        summary_at: int | None = None
        usage_boundary = -1

        for raw in path.read_text(errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            timestamp = _parse_ts(entry.get("timestamp"))
            entry_type = entry.get("type")

            if entry_type == "session_meta":
                candidate_id = payload.get("session_id") or payload.get("id")
                if isinstance(candidate_id, str):
                    session_id = candidate_id
                cwd, cwd_rank = _prefer_cwd(cwd, cwd_rank, payload.get("cwd"), 1)
                cli_version = (
                    payload["cli_version"]
                    if isinstance(payload.get("cli_version"), str)
                    else cli_version
                )
                originator = (
                    payload["originator"]
                    if isinstance(payload.get("originator"), str)
                    else originator
                )
                source_surface = (
                    payload["source"]
                    if isinstance(payload.get("source"), str)
                    else source_surface
                )
                git = payload.get("git")
                if isinstance(git, dict) and isinstance(git.get("branch"), str):
                    git_branch = git["branch"]
                continue

            if entry_type == "turn_context":
                if isinstance(payload.get("model"), str):
                    model = payload["model"]
                cwd, cwd_rank = _prefer_cwd(cwd, cwd_rank, payload.get("cwd"), 4)
                roots = payload.get("workspace_roots")
                if isinstance(roots, list):
                    for root in roots:
                        cwd, cwd_rank = _prefer_cwd(cwd, cwd_rank, root, 5)
                continue

            if entry_type == "world_state":
                state = payload.get("state")
                environments = state.get("environments") if isinstance(state, dict) else None
                local = (
                    environments.get("environments", {}).get("local")
                    if isinstance(environments, dict)
                    and isinstance(environments.get("environments"), dict)
                    else None
                )
                if isinstance(local, dict):
                    cwd, cwd_rank = _prefer_cwd(cwd, cwd_rank, local.get("cwd"), 2)
                continue

            found_summary = _summary_from_entry(entry)
            if found_summary is not None:
                summary = found_summary
                summary_at = len(turns)
                continue

            if entry_type == "event_msg":
                settings = payload.get("thread_settings")
                if isinstance(settings, dict):
                    if isinstance(settings.get("model"), str):
                        model = settings["model"]
                    cwd, cwd_rank = _prefer_cwd(cwd, cwd_rank, settings.get("cwd"), 3)
                # Codex records the AUTHORITATIVE file edits here, not in the tool
                # call: `patch_apply_end.changes` maps each absolute path to its
                # change type/diff. Without this, `files_touched` starves on Codex
                # sessions (the compactor's deterministic extractor reads
                # tool_input["file_path"]), so emit one apply_patch turn per file.
                if payload.get("type") == "patch_apply_end":
                    changes = payload.get("changes")
                    if isinstance(changes, dict):
                        success = payload.get("success")
                        failed = success is False
                        for file_path, change in changes.items():
                            if not isinstance(file_path, str) or not file_path:
                                continue
                            change_type = (
                                change.get("type") if isinstance(change, dict) else None
                            )
                            turns.append(
                                _make_turn(
                                    actor=self.actor,
                                    base_id=path_session_id,
                                    role=Role.assistant,
                                    timestamp=timestamp,
                                    payload=payload,
                                    tool_name="apply_patch",
                                    tool_input={
                                        "file_path": file_path,
                                        "change_type": change_type
                                        if isinstance(change_type, str)
                                        else None,
                                    },
                                    error="patch apply failed" if failed else None,
                                    model=model,
                                )
                            )
                    continue
                if payload.get("type") != "token_count":
                    continue
                usage = _usage_fields(payload)
                if usage is None:
                    continue
                anchor: Turn | None = None
                for index in range(len(turns) - 1, usage_boundary, -1):
                    if turns[index].role is Role.assistant:
                        anchor = turns[index]
                        break
                if anchor is None:
                    anchor = _make_turn(
                        actor=self.actor,
                        base_id=path_session_id,
                        role=Role.assistant,
                        timestamp=timestamp,
                        payload=payload,
                        model=model,
                    )
                    turns.append(anchor)
                for field, value in usage.items():
                    setattr(anchor, field, value)
                usage_boundary = len(turns) - 1
                continue

            if entry_type != "response_item":
                continue

            item_type = payload.get("type")
            if item_type in ("message", "agent_message", "user_message"):
                role_value = payload.get("role")
                if item_type == "agent_message" and role_value is None:
                    role_value = "assistant"
                if item_type == "user_message" and role_value is None:
                    role_value = "user"
                if role_value not in ("user", "assistant"):
                    continue
                content: object = payload.get("content")
                if content is None:
                    content = payload.get("output")
                if content is None:
                    content = payload.get("text")
                role = Role.user if role_value == "user" else Role.assistant
                item_model = payload.get("model")
                if not isinstance(item_model, str):
                    item_model = model
                for text in _text_blocks(content):
                    turns.append(
                        _make_turn(
                            actor=self.actor,
                            base_id=path_session_id,
                            role=role,
                            timestamp=timestamp,
                            payload=payload,
                            content=text,
                            model=item_model if role is Role.assistant else None,
                        )
                    )
                continue

            if item_type in ("custom_tool_call", "function_call"):
                call_id = payload.get("call_id")
                name = payload.get("name")
                if isinstance(call_id, str) and isinstance(name, str):
                    tool_names[call_id] = name
                raw_input = (
                    payload.get("arguments")
                    if item_type == "function_call"
                    else payload.get("input")
                )
                turns.append(
                    _make_turn(
                        actor=self.actor,
                        base_id=path_session_id,
                        role=Role.assistant,
                        timestamp=timestamp,
                        payload=payload,
                        tool_name=name if isinstance(name, str) else None,
                        tool_input=_tool_input(raw_input),
                        tool_use_id=call_id if isinstance(call_id, str) else None,
                        model=model,
                    )
                )
                continue

            if item_type in ("custom_tool_call_output", "function_call_output"):
                call_id = payload.get("call_id")
                status = payload.get("status")
                error = (
                    str(status)
                    if isinstance(status, str)
                    and status.lower() not in ("completed", "success", "succeeded")
                    else None
                )
                turns.append(
                    _make_turn(
                        actor=self.actor,
                        base_id=path_session_id,
                        role=Role.tool,
                        timestamp=timestamp,
                        payload=payload,
                        tool_name=tool_names.get(call_id) if isinstance(call_id, str) else None,
                        tool_output=_stringify(payload.get("output")),
                        tool_use_id=call_id if isinstance(call_id, str) else None,
                        error=error,
                    )
                )

        final_session_id = session_id or path_session_id
        for index, turn in enumerate(turns):
            turn.id = f"{final_session_id}-{index:06d}"
            turn.session_id = final_session_id
            turn.seq = index
            if turn.role is Role.assistant and turn.model is None:
                turn.model = model

        return turns, CodexFileMeta(
            session_id=final_session_id,
            cwd=cwd,
            git_branch=git_branch,
            mtime=mtime,
            model=model,
            cli_version=cli_version,
            originator=originator,
            source=source_surface,
            compact_summary=summary,
            compact_summary_at=summary_at if summary is not None else None,
        )

    def read_summary(self, source: str) -> CodexSummary | None:
        """Cheaply extract only the newest Codex context-compaction summary."""
        summary: CodexSummary | None = None
        try:
            lines = Path(source).read_text(errors="replace").splitlines()
        except OSError:
            return None
        for raw in lines:
            if '"compacted"' not in raw and '"context_compacted"' not in raw:
                continue
            try:
                entry = json.loads(raw.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                found = _summary_from_entry(entry)
                if found is not None:
                    summary = found
        return summary

    def parse(self, source: str) -> Iterator[Turn]:
        """Collector-protocol entry point."""
        turns, _meta = self.read(source)
        yield from turns


__all__ = [
    "CodexCollector",
    "CodexFileMeta",
    "CodexSummary",
    "DEFAULT_CODEX_DIR",
]
