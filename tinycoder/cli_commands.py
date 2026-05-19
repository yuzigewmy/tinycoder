from __future__ import annotations

import os
from typing import Any

from .config import (
    CLAUDE_SETTINGS_PATH,
    TINYCODER_MCP_PATH,
    TINYCODER_PERMISSIONS_PATH,
    TINYCODER_SETTINGS_PATH,
    load_effective_settings,
    load_runtime_config,
    save_tinycoder_settings,
)

SUPPORTED_MODEL_PROVIDERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "label": "Anthropic Claude",
        "model_env": "ANTHROPIC_MODEL",
        "api_key_env": "ANTHROPIC_API_KEY",
        "auth_token_env": "ANTHROPIC_AUTH_TOKEN",
        "base_url_env": "ANTHROPIC_BASE_URL",
        "default_model": "claude-3-5-sonnet-latest",
        "default_base_url": "https://api.anthropic.com",
    },
    "qwen": {
        "label": "通义千问 / 阿里云百炼",
        "model_env": "DASHSCOPE_MODEL",
        "api_key_env": "DASHSCOPE_API_KEY",
        "auth_token_env": "DASHSCOPE_AUTH_TOKEN",
        "base_url_env": "DASHSCOPE_BASE_URL",
        "default_model": "qwen-plus",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
}

PROVIDER_ALIASES = {
    "claude": "anthropic",
    "anthropic": "anthropic",
    "qwen": "qwen",
    "dashscope": "qwen",
    "aliyun": "qwen",
}

SLASH_COMMANDS: list[dict[str, str]] = [
    {"name": "/help", "usage": "/help", "description": "查看所有可用命令。"},
    {"name": "/tools", "usage": "/tools", "description": "查看 Agent 可用工具和本地快捷命令。"},
    {"name": "/status", "usage": "/status", "description": "查看当前模型、供应商、API Key 状态和配置来源。"},
    {"name": "/providers", "usage": "/providers", "description": "查看支持的模型供应商。"},
    {"name": "/provider", "usage": "/provider", "description": "查看当前模型供应商。"},
    {"name": "/provider", "usage": "/provider <anthropic|qwen>", "description": "切换模型供应商并持久化保存。"},
    {"name": "/model", "usage": "/model", "description": "查看当前模型名称。"},
    {"name": "/model", "usage": "/model <model-name>", "description": "切换当前供应商的模型名称并持久化保存。"},
    {"name": "/apikey", "usage": "/apikey", "description": "脱敏查看当前供应商的 API Key。"},
    {"name": "/apikey", "usage": "/apikey <api-key>", "description": "切换当前供应商的 API Key 并持久化保存。"},
    {"name": "/base-url", "usage": "/base-url", "description": "查看当前供应商的 Base URL。"},
    {"name": "/base-url", "usage": "/base-url <url>", "description": "切换当前供应商的 Base URL 并持久化保存。"},
    {"name": "/use", "usage": "/use <provider> <model> [api-key] [base-url]", "description": "一次性切换供应商、模型、API Key 和可选 Base URL。"},
    {"name": "/config-paths", "usage": "/config-paths", "description": "查看 TinyCoder 配置、权限和 MCP 配置文件路径。"},
    {"name": "/skills", "usage": "/skills", "description": "查看已发现的 SKILL.md 工作流。"},
    {"name": "/mcp", "usage": "/mcp", "description": "查看 MCP Server 配置和连接状态。"},
    {"name": "/resume", "usage": "/resume", "description": "查看可恢复的历史会话。"},
    {"name": "/resume", "usage": "/resume <id>", "description": "恢复指定会话。"},
    {"name": "/rename", "usage": "/rename <name>", "description": "重命名当前会话。"},
    {"name": "/new", "usage": "/new", "description": "清空当前会话并重新开始。"},
    {"name": "/fork", "usage": "/fork", "description": "将当前会话分叉为新的独立会话。"},
    {"name": "/permissions", "usage": "/permissions", "description": "查看权限配置存储路径。"},
    {"name": "/exit", "usage": "/exit", "description": "退出 TinyCoder。"},
    {"name": "/ls", "usage": "/ls [path]", "description": "列出目录文件。"},
    {"name": "/grep", "usage": "/grep <pattern>::[path]", "description": "在文件中搜索文本。"},
    {"name": "/read", "usage": "/read <path>", "description": "直接读取文件。"},
    {"name": "/md", "usage": "/md <path>", "description": "读取并以终端友好格式渲染 Markdown 文件。"},
    {"name": "/write", "usage": "/write <path>::<content>", "description": "直接写入文件。"},
    {"name": "/modify", "usage": "/modify <path>::<content>", "description": "替换文件内容，并在应用前展示可审查 diff。"},
    {"name": "/edit", "usage": "/edit <path>::<search>::<replace>", "description": "通过精确匹配替换编辑文件。"},
    {"name": "/patch", "usage": "/patch <path>::<search1>::<replace1>::<search2>::<replace2>...", "description": "对同一文件批量应用多组替换。"},
    {"name": "/cmd", "usage": "/cmd [cwd::]<command> [args...]", "description": "执行允许的开发命令，可指定工作目录。"},
    {"name": "/compact", "usage": "/compact", "description": "压缩对话上下文，释放上下文窗口。"},
    {"name": "/collapse", "usage": "/collapse", "description": "将旧的安全上下文片段折叠为摘要，保留完整转录记录。"},
    {"name": "/snip", "usage": "/snip", "description": "不调用模型，移除可安全裁剪的中间上下文片段。"},
]


