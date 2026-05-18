from __future__ import annotations

from typing import Any

from ..skills import load_skill
from ..tool import ToolDefinition


def _validate(input_value: Any) -> dict[str, str]:
    if not isinstance(input_value, dict) or not isinstance(input_value.get("name"), str) or not input_value["name"].strip():
        raise ValueError("name must be a non-empty string")
    return {"name": input_value["name"]}


def create_load_skill_tool(cwd: str) -> ToolDefinition:
    async def _run(input_value: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
        skill = await load_skill(cwd, input_value["name"])
        if not skill:
            return {"ok": False, "output": f"Unknown skill: {input_value['name']}"}
        return {"ok": True, "output": "\n".join([f"SKILL: {skill['name']}", f"SOURCE: {skill['source']}", f"PATH: {skill['path']}", "", skill["content"]])}

    return ToolDefinition(
        name="load_skill",
        description="Load the full contents of a named SKILL.md file so you can follow that workflow accurately.",
        input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        validator=_validate,
        run=_run,
    )


createLoadSkillTool = create_load_skill_tool
