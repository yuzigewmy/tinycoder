"""Sub-agent runner — mirrors qwen-code AgentHeadless / TeamManager.spawnTeammate.

Creates an isolated agent_loop context for the sub-agent with restricted toolset,
runs it to completion (or max_turns), and returns the result.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..agent_loop import run_agent_turn
from ..tool import ToolRegistry
from ..turn_controller import TurnController, TurnBudget
from .types import SubAgentConfig, SubAgentResult


async def run_subagent(
    config: SubAgentConfig,
    task_description: str,
    model: Any,
    parent_tools: ToolRegistry,
    cwd: str,
    permissions: Any = None,
) -> SubAgentResult:
    """Run a sub-agent with isolated context and restricted tools.

    Max turns is resolved as: config.max_turns (top-level cc field wins)
    or config.run_config.max_turns as fallback.
    """
    all_tool_names = [t.name for t in parent_tools.list()]
    allowed = set(config.tools) if config.tools else set(all_tool_names)
    allowed.difference_update(config.disallowed_tools)

    restricted = ToolRegistry([
        t for t in parent_tools.list() if t.name in allowed
    ])

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": task_description},
    ]

    # max_turns: top-level wins over runConfig (qwen-code semantics)
    max_turns = config.max_turns

    turns_used = 0
    try:
        for turn in range(max_turns):
            budget = TurnBudget.default()
            controller = TurnController(budget)
            controller.before_model_step()

            messages = await run_agent_turn({
                "model": model,
                "tools": restricted,
                "messages": messages,
                "cwd": cwd,
                "permissions": permissions,
                "modelName": config.model if config.model != "inherit" else "",
                "instructionContext": "",
            })

            turns_used = turn + 1
    except Exception as exc:
        return SubAgentResult(
            agent_name=config.name,
            ok=False,
            output=f"Sub-agent '{config.name}' failed: {exc}",
            turns_used=turns_used,
        )

    # Extract the final assistant message
    final_output = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            final_output = str(msg["content"])
            break

    if not final_output:
        final_output = f"Sub-agent '{config.name}' completed but produced no output."

    return SubAgentResult(
        agent_name=config.name,
        ok=True,
        output=final_output,
        turns_used=turns_used,
    )


async def run_subagents_parallel(
    configs: list[SubAgentConfig],
    task_descriptions: list[str],
    model: Any,
    parent_tools: ToolRegistry,
    cwd: str,
    permissions: Any = None,
) -> list[SubAgentResult]:
    """Run multiple sub-agents in parallel via asyncio.gather."""
    tasks = [
        run_subagent(cfg, desc, model, parent_tools, cwd, permissions)
        for cfg, desc in zip(configs, task_descriptions)
    ]
    return await asyncio.gather(*tasks)
