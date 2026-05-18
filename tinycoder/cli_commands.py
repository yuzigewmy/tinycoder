from __future__ import annotations

from typing import Any

from .config import CLAUDE_SETTINGS_PATH, TINYCODER_MCP_PATH, TINYCODER_PERMISSIONS_PATH, TINYCODER_SETTINGS_PATH, load_runtime_config, save_tinycoder_settings

SLASH_COMMANDS: list[dict[str, str]] = [
    {"name": "/help", "usage": "/help", "description": "Show available slash commands."},
    {"name": "/tools", "usage": "/tools", "description": "List tools available to the coding agent and tool shortcuts."},
    {"name": "/status", "usage": "/status", "description": "Show current model and config source."},
    {"name": "/model", "usage": "/model", "description": "Show the current model."},
    {"name": "/model", "usage": "/model <model-name>", "description": "Persist a model override into ~/.tinycoder/settings.json."},
    {"name": "/config-paths", "usage": "/config-paths", "description": "Show tinycoder and Claude fallback settings paths."},
    {"name": "/skills", "usage": "/skills", "description": "List discovered SKILL.md workflows."},
    {"name": "/mcp", "usage": "/mcp", "description": "Show configured MCP servers and connection state."},
    {"name": "/resume", "usage": "/resume", "description": "Resume a saved session (interactive picker, or /resume <id>)."},
    {"name": "/rename", "usage": "/rename <name>", "description": "Rename the current session."},
    {"name": "/new", "usage": "/new", "description": "Clear saved session and start fresh."},
    {"name": "/fork", "usage": "/fork", "description": "Fork current session into a new independent session."},
    {"name": "/permissions", "usage": "/permissions", "description": "Show tinycoder permission storage path."},
    {"name": "/exit", "usage": "/exit", "description": "Exit tinycoder."},
    {"name": "/ls", "usage": "/ls [path]", "description": "List files in a directory."},
    {"name": "/grep", "usage": "/grep <pattern>::[path]", "description": "Search text in files."},
    {"name": "/read", "usage": "/read <path>", "description": "Read a file directly."},
    {"name": "/write", "usage": "/write <path>::<content>", "description": "Write a file directly."},
    {"name": "/modify", "usage": "/modify <path>::<content>", "description": "Replace a file, showing a reviewable diff before applying it."},
    {"name": "/edit", "usage": "/edit <path>::<search>::<replace>", "description": "Edit a file by exact replacement."},
    {"name": "/patch", "usage": "/patch <path>::<search1>::<replace1>::<search2>::<replace2>...", "description": "Apply multiple replacements to one file in one command."},
    {"name": "/cmd", "usage": "/cmd [cwd::]<command> [args...]", "description": "Run an allowed development command directly, optionally in another directory."},
    {"name": "/compact", "usage": "/compact", "description": "Compress conversation context to free up context window space."},
    {"name": "/collapse", "usage": "/collapse", "description": "Project old safe context spans into summaries without deleting the transcript."},
    {"name": "/snip", "usage": "/snip", "description": "Remove a safe middle segment of conversation context without calling the model."},
]


def format_slash_commands() -> str:
    return "\n".join(f"{command['usage']}  {command['description']}" for command in SLASH_COMMANDS)


def find_matching_slash_commands(input_text: str) -> list[str]:
    return [command["usage"] for command in SLASH_COMMANDS if command["usage"].startswith(input_text)]


async def try_handle_local_command(input_text: str, context: dict[str, Any] | None = None) -> str | None:
    context = context or {}
    if input_text in {"/", "/help"}:
        return format_slash_commands()
    if input_text == "/config-paths":
        return "\n".join([f"tinycoder settings: {TINYCODER_SETTINGS_PATH}", f"tinycoder permissions: {TINYCODER_PERMISSIONS_PATH}", f"tinycoder mcp: {TINYCODER_MCP_PATH}", f"compat fallback: {CLAUDE_SETTINGS_PATH}"])
    if input_text == "/permissions":
        return f"permission store: {TINYCODER_PERMISSIONS_PATH}"
    if input_text == "/skills":
        tools = context.get("tools")
        skills = tools.get_skills() if tools else []
        if not skills:
            return "No skills discovered. Add skills under ~/.tinycoder/skills/<name>/SKILL.md, .tinycoder/skills/<name>/SKILL.md, .claude/skills/<name>/SKILL.md, or ~/.claude/skills/<name>/SKILL.md."
        return "\n".join(f"{skill.get('name')}  {skill.get('description')}  [{skill.get('source')}]" for skill in skills)
    if input_text == "/mcp":
        tools = context.get("tools")
        servers = tools.get_mcp_servers() if tools else []
        if not servers:
            return "No MCP servers configured. Add mcpServers to ~/.tinycoder/settings.json, ~/.tinycoder/mcp.json, or project .mcp.json."
        lines = []
        for server in servers:
            suffix = f"  error={server.get('error')}" if server.get("error") else ""
            protocol = f"  protocol={server.get('protocol')}" if server.get("protocol") else ""
            resources = f"  resources={server.get('resourceCount')}" if server.get("resourceCount") is not None else ""
            prompts = f"  prompts={server.get('promptCount')}" if server.get("promptCount") is not None else ""
            lines.append(f"{server.get('name')}  status={server.get('status')}  tools={server.get('toolCount')}{resources}{prompts}{protocol}{suffix}")
        return "\n".join(lines)
    if input_text == "/tools":
        tools = context.get("tools")
        if not tools:
            return "No tool registry available."
        tool_lines = [f"{tool.name}  {tool.description}" for tool in tools.list()]
        shortcuts = ["/ls [path]", "/grep <pattern>::[path]", "/read <path>", "/write <path>::<content>", "/modify <path>::<content>", "/edit <path>::<search>::<replace>", "/patch <path>::<search>::<replace>...", "/cmd [cwd::]<command> [args...]"]
        return "Tools:\n" + "\n".join(tool_lines) + "\n\nShortcuts:\n" + "\n".join(shortcuts)
    if input_text == "/status":
        try:
            runtime = await load_runtime_config()
            return "\n".join([f"model: {runtime.get('model')}", f"baseUrl: {runtime.get('baseUrl')}", f"auth: {'ANTHROPIC_AUTH_TOKEN' if runtime.get('authToken') else 'ANTHROPIC_API_KEY'}", f"mcp servers: {len(runtime.get('mcpServers') or {})}", str(runtime.get("sourceSummary") or "")])
        except Exception as error:
            return f"status unavailable: {error}"
    if input_text == "/model":
        try:
            runtime = await load_runtime_config()
            return f"current model: {runtime.get('model')}"
        except Exception as error:
            return f"model unavailable: {error}"
    if input_text.startswith("/model "):
        model = input_text[len("/model "):].strip()
        if not model:
            return "用法: /model <model-name>"
        await save_tinycoder_settings({"model": model})
        return f"saved model={model} to {TINYCODER_SETTINGS_PATH}"
    return None


def complete_slash_command(line: str) -> tuple[list[str], str]:
    hits = [command["usage"] for command in SLASH_COMMANDS if command["usage"].startswith(line)]
    return hits or [command["usage"] for command in SLASH_COMMANDS], line


formatSlashCommands = format_slash_commands
findMatchingSlashCommands = find_matching_slash_commands
tryHandleLocalCommand = try_handle_local_command
completeSlashCommand = complete_slash_command
