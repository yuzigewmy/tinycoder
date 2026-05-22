from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Literal, TypedDict


class McpServerConfig(TypedDict, total=False):
    command: str
    args: list[str]
    env: dict[str, str | int]
    url: str
    headers: dict[str, str | int]
    cwd: str
    enabled: bool
    protocol: Literal["auto", "content-length", "newline-json", "streamable-http"]


class TinyCoderSettings(TypedDict, total=False):
    env: dict[str, str | int]
    model: str
    maxOutputTokens: int
    mcpServers: dict[str, McpServerConfig]
    customProviders: dict[str, dict[str, str | int]]


class RuntimeConfig(TypedDict, total=False):
    provider: str
    providerType: str
    model: str
    baseUrl: str
    authToken: str
    apiKey: str
    maxOutputTokens: int
    mcpServers: dict[str, McpServerConfig]
    sourceSummary: str


McpConfigScope = Literal["user", "project"]

TINYCODER_DIR = Path(os.environ.get("TINYCODER_HOME", Path.home() / ".tinycoder")).expanduser().resolve()
TINYCODER_SETTINGS_PATH = TINYCODER_DIR / "settings.json"
TINYCODER_HISTORY_PATH = TINYCODER_DIR / "history.jsonl"
TINYCODER_PERMISSIONS_PATH = TINYCODER_DIR / "permissions.json"
TINYCODER_MCP_PATH = TINYCODER_DIR / "mcp.json"
TINYCODER_MCP_TOKENS_PATH = TINYCODER_DIR / "mcp-tokens.json"
TINYCODER_PROJECTS_DIR = TINYCODER_DIR / "projects"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
PROJECT_MCP_PATH = Path.cwd() / ".mcp.json"


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


async def read_mcp_tokens_file(file_path: str | Path = TINYCODER_MCP_TOKENS_PATH) -> dict[str, str]:
    parsed = _read_json_file(Path(file_path), {})
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


async def save_mcp_tokens_file(tokens: dict[str, str], file_path: str | Path = TINYCODER_MCP_TOKENS_PATH) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def read_settings_file(file_path: str | Path) -> TinyCoderSettings:
    parsed = _read_json_file(Path(file_path), {})
    return parsed if isinstance(parsed, dict) else {}


async def read_mcp_config_file(file_path: str | Path) -> dict[str, McpServerConfig]:
    parsed = _read_json_file(Path(file_path), {})
    if not isinstance(parsed, dict):
        return {}
    servers = parsed.get("mcpServers")
    if not isinstance(servers, dict):
        return {}
    return servers


def get_mcp_config_path(scope: McpConfigScope, cwd: str | Path = Path.cwd()) -> Path:
    return Path(cwd) / ".mcp.json" if scope == "project" else TINYCODER_MCP_PATH


async def load_scoped_mcp_servers(scope: McpConfigScope, cwd: str | Path = Path.cwd()) -> dict[str, McpServerConfig]:
    return await read_mcp_config_file(get_mcp_config_path(scope, cwd))


async def save_scoped_mcp_servers(scope: McpConfigScope, servers: dict[str, McpServerConfig], cwd: str | Path = Path.cwd()) -> None:
    target = get_mcp_config_path(scope, cwd)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_settings(base: TinyCoderSettings, override: TinyCoderSettings) -> TinyCoderSettings:
    merged_servers: dict[str, Any] = dict(base.get("mcpServers") or {})
    for name, server in (override.get("mcpServers") or {}).items():
        prev = merged_servers.get(name, {}) if isinstance(merged_servers.get(name), dict) else {}
        merged_servers[name] = {
            **prev,
            **server,
            "env": {**(prev.get("env") or {}), **(server.get("env") or {})},
            "headers": {**(prev.get("headers") or {}), **(server.get("headers") or {})},
        }
    result: TinyCoderSettings = {
        **base,
        **override,
        "env": {**(base.get("env") or {}), **(override.get("env") or {})},
        "mcpServers": merged_servers,
        "customProviders": {**(base.get("customProviders") or {}), **(override.get("customProviders") or {})},
    }
    return result


async def load_effective_settings() -> TinyCoderSettings:
    claude = await read_settings_file(CLAUDE_SETTINGS_PATH)
    global_mcp = await read_mcp_config_file(TINYCODER_MCP_PATH)
    project_mcp = await read_mcp_config_file(PROJECT_MCP_PATH)
    mini = await read_settings_file(TINYCODER_SETTINGS_PATH)
    return merge_settings(
        merge_settings(
            merge_settings(claude, {"mcpServers": global_mcp}),
            {"mcpServers": project_mcp},
        ),
        mini,
    )


