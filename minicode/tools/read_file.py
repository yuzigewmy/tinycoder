from __future__ import annotations

from pathlib import Path
from typing import Any

from ..tool import ToolDefinition
from ..workspace import resolve_tool_path

DEFAULT_READ_LIMIT = 8000
MAX_READ_LIMIT = 20000


def _validate(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict) or not isinstance(input_value.get("path"), str):
        raise ValueError("path must be a string")
    path = input_value["path"]
    offset = input_value.get("offset", 0)
    limit = input_value.get("limit", DEFAULT_READ_LIMIT)
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if not isinstance(limit, int) or limit < 1 or limit > MAX_READ_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {MAX_READ_LIMIT}")
    return {"path": path, "offset": offset, "limit": limit}


async def _run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = await resolve_tool_path(context, input_value["path"], "read")
    content = Path(target).read_text(encoding="utf-8")
    offset = max(0, int(input_value.get("offset") or 0))
    limit = min(MAX_READ_LIMIT, int(input_value.get("limit") or DEFAULT_READ_LIMIT))
    end = min(len(content), offset + limit)
    chunk = content[offset:end]
    truncated = end < len(content)
    header = "\n".join([
        f"FILE: {input_value['path']}",
        f"OFFSET: {offset}",
        f"END: {end}",
        f"TOTAL_CHARS: {len(content)}",
        f"TRUNCATED: yes - call read_file again with offset {end}" if truncated else "TRUNCATED: no",
        "",
    ])
    return {"ok": True, "output": header + chunk}


read_file_tool = ToolDefinition(
    name="read_file",
    description="Read a UTF-8 text file relative to the workspace root. Large files can be read in chunks via offset and limit.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "offset": {"type": "number"}, "limit": {"type": "number"}},
        "required": ["path"],
    },
    validator=_validate,
    run=_run,
)

readFileTool = read_file_tool
