from __future__ import annotations

from typing import Any

from ..file_review import apply_reviewed_file_change
from ..tool import ToolDefinition
from ..workspace import resolve_tool_path


def _validate(input_value: Any) -> dict[str, str]:
    if not isinstance(input_value, dict) or not isinstance(input_value.get("path"), str) or not input_value["path"]:
        raise ValueError("path must be a non-empty string")
    if not isinstance(input_value.get("content"), str):
        raise ValueError("content must be a string")
    return {"path": input_value["path"], "content": input_value["content"]}


async def _run(input_value: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    target = await resolve_tool_path(context, input_value["path"], "write")
    return await apply_reviewed_file_change(context, input_value["path"], target, input_value["content"])


write_file_tool = ToolDefinition(
    name="write_file",
    description="Write a UTF-8 text file relative to the workspace root.",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    validator=_validate,
    run=_run,
)

writeFileTool = write_file_tool
