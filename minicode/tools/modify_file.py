from __future__ import annotations

from typing import Any

from .write_file import _validate as _validate_write
from ..file_review import apply_reviewed_file_change
from ..tool import ToolDefinition
from ..workspace import resolve_tool_path


async def _run(input_value: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    target = await resolve_tool_path(context, input_value["path"], "write")
    return await apply_reviewed_file_change(context, input_value["path"], target, input_value["content"])


modify_file_tool = ToolDefinition(
    name="modify_file",
    description="Replace a file with reviewed content so the user can approve the diff first.",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    validator=_validate_write,
    run=_run,
)

modifyFileTool = modify_file_tool
