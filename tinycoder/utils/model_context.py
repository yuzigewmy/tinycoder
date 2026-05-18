from __future__ import annotations

from typing import TypedDict


class ModelContextWindow(TypedDict):
    contextWindow: int
    outputReserve: int
    effectiveInput: int


UNKNOWN_MODEL_CONTEXT = {"patterns": [], "contextWindow": 128_000, "outputReserve": 8_000}
MODEL_CONTEXT_RULES = [
    {"patterns": ["claude-opus-4-6", "claude opus 4.6", "opus-4-6"], "contextWindow": 200_000, "outputReserve": 16_000},
    {"patterns": ["claude-sonnet-4-6", "claude sonnet 4.6", "sonnet-4-6"], "contextWindow": 200_000, "outputReserve": 16_000},
    {"patterns": ["claude-haiku-4-5", "claude haiku 4.5", "haiku-4-5"], "contextWindow": 200_000, "outputReserve": 16_000},
    {"patterns": ["claude-opus-4-1", "claude opus 4.1", "opus-4-1", "claude-opus-4", "claude opus 4", "opus-4"], "contextWindow": 200_000, "outputReserve": 16_000},
    {"patterns": ["claude-sonnet-4", "claude sonnet 4", "sonnet-4"], "contextWindow": 200_000, "outputReserve": 16_000},
    {"patterns": ["claude-3-7-sonnet", "claude 3.7 sonnet", "3-7-sonnet"], "contextWindow": 200_000, "outputReserve": 8192},
    {"patterns": ["claude-3-5-sonnet", "claude 3.5 sonnet", "3-5-sonnet", "claude-3-sonnet"], "contextWindow": 200_000, "outputReserve": 8192},
    {"patterns": ["claude-3-5-haiku", "claude 3.5 haiku", "3-5-haiku"], "contextWindow": 200_000, "outputReserve": 8192},
    {"patterns": ["claude-3-opus", "claude 3 opus"], "contextWindow": 200_000, "outputReserve": 4096},
    {"patterns": ["claude-3-haiku", "claude 3 haiku"], "contextWindow": 200_000, "outputReserve": 4096},
    {"patterns": ["gpt-5-codex", "gpt-5.4", "gpt-5.2", "gpt-5.1", "gpt-5"], "contextWindow": 128_000, "outputReserve": 16_000},
    {"patterns": ["o4-mini", "o3", "o1-pro", "o1"], "contextWindow": 200_000, "outputReserve": 16_000},
    {"patterns": ["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4.1"], "contextWindow": 1_047_576, "outputReserve": 16_000},
    {"patterns": ["gpt-4o-mini", "gpt-4o"], "contextWindow": 128_000, "outputReserve": 16_384},
    {"patterns": ["gpt-4"], "contextWindow": 128_000, "outputReserve": 8192},
    {"patterns": ["gemini-2.5-pro", "gemini 2.5 pro"], "contextWindow": 1_048_576, "outputReserve": 16_000},
    {"patterns": ["gemini-2.5-flash-lite", "gemini 2.5 flash-lite"], "contextWindow": 1_048_576, "outputReserve": 16_000},
    {"patterns": ["gemini-2.5-flash", "gemini 2.5 flash"], "contextWindow": 1_048_576, "outputReserve": 16_000},
    {"patterns": ["deepseek-reasoner"], "contextWindow": 128_000, "outputReserve": 16_000},
    {"patterns": ["deepseek-chat"], "contextWindow": 128_000, "outputReserve": 4000},
]


def get_model_context_window(model: str) -> ModelContextWindow:
    normalized = (model or "").strip().lower()
    for rule in MODEL_CONTEXT_RULES:
        if any(pattern in normalized for pattern in rule["patterns"]):
            return {"contextWindow": rule["contextWindow"], "outputReserve": rule["outputReserve"], "effectiveInput": rule["contextWindow"] - rule["outputReserve"]}
    return {"contextWindow": UNKNOWN_MODEL_CONTEXT["contextWindow"], "outputReserve": UNKNOWN_MODEL_CONTEXT["outputReserve"], "effectiveInput": UNKNOWN_MODEL_CONTEXT["contextWindow"] - UNKNOWN_MODEL_CONTEXT["outputReserve"]}

getModelContextWindow = get_model_context_window