async def save_tinycoder_settings(updates: TinyCoderSettings) -> None:
    TINYCODER_DIR.mkdir(parents=True, exist_ok=True)
    existing = await read_settings_file(TINYCODER_SETTINGS_PATH)
    nxt = merge_settings(existing, updates)
    TINYCODER_SETTINGS_PATH.write_text(json.dumps(nxt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def load_runtime_config() -> RuntimeConfig:
    effective = await load_effective_settings()
    env = {**{str(k): str(v) for k, v in (effective.get("env") or {}).items()}, **os.environ}
    provider = (
        env.get("TINYCODER_MODEL_PROVIDER")
        or env.get("MODEL_PROVIDER")
        or "anthropic"
    ).strip().lower()

    custom_providers = effective.get("customProviders") or {}
    custom_provider = custom_providers.get(provider) if isinstance(custom_providers, dict) else None

    if provider in {"qwen", "dashscope", "aliyun"}:
        provider_type = "openai"
        model = (
            os.environ.get("TINYCODER_MODEL")
            or effective.get("model")
            or env.get("DASHSCOPE_MODEL")
            or env.get("QWEN_MODEL")
            or "qwen-plus"
        ).strip()
        base_url = (
            env.get("DASHSCOPE_BASE_URL")
            or env.get("QWEN_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).strip().rstrip("/")
        auth_token = (env.get("DASHSCOPE_AUTH_TOKEN") or env.get("QWEN_AUTH_TOKEN") or "").strip()
        api_key = (env.get("DASHSCOPE_API_KEY") or env.get("QWEN_API_KEY") or "").strip()
    elif provider == "anthropic":
        provider_type = "anthropic"
        model = (os.environ.get("TINYCODER_MODEL") or effective.get("model") or env.get("ANTHROPIC_MODEL") or "").strip()
        base_url = (env.get("ANTHROPIC_BASE_URL") or "").strip() or "https://api.anthropic.com"
        auth_token = (env.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
        api_key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    elif isinstance(custom_provider, dict):
        provider_type = str(custom_provider.get("type") or "openai").strip().lower()
        if provider_type not in {"openai", "openai-compatible"}:
            raise RuntimeError(f"Unsupported custom provider type: {provider_type}. Use openai.")
        provider_type = "openai"
        upper = provider.upper().replace("-", "_")
        model = (
            os.environ.get("TINYCODER_MODEL")
            or env.get(f"TINYCODER_{upper}_MODEL")
            or str(custom_provider.get("model") or "")
        ).strip()
        base_url = (
            env.get(f"TINYCODER_{upper}_BASE_URL")
            or str(custom_provider.get("baseUrl") or "")
        ).strip().rstrip("/")
        auth_token = (env.get(f"TINYCODER_{upper}_AUTH_TOKEN") or str(custom_provider.get("authToken") or "")).strip()
        api_key = (env.get(f"TINYCODER_{upper}_API_KEY") or str(custom_provider.get("apiKey") or "")).strip()
    else:
        raise RuntimeError(f"Unsupported model provider: {provider}. Use anthropic, qwen, or add a custom OpenAI-compatible provider with /provider add.")

    raw_max = os.environ.get("TINYCODER_MAX_OUTPUT_TOKENS", effective.get("maxOutputTokens") or env.get("TINYCODER_MAX_OUTPUT_TOKENS"))
    max_out: int | None = None
    try:
        if raw_max is not None:
            val = int(float(raw_max))
            if val > 0:
                max_out = val
    except (TypeError, ValueError):
        max_out = None
    if not model:
        raise RuntimeError("No model configured. Set ~/.tinycoder/settings.json or a provider-specific model env variable.")
    if not auth_token and not api_key:
        raise RuntimeError("No auth configured. Set an API key/auth token in ~/.tinycoder/settings.json or process env.")
    result: RuntimeConfig = {
        "provider": provider,
        "providerType": provider_type,
        "model": model,
        "baseUrl": base_url,
        "mcpServers": effective.get("mcpServers") or {},
        "sourceSummary": f"config: {TINYCODER_SETTINGS_PATH} > {CLAUDE_SETTINGS_PATH} > process.env",
    }
    if auth_token:
        result["authToken"] = auth_token
    if api_key:
        result["apiKey"] = api_key
    if max_out is not None:
        result["maxOutputTokens"] = max_out
    return result
