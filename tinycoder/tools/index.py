from __future__ import annotations

from typing import Any

from ..mcp import create_mcp_backed_tools
from ..skills import discover_skills
from ..tool import ToolRegistry
from .ask_user import ask_user_tool
from .edit_file import edit_file_tool
from .grep_files import grep_files_tool
from .list_files import list_files_tool
from .load_skill import create_load_skill_tool
from .modify_file import modify_file_tool
from .patch_file import patch_file_tool
from .read_file import read_file_tool
from .run_command import run_command_tool
from .web_fetch import web_fetch_tool
from .web_search import web_search_tool
from .write_file import write_file_tool


def summarize_server_endpoint(config: dict[str, Any]) -> str:
    remote_url = str(config.get("url") or "").strip()
    if remote_url:
        return remote_url
    command = str(config.get("command") or "").strip()
    args = " ".join(str(arg) for arg in config.get("args") or [])
    return f"{command} {args}".strip()


def build_connecting_mcp_summaries(mcp_servers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for name, config in mcp_servers.items():
        protocol = config.get("protocol")
        summaries.append({
            "name": name,
            "command": summarize_server_endpoint(config),
            "status": "disabled" if config.get("enabled") is False else "connecting",
            "toolCount": 0,
            "protocol": None if protocol in (None, "auto") else protocol,
        })
    return summaries


async def create_default_tool_registry(args: dict[str, Any]) -> ToolRegistry:
    cwd = args["cwd"]
    runtime = args.get("runtime") or {}
    skills = await discover_skills(cwd)
    mcp_servers = runtime.get("mcpServers") or {}
    return ToolRegistry([
        ask_user_tool,
        list_files_tool,
        grep_files_tool,
        read_file_tool,
        write_file_tool,
        modify_file_tool,
        edit_file_tool,
        patch_file_tool,
        run_command_tool,
        create_load_skill_tool(cwd),
        web_fetch_tool,
        web_search_tool,
    ], {"skills": skills, "mcpServers": build_connecting_mcp_summaries(mcp_servers)})


async def hydrate_mcp_tools(args: dict[str, Any]) -> None:
    runtime = args.get("runtime") or {}
    mcp = await create_mcp_backed_tools({"cwd": args["cwd"], "mcpServers": runtime.get("mcpServers") or {}})
    tools: ToolRegistry = args["tools"]
    tools.add_tools(mcp.get("tools") or [])
    tools.set_mcp_servers(mcp.get("servers") or [])
    tools.add_disposer(mcp["dispose"])


createDefaultToolRegistry = create_default_tool_registry
hydrateMcpTools = hydrate_mcp_tools
buildConnectingMcpSummaries = build_connecting_mcp_summaries
