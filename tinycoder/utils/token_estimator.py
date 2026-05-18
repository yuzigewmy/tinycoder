from __future__ import annotations

import json
import math
from typing import Any, Dict, List

from .model_context import get_model_context_window

CHARS_PER_TOKEN = {
    "system": 3.5,
    "user": 3.0,
    "assistant_thinking": 3.0,
    "assistant": 3.5,
    "assistant_progress": 3.5,
    "assistant_tool_call": 2.5,
    "tool_result": 2.0,
    "context_summary": 3.5,
    "snip_boundary": 3.5,
}
CLEAR_MARKER = "[Output cleared for context space]"


def message_content_length(message: dict[str, Any]) -> int:
    role = message.get("role")
    if role in {"system", "user", "assistant", "assistant_progress", "tool_result", "context_summary", "snip_boundary"}:
        return len(str(message.get("content") or ""))
    if role == "assistant_thinking":
        try:
            return len(json.dumps(message.get("blocks"), ensure_ascii=False))
        except Exception:
            return 0
    if role == "assistant_tool_call":
        try:
            return len(json.dumps(message.get("input"), ensure_ascii=False))
        except Exception:
            return 0
    return 0


def estimate_message_tokens(message: dict[str, Any]) -> int:
    ratio = CHARS_PER_TOKEN.get(message.get("role"), 3.0)
    return math.ceil(message_content_length(message) / ratio)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def message_provider_usage(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("role") in {"assistant", "assistant_progress", "assistant_tool_call"} and message.get("providerUsage") and not message.get("usageStale"):
        return message.get("providerUsage")
    return None


def stale_usage_reason(messages: list[dict[str, Any]]) -> str | None:
    for message in messages:
        if message.get("role") in {"assistant", "assistant_progress", "assistant_tool_call"} and message.get("providerUsage") and message.get("usageStale"):
            return message.get("usageStaleReason") or "provider usage was marked stale"
    return None


def message_boundary_id(message: dict[str, Any]) -> str | None:
    return message.get("toolUseId") if message.get("role") == "assistant_tool_call" else None


def token_count_with_estimation(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for i in range(len(messages) - 1, -1, -1):
        usage = message_provider_usage(messages[i])
        if not usage:
            continue
        tail = messages[i + 1:]
        estimated = estimate_messages_tokens(tail)
        total = int(usage.get("totalTokens", 0)) + estimated
        return {
            "totalTokens": total,
            "providerUsageTokens": int(usage.get("totalTokens", 0)),
            "estimatedTokens": estimated,
            "source": "provider_usage_plus_estimate" if estimated > 0 else "provider_usage",
            "isExact": estimated == 0,
            "usageBoundary": {"messageIndex": i, "messageId": message_boundary_id(messages[i])},
        }
    reason = stale_usage_reason(messages)
    estimated = estimate_messages_tokens(messages)
    return {
        "totalTokens": estimated,
        "providerUsageTokens": 0,
        "estimatedTokens": estimated,
        "source": "estimate_only",
        "isExact": False,
        "stale": bool(reason),
        "reason": reason or "no provider usage available",
    }


def mark_provider_usage_stale(message: dict[str, Any], reason: str) -> dict[str, Any]:
    if message.get("role") in {"assistant", "assistant_progress", "assistant_tool_call"} and message.get("providerUsage"):
        nxt = dict(message)
        nxt["usageStale"] = True
        nxt["usageStaleReason"] = reason
        return nxt
    return message


def compute_context_stats(messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    window = get_model_context_window(model)
    accounting = token_count_with_estimation(messages)
    effective = max(1, int(window["effectiveInput"]))
    utilization = min(1.0, accounting["totalTokens"] / effective)
    if utilization >= 0.95:
        warning = "blocked"
    elif utilization >= 0.85:
        warning = "critical"
    elif utilization >= 0.50:
        warning = "warning"
    else:
        warning = "normal"
    return {
        "estimatedTokens": accounting["estimatedTokens"],
        "totalTokens": accounting["totalTokens"],
        "providerUsageTokens": accounting["providerUsageTokens"],
        "contextWindow": window["contextWindow"],
        "effectiveInput": window["effectiveInput"],
        "utilization": utilization,
        "warningLevel": warning,
        "accounting": accounting,
    }

estimateMessageTokens = estimate_message_tokens
estimateMessagesTokens = estimate_messages_tokens
tokenCountWithEstimation = token_count_with_estimation
markProviderUsageStale = mark_provider_usage_stale
computeContextStats = compute_context_stats
