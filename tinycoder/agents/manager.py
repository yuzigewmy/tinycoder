"""Sub-agent config manager — mirrors qwen-code SubagentManager.

Scans ~/.tinycoder/agents/, .tinycoder/agents/, and builtin/ for *.md
files with YAML frontmatter. Layer priority: session > project > user >
extension > builtin.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

from .types import (
    AgentLevel,
    RunConfig,
    SubAgentConfig,
    VALID_APPROVAL_MODES,
)
from ..config import TINYCODER_DIR

BUILTIN_AGENTS_DIR = Path(__file__).parent / "builtin"
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# In-memory session agents (runtime-injected, not persisted to disk)
_session_agents: list[SubAgentConfig] = []


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter via PyYAML — mirrors qwen-code's parseYaml."""
    try:
        result = yaml.safe_load(text)
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        return {}


def _coerce_list(value: object) -> list[str]:
    """Normalise tools/disallowedTools: array or comma-separated string."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _load_agent_from_file(filepath: Path, level: AgentLevel) -> Optional[SubAgentConfig]:
    """Load a single agent config from a markdown file."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    match = FRONTMATTER_RE.match(text)
    if not match:
        return None

    frontmatter = _parse_frontmatter(match.group(1))
    system_prompt = text[match.end():].strip()

    name = str(frontmatter.get("name") or filepath.stem)
    description = str(frontmatter.get("description") or "")

    # Tools
    tools = _coerce_list(frontmatter.get("tools") or [])
    disallowed_tools = _coerce_list(frontmatter.get("disallowedTools") or [])

    # Model
    model = str(frontmatter.get("model") or "inherit")

    # maxTurns — support both top-level (cc style) and nested runConfig.max_turns
    max_turns_raw = frontmatter.get("maxTurns")
    if max_turns_raw is not None:
        max_turns = int(max_turns_raw)
    else:
        max_turns = 10

    # runConfig
    run_config_raw = frontmatter.get("runConfig")
    if isinstance(run_config_raw, dict):
        run_config = RunConfig(
            max_time_minutes=run_config_raw.get("max_time_minutes"),
            max_turns=run_config_raw.get("max_turns"),
        )
        # Nested max_turns wins over top-level only when explicitly set
        if run_config.max_turns is not None:
            max_turns = run_config.max_turns
    else:
        run_config = RunConfig()

    # approvalMode
    approval_mode_raw = frontmatter.get("approvalMode")
    approval_mode: Optional[str] = None
    if isinstance(approval_mode_raw, str) and approval_mode_raw.strip():
        mode = approval_mode_raw.strip()
        if mode in VALID_APPROVAL_MODES:
            approval_mode = mode

    # color
    color = frontmatter.get("color")
    if not isinstance(color, str):
        color = None

    # background
    background_raw = frontmatter.get("background")
    background = background_raw if isinstance(background_raw, bool) else None

    # mcpServers
    mcp_servers = frontmatter.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = None

    return SubAgentConfig(
        name=name,
        description=description,
        system_prompt=system_prompt,
        tools=tools,
        disallowed_tools=disallowed_tools,
        model=model,
        max_turns=max_turns,
        level=level,
        file_path=str(filepath),
        is_builtin=(level == "builtin"),
        approval_mode=approval_mode,
        run_config=run_config,
        color=color,
        background=background,
        mcp_servers=mcp_servers,
    )


def _scan_directory(directory: Path, level: AgentLevel) -> dict[str, SubAgentConfig]:
    """Scan a directory for *.md agent config files."""
    agents: dict[str, SubAgentConfig] = {}
    if not directory.is_dir():
        return agents
    for filepath in sorted(directory.glob("*.md")):
        config = _load_agent_from_file(filepath, level)
        if config is not None and config.name:
            agents[config.name] = config
    return agents


def load_session_agents(agents: list[SubAgentConfig]) -> None:
    """Load session-level agents at runtime — mirrors loadSessionSubagents."""
    global _session_agents
    _session_agents = [
        SubAgentConfig(**{**a.__dict__, "level": "session", "file_path": f"<session:{a.name}>"})
        for a in agents
    ]


def list_agents() -> list[SubAgentConfig]:
    """List all available sub-agents with layer priority.

    session > project > user > extension > builtin
    """
    merged: dict[str, SubAgentConfig] = {}

    # Builtin (lowest)
    for name, cfg in _scan_directory(BUILTIN_AGENTS_DIR, "builtin").items():
        merged[name] = cfg

    # User (~/.tinycoder/agents/)
    for name, cfg in _scan_directory(TINYCODER_DIR / "agents", "user").items():
        merged[name] = cfg

    # Project (.tinycoder/agents/)
    for name, cfg in _scan_directory(Path.cwd() / ".tinycoder" / "agents", "project").items():
        merged[name] = cfg

    # Session (highest)
    for cfg in _session_agents:
        merged[cfg.name] = cfg

    return sorted(merged.values(), key=lambda a: a.name)


def load_agent(name: str) -> Optional[SubAgentConfig]:
    """Load a specific sub-agent by name."""
    agents = list_agents()
    for agent in agents:
        if agent.name == name:
            return agent
    return None


def resolve_toolset(
    config: SubAgentConfig,
    available_tools: list[str],
) -> list[str]:
    """Resolve which tools a sub-agent can use.

    If config.tools is non-empty, restrict to that allowlist (minus disallowed).
    If empty, inherit all available tools (minus disallowed).
    """
    if config.tools:
        base = [t for t in config.tools if t in available_tools]
    else:
        base = list(available_tools)

    if config.disallowed_tools:
        exclude = set(config.disallowed_tools)
        base = [t for t in base if t not in exclude]

    return base
