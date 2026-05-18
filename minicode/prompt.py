from __future__ import annotations

from pathlib import Path
from typing import Any


def _maybe_read(file_path: Path) -> str | None:
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError:
        return None


async def build_system_prompt(cwd: str, permission_summary: list[str] | None = None, extras: dict[str, Any] | None = None) -> str:
    permission_summary = permission_summary or []
    extras = extras or {}
    global_claude_md = _maybe_read(Path.home() / ".claude" / "CLAUDE.md")
    project_claude_md = _maybe_read(Path(cwd) / "CLAUDE.md")
    parts = [
        "You are mini-code, a terminal coding assistant.",
        "Default behavior: inspect the repository, use tools, make code changes when appropriate, and explain results clearly.",
        "Prefer reading files, searching code, editing files, and running verification commands over giving purely theoretical advice.",
        f"Current cwd: {cwd}",
        "You can inspect or modify paths outside the current cwd when the user asks, but tool permissions may pause for approval first.",
        "When making code changes, keep them minimal, practical, and working-oriented.",
        "If the user clearly asked you to build, modify, optimize, or generate something, do the work instead of stopping at a plan.",
        "If you need user clarification, call the ask_user tool with one concise question and wait for the user reply. Do not ask clarifying questions as plain assistant text.",
        "Do not choose subjective preferences such as colors, visual style, copy tone, or naming unless the user explicitly told you to decide yourself.",
        "When using read_file, pay attention to the header fields. If it says TRUNCATED: yes, continue reading with a larger offset before concluding that the file itself is cut off.",
        "If the user names a skill or clearly asks for a workflow that matches a listed skill, call load_skill before following it.",
        "Structured response protocol:",
        "- When you are still working and will continue with more tool calls, start your text with <progress>.",
        "- Only when the task is actually complete and you are ready to hand control back, start your text with <final>.",
        "- Use ask_user when clarification is required; that tool ends the turn and waits for user input.",
        "- Do not stop after a progress update. After a <progress> message, continue the task in the next step.",
        "- Plain assistant text without <progress> is treated as a completed assistant message for this turn.",
    ]
    if permission_summary:
        parts.append("Permission context:\n" + "\n".join(permission_summary))
    skills = extras.get("skills") or []
    if skills:
        parts.append("Available skills:\n" + "\n".join(f"- {skill.get('name')}: {skill.get('description')}" for skill in skills))
    else:
        parts.append("Available skills:\n- none discovered")
    mcp_servers = extras.get("mcpServers") or []
    if mcp_servers:
        lines = []
        for server in mcp_servers:
            suffix = f" ({server.get('error')})" if server.get("error") else ""
            protocol = f", protocol={server.get('protocol')}" if server.get("protocol") else ""
            resources = f", resources={server.get('resourceCount')}" if server.get("resourceCount") is not None else ""
            prompts = f", prompts={server.get('promptCount')}" if server.get("promptCount") is not None else ""
            lines.append(f"- {server.get('name')}: {server.get('status')}, tools={server.get('toolCount')}{resources}{prompts}{protocol}{suffix}")
        parts.append("Configured MCP servers:\n" + "\n".join(lines))
        connected = [server for server in mcp_servers if server.get("status") == "connected"]
        if connected:
            hints = ["Connected MCP tools are already exposed in the tool list with names prefixed like mcp__server__tool. To discover callable MCP integrations, inspect the tool list or use /mcp."]
            if any((server.get("resourceCount") or 0) > 0 for server in connected):
                hints.append("Some connected MCP servers also publish resources, so list_mcp_resources/read_mcp_resource can be useful for reading server-provided content.")
            if any((server.get("promptCount") or 0) > 0 for server in connected):
                hints.append("Some connected MCP servers also publish prompts, so list_mcp_prompts/get_mcp_prompt can be useful for fetching server-provided prompt templates.")
            parts.append(" ".join(hints))
    if global_claude_md:
        parts.append(f"Global instructions from ~/.claude/CLAUDE.md:\n{global_claude_md}")
    if project_claude_md:
        parts.append(f"Project instructions from {Path(cwd) / 'CLAUDE.md'}:\n{project_claude_md}")
    return "\n\n".join(parts)


buildSystemPrompt = build_system_prompt
