from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from ..config import MINI_CODE_DIR

TOOL_RESULTS_SUBDIR = "tool-results"
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000
MAX_TOOL_RESULTS_PER_BATCH_CHARS = 200_000
PREVIEW_SIZE_CHARS = 2_000
_SESSION_ID = re.sub(r"[^a-zA-Z0-9._-]", "_", str(uuid.uuid4()))


def create_content_replacement_state() -> dict[str, Any]:
    return {"seenIds": set(), "replacements": {}}


def _sanitize_path_segment(value: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", value)
    return sanitized or str(uuid.uuid4())


def _tool_results_dir() -> Path:
    return MINI_CODE_DIR / TOOL_RESULTS_SUBDIR / _SESSION_ID


def _tool_result_path(tool_use_id: str) -> Path:
    root = _tool_results_dir().resolve()
    candidate = (root / f"{_sanitize_path_segment(tool_use_id)}.txt").resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return root / f"{uuid.uuid4()}.txt"
    return candidate


def is_already_persisted_output(content: str) -> bool:
    return content.startswith(PERSISTED_OUTPUT_TAG)


def generate_preview(content: str) -> dict[str, Any]:
    if len(content) <= PREVIEW_SIZE_CHARS:
        return {"preview": content, "hasMore": False}
    truncated = content[:PREVIEW_SIZE_CHARS]
    last_newline = truncated.rfind("\n")
    cut = last_newline if last_newline > PREVIEW_SIZE_CHARS * 0.5 else PREVIEW_SIZE_CHARS
    return {"preview": content[:cut], "hasMore": True}


def format_chars(chars: int) -> str:
    if chars >= 1_000_000:
        return f"{chars / 1_000_000:.1f}M chars"
    if chars >= 1_000:
        return f"{round(chars / 1_000)}K chars"
    return f"{chars} chars"


def normalize_tool_result_content(content: Any) -> str:
    if content is None:
        return ""
    return content if isinstance(content, str) else str(content)


async def persist_tool_result(content: str, tool_use_id: str) -> dict[str, Any] | None:
    filepath = _tool_result_path(tool_use_id)
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        if not filepath.exists():
            filepath.write_text(content, encoding="utf-8")
    except OSError:
        return None
    preview = generate_preview(content)
    return {"filepath": str(filepath), "originalSize": len(content), **preview}


def build_persisted_tool_result_message(result: dict[str, Any]) -> str:
    parts = [
        PERSISTED_OUTPUT_TAG,
        f"Output too large ({format_chars(int(result['originalSize']))}). Full output saved to: {result['filepath']}",
        "",
        f"Preview (first {format_chars(PREVIEW_SIZE_CHARS)}):",
        result["preview"],
    ]
    if result.get("hasMore"):
        parts.append("...")
    parts.append(PERSISTED_OUTPUT_CLOSING_TAG)
    return "\n".join(parts)


async def replace_large_tool_result(result: dict[str, Any], state_or_threshold: dict[str, Any] | int | None = None, maybe_threshold: int = DEFAULT_MAX_RESULT_SIZE_CHARS) -> dict[str, Any]:
    state = state_or_threshold if isinstance(state_or_threshold, dict) else None
    threshold = state_or_threshold if isinstance(state_or_threshold, int) else maybe_threshold
    content = normalize_tool_result_content(result.get("content"))
    normalized = {**result, "content": content}
    replacements = state.get("replacements") if state else None
    seen = state.get("seenIds") if state else None
    if replacements is not None and result.get("toolUseId") in replacements:
        return {**normalized, "content": replacements[result.get("toolUseId")]}
    if len(content.strip()) == 0:
        if seen is not None:
            seen.add(result.get("toolUseId"))
        return {**normalized, "content": f"({result.get('toolName')} completed with no output)"}
    if is_already_persisted_output(content):
        if seen is not None:
            seen.add(result.get("toolUseId"))
        if replacements is not None:
            replacements[result.get("toolUseId")] = content
        return normalized
    if len(content) <= threshold:
        return normalized
    persisted = await persist_tool_result(content, str(result.get("toolUseId")))
    if not persisted:
        return normalized
    replacement = build_persisted_tool_result_message(persisted)
    if seen is not None:
        seen.add(result.get("toolUseId"))
    if replacements is not None:
        replacements[result.get("toolUseId")] = replacement
    return {**normalized, "content": replacement}


async def apply_tool_result_budget(results: list[dict[str, Any]], state: dict[str, Any], limit: int = MAX_TOOL_RESULTS_PER_BATCH_CHARS) -> dict[str, Any]:
    if not results:
        return {"results": results, "newlyReplaced": []}
    replacements: dict[str, str] = {}
    fresh_candidates: list[dict[str, Any]] = []
    visible_size = 0
    state.setdefault("seenIds", set())
    state.setdefault("replacements", {})
    for result in results:
        tool_id = result.get("toolUseId")
        content = normalize_tool_result_content(result.get("content"))
        previous = state["replacements"].get(tool_id)
        if previous is not None:
            replacements[tool_id] = previous
            visible_size += len(previous)
            continue
        if tool_id in state["seenIds"]:
            visible_size += len(content)
            continue
        if len(content.strip()) == 0:
            state["seenIds"].add(tool_id)
            continue
        if is_already_persisted_output(content):
            state["seenIds"].add(tool_id)
            state["replacements"][tool_id] = content
            replacements[tool_id] = content
            visible_size += len(content)
            continue
        visible_size += len(content)
        fresh_candidates.append({"toolUseId": tool_id, "content": content, "size": len(content)})
    newly: list[dict[str, Any]] = []
    for candidate in sorted(fresh_candidates, key=lambda c: (-c["size"], str(c["toolUseId"]))):
        if visible_size <= limit:
            break
        persisted = await persist_tool_result(candidate["content"], str(candidate["toolUseId"]))
        state["seenIds"].add(candidate["toolUseId"])
        if not persisted:
            continue
        replacement = build_persisted_tool_result_message(persisted)
        replacements[candidate["toolUseId"]] = replacement
        state["replacements"][candidate["toolUseId"]] = replacement
        visible_size = visible_size - candidate["size"] + len(replacement)
        newly.append({"kind": "tool-result", "toolUseId": candidate["toolUseId"], "replacement": replacement})
    for c in fresh_candidates:
        state["seenIds"].add(c["toolUseId"])
    if not replacements:
        return {"results": results, "newlyReplaced": newly}
    return {
        "results": [{**r, "content": replacements.get(r.get("toolUseId"), r.get("content"))} for r in results],
        "newlyReplaced": newly,
    }

createContentReplacementState = create_content_replacement_state
normalizeToolResultContent = normalize_tool_result_content
replaceLargeToolResult = replace_large_tool_result
applyToolResultBudget = apply_tool_result_budget
