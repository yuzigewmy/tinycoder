"""Multi-agent / sub-agent delegation — mirrors qwen-code agent architecture."""

from .types import (
    AgentLevel,
    AgentTask,
    AgentTaskStatus,
    BUBBLE_APPROVAL_MODE,
    RunConfig,
    SubAgentConfig,
    SubAgentResult,
    VALID_APPROVAL_MODES,
)
from .manager import (
    list_agents,
    load_agent,
    load_session_agents,
    resolve_toolset,
)
from .runner import run_subagent, run_subagents_parallel
from .task_tool import task_tool

__all__ = [
    "AgentLevel",
    "AgentTask",
    "AgentTaskStatus",
    "BUBBLE_APPROVAL_MODE",
    "RunConfig",
    "SubAgentConfig",
    "SubAgentResult",
    "VALID_APPROVAL_MODES",
    "list_agents",
    "load_agent",
    "load_session_agents",
    "resolve_toolset",
    "run_subagent",
    "run_subagents_parallel",
    "task_tool",
]
