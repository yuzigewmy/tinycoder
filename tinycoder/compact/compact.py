from __future__ import annotations

import json
import time
from typing import Any

from ..utils.token_estimator import estimate_messages_tokens, mark_provider_usage_stale, token_count_with_estimation
from .constants import RETENTION
from .prompt import build_compact_summary_prompt, parse_summary_from_response


def group_messages_by_api_round(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    i = 0
    while i < len(messages):
        group: list[dict[str, Any]] = []
        cursor = i
        if messages[cursor].get("role") == "assistant_thinking":
            group.append(messages[cursor]); cursor += 1
        while cursor < len(messages) and messages[cursor].get("role") == "assistant_tool_call":
            group.append(messages[cursor]); cursor += 1
        while cursor < len(messages) and messages[cursor].get("role") == "tool_result":
            group.append(messages[cursor]); cursor += 1
        if any(m.get("role") in {"assistant_tool_call", "tool_result"} for m in group):
            groups.append(group); i = cursor; continue
        groups.append([messages[i]]); i += 1
    return groups


def align_boundary_to_api_round(messages: list[dict[str, Any]], boundary: int) -> int:
    start = 0
    for group in group_messages_by_api_round(messages):
        end = start + len(group)
        if boundary > start and boundary < end:
            return start
        start = end
    return boundary


def find_retention_boundary(messages: list[dict[str, Any]]) -> int:
    token_sum = 0
    boundary = len(messages)
    for i in range(len(messages) - 1, 0, -1):
        tokens = estimate_messages_tokens([messages[i]])
        if token_sum + tokens > RETENTION["MAX_KEEP_TOKENS"]:
            break
        token_sum += tokens
        boundary = i
    min_boundary = max(1, len(messages) - RETENTION["MIN_KEEP_MESSAGES"])
    boundary = min(boundary, min_boundary)
    if boundary <= 1 and len(messages) > RETENTION["MIN_KEEP_MESSAGES"] + 1:
        boundary = max(1, len(messages) - RETENTION["MIN_KEEP_MESSAGES"])
    return align_boundary_to_api_round(messages, boundary)


def messages_to_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            parts.append(f"[User]: {msg.get('content', '')}")
        elif role in {"assistant", "assistant_progress"}:
            parts.append(f"[Assistant]: {msg.get('content', '')}")
        elif role == "assistant_thinking":
            parts.append("[Assistant Thinking]: preserved provider reasoning block")
        elif role == "assistant_tool_call":
            parts.append(f"[Tool Call: {msg.get('toolName')}]: {json.dumps(msg.get('input'), ensure_ascii=False)}")
        elif role == "tool_result":
            content = str(msg.get("content", ""))
            if len(content) > 500:
                content = content[:500] + "... (truncated)"
            parts.append(f"[Tool Result: {msg.get('toolName')}{' ERROR' if msg.get('isError') else ''}]: {content}")
        elif role == "context_summary":
            parts.append(f"[Previous Summary]: {msg.get('content', '')}")
        elif role == "snip_boundary":
            parts.append(f"[Snipped Context Boundary]: {msg.get('content', '')}")
    return "\n\n".join(parts)


async def compact_conversation(messages: list[dict[str, Any]], model_adapter: Any) -> dict[str, Any] | None:
    if len(messages) <= 2:
        return None
    tokens_before = token_count_with_estimation(messages)["totalTokens"]
    system_messages = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    if len(non_system) <= RETENTION["MIN_KEEP_MESSAGES"]:
        return None
    boundary = find_retention_boundary(messages)
    to_compress = messages[1:boundary]
    to_keep = [mark_provider_usage_stale(m, "conversation was compacted after this provider usage was recorded") for m in messages[boundary:]]
    if not to_compress:
        return None
    summary_prompt = build_compact_summary_prompt(messages_to_text(to_compress))
    req = [
        {"role": "system", "content": "You are a helpful assistant that summarizes conversations concisely."},
        {"role": "user", "content": summary_prompt},
    ]
    try:
        response = await model_adapter.next(req)
        if response.get("type") != "assistant" or not str(response.get("content", "")).strip():
            return None
        summary_content = parse_summary_from_response(str(response.get("content", "")))
        if not summary_content:
            return None
        summary = {"role": "context_summary", "content": summary_content, "compressedCount": len(to_compress), "timestamp": int(time.time() * 1000)}
        new_messages = [*system_messages, summary, *to_keep]
        tokens_after = token_count_with_estimation(new_messages)["totalTokens"]
        return {"messages": new_messages, "summary": summary, "removedCount": len(to_compress), "tokensBefore": tokens_before, "tokensAfter": tokens_after}
    except Exception:
        return None

# aliases
groupMessagesByApiRound = group_messages_by_api_round
findRetentionBoundary = find_retention_boundary
messagesToText = messages_to_text
compactConversation = compact_conversation
