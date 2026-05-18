from __future__ import annotations

from pathlib import Path
from typing import Any

from ..file_review import apply_reviewed_file_change
from ..tool import ToolDefinition
from ..workspace import resolve_tool_path


def _validate(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict):
        raise ValueError("input must be an object")
    path = input_value.get("path")
    search = input_value.get("search")
    replace = input_value.get("replace")
    replace_all = input_value.get("replaceAll", False)
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not isinstance(search, str) or not search:
        raise ValueError("search must be a non-empty string")
    if not isinstance(replace, str):
        raise ValueError("replace must be a string")
    if not isinstance(replace_all, bool):
        raise ValueError("replaceAll must be a boolean")
    return {"path": path, "search": search, "replace": replace, "replaceAll": replace_all}


async def _run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = await resolve_tool_path(context, input_value["path"], "write")
    original = Path(target).read_text(encoding="utf-8")
    if input_value["search"] not in original:
        return {"ok": False, "output": f"Text not found in {input_value['path']}"}
    next_content = original.replace(input_value["search"], input_value["replace"]) if input_value.get("replaceAll") else original.replace(input_value["search"], input_value["replace"], 1)
    return await apply_reviewed_file_change(context, input_value["path"], target, next_content)


edit_file_tool = ToolDefinition(
    name="edit_file",
    description="Edit a text file by replacing exact text.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "search": {"type": "string"}, "replace": {"type": "string"}, "replaceAll": {"type": "boolean"}},
        "required": ["path", "search", "replace"],
    },
    validator=_validate,
    run=_run,
)

editFileTool = edit_file_tool
