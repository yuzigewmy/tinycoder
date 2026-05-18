from __future__ import annotations

import os
from typing import Any

from ..utils.token_estimator import compute_context_stats
from ..utils.model_context import get_model_context_window
from .compact import compact_conversation
from .constants import THRESHOLDS, LIMITS

_state = {"consecutiveFailures": 0, "disabled": False}


def reset_auto_compact_state() -> None:
    _state["consecutiveFailures"] = 0
    _state["disabled"] = False


def get_auto_compact_state() -> dict[str, Any]:
    return dict(_state)


def should_auto_compact(messages: list[dict[str, Any]], model: str) -> bool:
    stats = compute_context_stats(messages, model)
    return stats["utilization"] >= THRESHOLDS["AUTOCOMPACT_UTILIZATION"]


async def auto_compact(messages: list[dict[str, Any]], model: str, model_adapter: Any) -> dict[str, Any] | None:
    if _state["disabled"]:
        return None
    window = get_model_context_window(model)
    if window["effectiveInput"] < LIMITS["MIN_EFFECTIVE_INPUT_FOR_AUTOCOMPACT"]:
        return None
    if not should_auto_compact(messages, model):
        return None
    try:
        result = await compact_conversation(messages, model_adapter)
        if result:
            _state["consecutiveFailures"] = 0
            return result
        _state["consecutiveFailures"] += 1
    except Exception:
        _state["consecutiveFailures"] += 1
    if _state["consecutiveFailures"] >= LIMITS["MAX_AUTOCOMPACT_FAILURES"]:
        _state["disabled"] = True
    return None

resetAutoCompactState = reset_auto_compact_state
getAutoCompactState = get_auto_compact_state
shouldAutoCompact = should_auto_compact
autoCompact = auto_compact
