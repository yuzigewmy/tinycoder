from __future__ import annotations
from typing import Any
from ..utils.context import COMPACTABLE_TOOLS
from ..utils.token_estimator import compute_context_stats, CLEAR_MARKER
from .constants import THRESHOLDS, RETENTION


def microcompact(messages: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    stats = compute_context_stats(messages, model)
    if stats["utilization"] < THRESHOLDS["MICROCOMPACT_UTILIZATION"]:
        return messages
    tool_indices = [i for i, msg in enumerate(messages) if msg.get("role") == "tool_result" and msg.get("toolName") in COMPACTABLE_TOOLS]
    if len(tool_indices) <= RETENTION["KEEP_RECENT_TOOL_RESULTS"]:
        return messages
    keep_from = len(tool_indices) - RETENTION["KEEP_RECENT_TOOL_RESULTS"]
    clear_indices = set(tool_indices[:keep_from])
    changed = False
    result = []
    for i, msg in enumerate(messages):
        if i in clear_indices and msg.get("role") == "tool_result":
            if msg.get("content") != CLEAR_MARKER:
                nxt = dict(msg); nxt["content"] = CLEAR_MARKER; result.append(nxt); changed = True
            else:
                result.append(msg)
        else:
            result.append(msg)
    return result if changed else messages
