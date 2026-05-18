from __future__ import annotations

from pathlib import Path
from typing import Any

from ..tool import ToolDefinition
from ..workspace import resolve_tool_path


def _validate(input_value: Any) -> dict[str, Any]:
    if input_value is None:
        return {}
    if not isinstance(input_value, dict):
        raise ValueError("input must be an object")
    path = input_value.get("path")
    if path is not None and not isinstance(path, str):
        raise ValueError("path must be a string")
    return {"path": path} if path is not None else {}


async def _run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = await resolve_tool_path(context, input_value.get("path") or ".", "list")
    entries = sorted(Path(target).iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines = [f"{'dir ' if entry.is_dir() else 'file'} {entry.name}" for entry in entries[:200]]
    return {"ok": True, "output": "\n".join(lines) if lines else "(empty)"}


list_files_tool = ToolDefinition(
    name="list_files",
    description="List files in a directory relative to the workspace root.",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    validator=_validate,
    run=_run,
)

listFilesTool = list_files_tool
