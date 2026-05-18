from __future__ import annotations

UNKNOWN_MODEL_MAX_OUTPUT_TOKENS = {"default": 32_000, "upperLimit": 64_000}
MODEL_MAX_OUTPUT_TOKEN_RULES = [
    {"patterns": ["claude-opus-4-6", "claude opus 4.6", "opus-4-6"], "limits": {"default": 128_000, "upperLimit": 128_000}},
    {"patterns": ["claude-sonnet-4-6", "claude sonnet 4.6", "sonnet-4-6"], "limits": {"default": 64_000, "upperLimit": 64_000}},
    {"patterns": ["claude-haiku-4-5", "claude haiku 4.5", "haiku-4-5"], "limits": {"default": 64_000, "upperLimit": 64_000}},
    {"patterns": ["claude-opus-4-1", "claude opus 4.1", "opus-4-1", "claude-opus-4", "claude opus 4", "opus-4"], "limits": {"default": 32_000, "upperLimit": 32_000}},
    {"patterns": ["claude-sonnet-4", "claude sonnet 4", "sonnet-4"], "limits": {"default": 64_000, "upperLimit": 64_000}},
    {"patterns": ["claude-3-7-sonnet", "claude 3.7 sonnet", "3-7-sonnet"], "limits": {"default": 8192, "upperLimit": 8192}},
    {"patterns": ["claude-3-5-sonnet", "claude 3.5 sonnet", "3-5-sonnet", "claude-3-sonnet"], "limits": {"default": 8192, "upperLimit": 8192}},
    {"patterns": ["claude-3-5-haiku", "claude 3.5 haiku", "3-5-haiku"], "limits": {"default": 8192, "upperLimit": 8192}},
    {"patterns": ["claude-3-opus", "claude 3 opus"], "limits": {"default": 4096, "upperLimit": 4096}},
    {"patterns": ["claude-3-haiku", "claude 3 haiku"], "limits": {"default": 4096, "upperLimit": 4096}},
    {"patterns": ["gpt-5-codex", "gpt-5.4", "gpt-5.2", "gpt-5.1", "gpt-5"], "limits": {"default": 128_000, "upperLimit": 128_000}},
    {"patterns": ["o4-mini", "o3", "o1-pro", "o1"], "limits": {"default": 100_000, "upperLimit": 100_000}},
    {"patterns": ["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4.1"], "limits": {"default": 32768, "upperLimit": 32768}},
    {"patterns": ["gpt-4o-mini", "gpt-4o"], "limits": {"default": 16384, "upperLimit": 16384}},
    {"patterns": ["gpt-4"], "limits": {"default": 8192, "upperLimit": 8192}},
    {"patterns": ["gemini-2.5-pro", "gemini 2.5 pro", "gemini-2.5-flash-lite", "gemini 2.5 flash-lite", "gemini-2.5-flash", "gemini 2.5 flash"], "limits": {"default": 65536, "upperLimit": 65536}},
    {"patterns": ["deepseek-reasoner"], "limits": {"default": 32_000, "upperLimit": 64_000}},
    {"patterns": ["deepseek-chat"], "limits": {"default": 4000, "upperLimit": 8000}},
]
COMPACTABLE_TOOLS = {"read_file", "run_command", "search_files", "list_files", "web_fetch"}


def get_model_max_output_tokens(model: str) -> dict[str, int]:
    normalized = (model or "").strip().lower()
    for rule in MODEL_MAX_OUTPUT_TOKEN_RULES:
        if any(pattern in normalized for pattern in rule["patterns"]):
            return dict(rule["limits"])
    return dict(UNKNOWN_MODEL_MAX_OUTPUT_TOKENS)


def resolve_max_output_tokens(model: str, configured_max_output_tokens: int | None = None) -> int:
    limits = get_model_max_output_tokens(model)
    if configured_max_output_tokens is not None:
        try:
            value = int(configured_max_output_tokens)
            if value > 0:
                return min(value, limits["upperLimit"])
        except (TypeError, ValueError):
            pass
    return limits["default"]

getModelMaxOutputTokens = get_model_max_output_tokens
resolveMaxOutputTokens = resolve_max_output_tokens
