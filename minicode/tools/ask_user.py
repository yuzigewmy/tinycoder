from __future__ import annotations

from typing import Any

from ..tool import ToolDefinition


def _validate(input_value: Any) -> dict[str, str]:
    if not isinstance(input_value, dict) or not isinstance(input_value.get("question"), str) or not input_value["question"].strip():
        raise ValueError("question must be a non-empty string")
    return {"question": input_value["question"]}


async def _run(input_value: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    question = input_value["question"].strip()
    return {"ok": True, "output": question, "awaitUser": True}


ask_user_tool = ToolDefinition(
    name="ask_user",
    description="Ask the user a clarifying question and stop the current turn until the user replies.",
    input_schema={"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    validator=_validate,
    run=_run,
)

askUserTool = ask_user_tool
