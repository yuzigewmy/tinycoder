"""Agent type definitions — mirrors qwen-code subagents/types.ts.

SubAgentConfig — loaded from ~/.tinycoder/agents/*.md markdown files.
SubAgentResult — what a sub-agent returns to the parent.
AgentTask — in-process task tracker (simplified, no file locks needed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Any

AgentLevel = Literal["session", "project", "user", "extension", "builtin"]
AgentTaskStatus = Literal["running", "completed", "failed"]

# qwen-code: BUBBLE_APPROVAL_MODE = 'bubble' — subagent-only, surfaces
# confirmations to parent session instead of auto-denying in background.
BUBBLE_APPROVAL_MODE = "bubble"
VALID_APPROVAL_MODES = frozenset({
    "default", "auto-edit", "plan", "yolo", BUBBLE_APPROVAL_MODE,
})


@dataclass
class RunConfig:
    """Mirrors qwen-code RunConfig: max_time_minutes, max_turns."""
    max_time_minutes: Optional[float] = None
    max_turns: Optional[int] = None


@dataclass
class SubAgentConfig:
    """Configuration for a sub-agent, loaded from markdown frontmatter."""
    name: str
    description: str
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: str = "inherit"
    max_turns: int = 10
    level: AgentLevel = "user"
    file_path: str = ""
    is_builtin: bool = False
    approval_mode: Optional[str] = None  # qwen-code: approvalMode
    run_config: RunConfig = field(default_factory=RunConfig)
    color: Optional[str] = None
    background: Optional[bool] = None
    mcp_servers: Optional[dict[str, Any]] = None


@dataclass
class SubAgentResult:
    """Result returned by a sub-agent after execution."""
    agent_name: str
    ok: bool
    output: str
    turns_used: int = 0


@dataclass
class AgentTask:
    """In-process task tracker for sub-agent delegation."""
    task_id: str
    sub_agent_name: str
    description: str
    status: AgentTaskStatus = "running"
    result: Optional[SubAgentResult] = None
