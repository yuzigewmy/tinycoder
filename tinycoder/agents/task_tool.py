"""Task tool — lets the model delegate work to sub-agents.

Mirrors qwen-code's task tool (subagent dispatch).
The model calls task(agent_name, description) to spawn a sub-agent
with restricted tools and get its result back.
"""

from __future__ import annotations

from typing import Any

from ..tool import ToolDefinition
from .manager import load_agent, list_agents
from .runner import run_subagent
from .types import SubAgentConfig


def _validate(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict):
        raise ValueError("task input must be an object")
    agent_name = input_value.get("agent_name")
    if not isinstance(agent_name, str) or not agent_name.strip():
        raise ValueError("agent_name must be a non-empty string")
    description = input_value.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description must be a non-empty string")
    model_override = input_value.get("model")
    if model_override is not None and not isinstance(model_override, str):
        raise ValueError("model must be a string")
    return {
        "agent_name": agent_name.strip(),
        "description": description.strip(),
        "model": model_override or None,
    }


async def _run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    agent_name = input_value["agent_name"]
    description = input_value["description"]
    model_override = input_value.get("model")

    # If agent_name is "*", list available agents for the model
    if agent_name == "*":
        agents = list_agents()
        if not agents:
            return {"ok": False, "output": "No sub-agents available. Create agent configs in ~/.tinycoder/agents/ or .tinycoder/agents/."}
        listing = "\n".join(
            f"- **{a.name}**: {a.description}"
            for a in agents
        )
        return {
            "ok": True,
            "output": (
                "Available sub-agents:\n\n"
                f"{listing}\n\n"
                "Call task() again with a specific agent_name to delegate work."
            ),
        }

    config = load_agent(agent_name)
    if config is None:
        available = ", ".join(a.name for a in list_agents())
        hint = f" Available: {available}" if available else " No sub-agents configured."
        return {
            "ok": False,
            "output": f"Sub-agent '{agent_name}' not found.{hint}",
        }

    # Apply model override
    if model_override:
        config.model = model_override

    model = context.get("model")
    tools = context.get("tools")
    cwd = context.get("cwd", ".")
    permissions = context.get("permissions")

    if not model or not tools:
        return {"ok": False, "output": "task tool: model or tools not available in context"}

    # Run the sub-agent
    result = await run_subagent(
        config=config,
        task_description=description,
        model=model,
        parent_tools=tools,
        cwd=str(cwd),
        permissions=permissions,
    )

    status = "completed" if result.ok else "failed"
    return {
        "ok": result.ok,
        "output": (
            f"[sub-agent: {result.agent_name}] ({status}, {result.turns_used} turns)\n\n"
            f"{result.output}"
        ),
    }


task_tool = ToolDefinition(
    name="task",
    description=(
        "Launch a sub-agent to handle a specific task. "
        "Use agent_name='*' to list available agents, then call again with "
        "a specific agent name and description of the work to delegate."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": "Name of the sub-agent to use (or '*' to list available agents).",
            },
            "description": {
                "type": "string",
                "description": "Detailed description of the task to delegate.",
            },
            "model": {
                "type": "string",
                "description": "Optional model override for this sub-agent.",
            },
        },
        "required": ["agent_name", "description"],
    },
    validator=_validate,
    run=_run,
)
