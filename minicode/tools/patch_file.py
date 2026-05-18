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
    replacements = input_value.get("replacements")
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not isinstance(replacements, list) or not replacements:
        raise ValueError("replacements must be a non-empty array")
    parsed = []
    for item in replacements:
        if not isinstance(item, dict):
            raise ValueError("replacement must be an object")
        search = item.get("search")
        replace = item.get("replace")
        replace_all = item.get("replaceAll", False)
        if not isinstance(search, str) or not search:
            raise ValueError("replacement.search must be a non-empty string")
        if not isinstance(replace, str):
            raise ValueError("replacement.replace must be a string")
        if not isinstance(replace_all, bool):
            raise ValueError("replacement.replaceAll must be a boolean")
        parsed.append({"search": search, "replace": replace, "replaceAll": replace_all})
    return {"path": path, "replacements": parsed}


async def _run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = await resolve_tool_path(context, input_value["path"], "write")
    content = Path(target).read_text(encoding="utf-8")
    applied: list[str] = []
    for index, replacement in enumerate(input_value["replacements"]):
        if replacement["search"] not in content:
            return {"ok": False, "output": f"Replacement {index + 1} not found in {input_value['path']}"}
        if replacement.get("replaceAll"):
            content = content.replace(replacement["search"], replacement["replace"])
            applied.append(f"#{index + 1} replaceAll")
        else:
            content = content.replace(replacement["search"], replacement["replace"], 1)
            applied.append(f"#{index + 1} replaceOnce")
    result = await apply_reviewed_file_change(context, input_value["path"], target, content)
    if not result.get("ok"):
        return result
    return {"ok": True, "output": f"Patched {input_value['path']} with {len(applied)} replacement(s): {', '.join(applied)}"}


patch_file_tool = ToolDefinition(
    name="patch_file",
    description="Apply multiple exact-text replacements to one file in a single operation.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "replacements": {"type": "array", "items": {"type": "object", "properties": {"search": {"type": "string"}, "replace": {"type": "string"}, "replaceAll": {"type": "boolean"}}, "required": ["search", "replace"]}},
        },
        "required": ["path", "replacements"],
    },
    validator=_validate,
    run=_run,
)

patchFileTool = patch_file_tool