def format_slash_commands() -> str:
    width = max(len(command["usage"]) for command in SLASH_COMMANDS)
    lines = ["可用命令："]
    lines.extend(f"  {command['usage'].ljust(width)}  {command['description']}" for command in SLASH_COMMANDS)
    return "\n".join(lines)


def find_matching_slash_commands(input_text: str) -> list[str]:
    return [command["usage"] for command in SLASH_COMMANDS if command["usage"].startswith(input_text)]


def mask_secret(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def normalize_provider(provider: str | None) -> str:
    key = (provider or "anthropic").strip().lower()
    normalized = PROVIDER_ALIASES.get(key)
    if not normalized:
        raise RuntimeError(f"不支持的模型供应商: {provider}. 可选: {', '.join(SUPPORTED_MODEL_PROVIDERS)}")
    return normalized


async def _effective_env() -> dict[str, str]:
    effective = await load_effective_settings()
    configured_env = {str(k): str(v) for k, v in (effective.get("env") or {}).items()}
    return {**configured_env, **os.environ}


async def _current_provider() -> str:
    env = await _effective_env()
    return normalize_provider(env.get("TINYCODER_MODEL_PROVIDER") or env.get("MODEL_PROVIDER") or "anthropic")


def _provider_info(provider: str) -> dict[str, str]:
    return SUPPORTED_MODEL_PROVIDERS[normalize_provider(provider)]


def _set_process_model_env(provider: str, *, model: str | None = None, api_key: str | None = None, base_url: str | None = None) -> dict[str, str]:
    provider = normalize_provider(provider)
    info = _provider_info(provider)
    updates: dict[str, str] = {"TINYCODER_MODEL_PROVIDER": provider}
    os.environ["TINYCODER_MODEL_PROVIDER"] = provider

    if model:
        updates["TINYCODER_MODEL"] = model
        updates[info["model_env"]] = model
        os.environ["TINYCODER_MODEL"] = model
        os.environ[info["model_env"]] = model

    if api_key:
        updates[info["api_key_env"]] = api_key
        os.environ[info["api_key_env"]] = api_key

    if base_url:
        cleaned = base_url.rstrip("/")
        updates[info["base_url_env"]] = cleaned
        os.environ[info["base_url_env"]] = cleaned

    return updates


async def _persist_model_env(provider: str, *, model: str | None = None, api_key: str | None = None, base_url: str | None = None) -> None:
    env_updates = _set_process_model_env(provider, model=model, api_key=api_key, base_url=base_url)
    settings: dict[str, Any] = {"env": env_updates}
    if model:
        settings["model"] = model
    await save_tinycoder_settings(settings)


async def _default_model_for(provider: str) -> str:
    provider = normalize_provider(provider)
    env = await _effective_env()
    info = _provider_info(provider)
    return (env.get(info["model_env"]) or info["default_model"]).strip()


async def _default_base_url_for(provider: str) -> str:
    provider = normalize_provider(provider)
    env = await _effective_env()
    info = _provider_info(provider)
    return (env.get(info["base_url_env"]) or info["default_base_url"]).strip().rstrip("/")


async def _format_status() -> str:
    provider = await _current_provider()
    info = _provider_info(provider)
    env = await _effective_env()
    try:
        runtime = await load_runtime_config()
        provider = normalize_provider(str(runtime.get("provider") or provider))
        info = _provider_info(provider)
        model = str(runtime.get("model") or "")
        base_url = str(runtime.get("baseUrl") or "")
        api_key = str(runtime.get("apiKey") or "")
        auth_token = str(runtime.get("authToken") or "")
        auth_line = f"{info['auth_token_env']}: {mask_secret(auth_token)}" if auth_token else f"{info['api_key_env']}: {mask_secret(api_key)}"
        source = str(runtime.get("sourceSummary") or "")
        mcp_count = len(runtime.get("mcpServers") or {})
        return "\n".join([
            f"provider: {provider} ({info['label']})",
            f"model: {model}",
            f"baseUrl: {base_url}",
            f"auth: {auth_line}",
            f"mcp servers: {mcp_count}",
            source,
        ])
    except Exception as error:
        model = env.get("TINYCODER_MODEL") or env.get(info["model_env"]) or "未配置"
        base_url = env.get(info["base_url_env"]) or info["default_base_url"]
        api_key = env.get(info["api_key_env"]) or ""
        return "\n".join([
            f"provider: {provider} ({info['label']})",
            f"model: {model}",
            f"baseUrl: {base_url}",
            f"auth: {info['api_key_env']}: {mask_secret(api_key)}",
            f"status unavailable: {error}",
        ])


async def try_handle_local_command(input_text: str, context: dict[str, Any] | None = None) -> str | None:
    context = context or {}
    if input_text in {"/", "/help"}:
        return format_slash_commands()
    if input_text == "/config-paths":
        return "\n".join([f"tinycoder settings: {TINYCODER_SETTINGS_PATH}", f"tinycoder permissions: {TINYCODER_PERMISSIONS_PATH}", f"tinycoder mcp: {TINYCODER_MCP_PATH}", f"compat fallback: {CLAUDE_SETTINGS_PATH}"])
    if input_text == "/permissions":
        return f"permission store: {TINYCODER_PERMISSIONS_PATH}"
    if input_text == "/providers":
        return "\n".join(f"{key}  {value['label']}  default={value['default_model']}" for key, value in SUPPORTED_MODEL_PROVIDERS.items())
    if input_text == "/provider":
        provider = await _current_provider()
        return f"current provider: {provider} ({_provider_info(provider)['label']})"
    if input_text.startswith("/provider "):
        raw_provider = input_text[len("/provider "):].strip()
        provider = normalize_provider(raw_provider)
        model = await _default_model_for(provider)
        base_url = await _default_base_url_for(provider)
        await _persist_model_env(provider, model=model, base_url=base_url)
        return f"switched provider={provider}, model={model}; saved to {TINYCODER_SETTINGS_PATH}"
    if input_text == "/base-url":
        provider = await _current_provider()
        try:
            runtime = await load_runtime_config()
            return f"current baseUrl: {runtime.get('baseUrl')}"
        except Exception:
            return f"current baseUrl: {await _default_base_url_for(provider)}"
    if input_text.startswith("/base-url "):
        base_url = input_text[len("/base-url "):].strip().rstrip("/")
        if not base_url:
            return "用法: /base-url <url>"
        provider = await _current_provider()
        await _persist_model_env(provider, base_url=base_url)
        return f"saved {provider} baseUrl={base_url} to {TINYCODER_SETTINGS_PATH}"
    if input_text == "/apikey":
        provider = await _current_provider()
        info = _provider_info(provider)
        env = await _effective_env()
        try:
            runtime = await load_runtime_config()
            key = str(runtime.get("apiKey") or "")
        except Exception:
            key = env.get(info["api_key_env"], "")
        return f"current {info['api_key_env']}: {mask_secret(key)}"
    if input_text.startswith("/apikey "):
        api_key = input_text[len("/apikey "):].strip()
        if not api_key:
            return "用法: /apikey <api-key>"
        provider = await _current_provider()
        await _persist_model_env(provider, api_key=api_key)
        return f"saved {provider} API key={mask_secret(api_key)} to {TINYCODER_SETTINGS_PATH}"
    if input_text.startswith("/use "):
        parts = input_text.split()
        if len(parts) < 3:
            return "用法: /use <provider> <model> [api-key] [base-url]"
        provider = normalize_provider(parts[1])
        model = parts[2].strip()
        api_key = parts[3].strip() if len(parts) >= 4 else None
        base_url = parts[4].strip().rstrip("/") if len(parts) >= 5 else await _default_base_url_for(provider)
        await _persist_model_env(provider, model=model, api_key=api_key, base_url=base_url)
        key_text = f", apiKey={mask_secret(api_key)}" if api_key else ""
        return f"switched provider={provider}, model={model}, baseUrl={base_url}{key_text}; saved to {TINYCODER_SETTINGS_PATH}"
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
        shortcuts = ["/ls [path]", "/grep <pattern>::[path]", "/read <path>", "/md <path>", "/write <path>::<content>", "/modify <path>::<content>", "/edit <path>::<search>::<replace>", "/patch <path>::<search>::<replace>...", "/cmd [cwd::]<command> [args...]"]
        return "Tools:\n" + "\n".join(tool_lines) + "\n\nShortcuts:\n" + "\n".join(shortcuts)
    if input_text == "/status":
        return await _format_status()
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
        provider = await _current_provider()
        await _persist_model_env(provider, model=model)
        return f"saved provider={provider}, model={model} to {TINYCODER_SETTINGS_PATH}"
    return None


def complete_slash_command(line: str) -> tuple[list[str], str]:
    hits = [command["usage"] for command in SLASH_COMMANDS if command["usage"].startswith(line)]
    return hits or [command["usage"] for command in SLASH_COMMANDS], line


formatSlashCommands = format_slash_commands
findMatchingSlashCommands = find_matching_slash_commands
tryHandleLocalCommand = try_handle_local_command
completeSlashCommand = complete_slash_command
