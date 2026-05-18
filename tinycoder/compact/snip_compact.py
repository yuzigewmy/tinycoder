from __future__ import annotations

import time
from typing import Any

from ..utils.token_estimator import estimate_messages_tokens, mark_provider_usage_stale, token_count_with_estimation
from .constants import SNIP_COMPACT_THRESHOLD, SNIP_KEEP_RECENT_MESSAGES, SNIP_MIN_MESSAGES_TO_REMOVE, SNIP_MIN_TOKENS_TO_FREE, SNIP_TARGET_USAGE

PROTECTED_TOOL_NAMES = {"edit_file", "modify_file", "patch_file", "write_file", "apply_patch"}
ERROR_MARKERS = ["error", "failed", "failure", "exception", "traceback", "permission denied"]


def no_snip_result(messages: list[dict[str, Any]], tokens_before: int, reason: str) -> dict[str, Any]:
    return {"messages": messages, "didSnip": False, "tokensBefore": tokens_before, "tokensAfter": tokens_before, "tokensFreed": 0, "removedMessageIds": [], "reason": reason}


def message_id(message: dict[str, Any], index: int) -> str:
    return message.get("id") or f"message-{index}"


def is_boundary_message(message: dict[str, Any]) -> bool:
    return message.get("role") in {"system", "context_summary", "snip_boundary"}


def is_protected_tool_name(tool_name: str) -> bool:
    normalized = (tool_name or "").strip().lower()
    return normalized in PROTECTED_TOOL_NAMES or any(x in normalized for x in ["patch", "write", "edit", "modify"])


def tool_result_looks_important_error(message: dict[str, Any]) -> bool:
    if message.get("isError"):
        return True
    content = str(message.get("content", "")).lower()
    return any(marker in content for marker in ERROR_MARKERS)


def message_text_looks_important_error(message: dict[str, Any]) -> bool:
    if message.get("role") not in {"user", "assistant", "assistant_progress", "context_summary", "snip_boundary"}:
        return False
    content = str(message.get("content", "")).lower()
    return any(marker in content for marker in ERROR_MARKERS)


def group_has_protected_tool(group: dict[str, Any]) -> bool:
    for message in group["messages"]:
        if message.get("role") in {"assistant_tool_call", "tool_result"} and is_protected_tool_name(message.get("toolName", "")):
            return True
    return False


def group_has_important_error(group: dict[str, Any]) -> bool:
    return any(message_text_looks_important_error(m) or (m.get("role") == "tool_result" and tool_result_looks_important_error(m)) for m in group["messages"])


def build_message_groups(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        if message.get("role") == "assistant_tool_call":
            nxt = messages[i + 1] if i + 1 < len(messages) else None
            grouped = [message, nxt] if nxt and nxt.get("role") == "tool_result" and nxt.get("toolUseId") == message.get("toolUseId") else [message]
            groups.append({"start": i, "end": i + len(grouped), "messages": grouped, "tokens": estimate_messages_tokens(grouped), "protected": len(grouped) == 1, "reasons": ["unclosed_tool_call"] if len(grouped) == 1 else []})
            i += len(grouped); continue
        if message.get("role") == "tool_result":
            groups.append({"start": i, "end": i + 1, "messages": [message], "tokens": estimate_messages_tokens([message]), "protected": True, "reasons": ["orphan_tool_result"]})
            i += 1; continue
        groups.append({"start": i, "end": i + 1, "messages": [message], "tokens": estimate_messages_tokens([message]), "protected": False, "reasons": []})
        i += 1
    return groups


def add_protected_reason(group: dict[str, Any], reason: str) -> None:
    group["protected"] = True
    if reason not in group["reasons"]:
        group["reasons"].append(reason)


def protect_nearby_groups(groups: list[dict[str, Any]], index: int, reason: str) -> None:
    for i in range(max(0, index - 1), min(len(groups) - 1, index + 1) + 1):
        add_protected_reason(groups[i], reason)


def mark_protected_groups(groups: list[dict[str, Any]], candidate_start: int, candidate_end: int) -> None:
    for group in groups:
        if group["start"] < candidate_start or group["end"] > candidate_end:
            add_protected_reason(group, "outside_candidate_range"); continue
        if any(is_boundary_message(m) for m in group["messages"]):
            add_protected_reason(group, "boundary_message")
    for i, group in enumerate(groups):
        if group_has_protected_tool(group):
            protect_nearby_groups(groups, i, "near_file_edit")
        if group_has_important_error(group):
            protect_nearby_groups(groups, i, "near_important_error")


def find_candidate_range(messages: list[dict[str, Any]]) -> dict[str, Any]:
    if len(messages) <= SNIP_KEEP_RECENT_MESSAGES + SNIP_MIN_MESSAGES_TO_REMOVE:
        return {"start": 0, "end": 0, "reason": "too_few_messages"}
    keep_recent_start = max(0, len(messages) - SNIP_KEEP_RECENT_MESSAGES)
    last_user = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user = i; break
    end = min(keep_recent_start, last_user if last_user >= 0 else len(messages))
    if end <= 0:
        return {"start": 0, "end": 0, "reason": "no_middle_range"}
    start = 0
    for i in range(end):
        if is_boundary_message(messages[i]):
            start = i + 1
    if end - start < SNIP_MIN_MESSAGES_TO_REMOVE:
        return {"start": start, "end": end, "reason": "candidate_range_too_small"}
    return {"start": start, "end": end}


def find_safe_runs(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    def flush() -> None:
        nonlocal current
        if not current: return
        first, last = current[0], current[-1]
        runs.append({"groups": current, "start": first["start"], "end": last["end"], "messagesCount": last["end"] - first["start"], "tokens": sum(g["tokens"] for g in current)})
        current = []
    for group in groups:
        if group.get("protected"):
            flush(); continue
        current.append(group)
    flush(); return runs


def select_deletion_from_run(run: dict[str, Any], desired_tokens_to_free: int) -> dict[str, int]:
    end_group_index = -1; tokens = 0; count = 0
    for i, group in enumerate(run["groups"]):
        tokens += group["tokens"]; count = group["end"] - run["start"]; end_group_index = i
        if tokens >= desired_tokens_to_free and count >= SNIP_MIN_MESSAGES_TO_REMOVE:
            break
    end_group = run["groups"][max(0, end_group_index)]
    return {"start": run["start"], "end": end_group["end"], "tokens": tokens, "messagesCount": count}


def build_snip_boundary_content(args: dict[str, Any]) -> str:
    return "\n".join([
        "[Snipped earlier conversation segment]", "",
        "A middle portion of the earlier conversation was removed to preserve context space.", "",
        "Removed range:",
        f"- messages: {args['removedCount']}",
        f"- approximate tokens freed: {max(0, round(args['tokensFreed']))}", "",
        "The recent conversation and active task context are preserved.",
    ])


def build_anthropic_snip_boundary_text() -> str:
    return "\n".join(["[Snipped earlier conversation segment]", "", "A middle portion of the earlier conversation was removed to preserve context space.", "The recent conversation and active task context are preserved."])


def build_boundary_message(args: dict[str, Any]) -> dict[str, Any]:
    timestamp = int(time.time() * 1000)
    first_removed = args["removedMessageIds"][0] if args.get("removedMessageIds") else "none"
    return {"id": f"snip-{timestamp}-{first_removed}", "role": "snip_boundary", "content": build_snip_boundary_content({"removedCount": args["removedCount"], "tokensFreed": args["tokensFreed"]}), "removedMessageIds": args["removedMessageIds"], "removedCount": args["removedCount"], "tokensFreed": args["tokensFreed"], "timestamp": timestamp}


def mark_retained_usage_stale(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [mark_provider_usage_stale(m, "conversation was snip-compacted after this provider usage was recorded") for m in messages]


async def snip_compact_conversation(params: dict[str, Any]) -> dict[str, Any]:
    messages = params["messages"]
    stats = params["contextStats"]
    trigger_tokens = stats["totalTokens"]
    tokens_before = estimate_messages_tokens(messages)
    effective = stats["effectiveInput"] if stats.get("effectiveInput", 0) > 0 else params.get("modelContextWindow", 1)
    utilization = trigger_tokens / effective if effective > 0 else stats.get("utilization", 0)
    if utilization < SNIP_COMPACT_THRESHOLD:
        return no_snip_result(messages, tokens_before, "below_threshold")
    rng = find_candidate_range(messages)
    if rng.get("reason"):
        return no_snip_result(messages, tokens_before, rng["reason"])
    groups = build_message_groups(messages)
    mark_protected_groups(groups, rng["start"], rng["end"])
    safe_runs = [r for r in find_safe_runs(groups) if r["messagesCount"] >= SNIP_MIN_MESSAGES_TO_REMOVE and r["tokens"] >= SNIP_MIN_TOKENS_TO_FREE]
    safe_runs.sort(key=lambda r: (-r["tokens"], -r["messagesCount"], r["start"]))
    if not safe_runs:
        return no_snip_result(messages, tokens_before, "no_safe_interval")
    target_tokens = int(effective * SNIP_TARGET_USAGE)
    desired = max(SNIP_MIN_TOKENS_TO_FREE, trigger_tokens - target_tokens)
    deletion = select_deletion_from_run(safe_runs[0], desired)
    if deletion["messagesCount"] < SNIP_MIN_MESSAGES_TO_REMOVE:
        return no_snip_result(messages, tokens_before, "below_min_messages")
    removed = messages[deletion["start"]:deletion["end"]]
    removed_ids = [message_id(m, deletion["start"] + offset) for offset, m in enumerate(removed)]
    boundary = build_boundary_message({"removedMessageIds": removed_ids, "removedCount": len(removed), "tokensFreed": deletion["tokens"]})
    boundary_tokens = estimate_messages_tokens([boundary])
    estimated_freed = max(0, deletion["tokens"] - boundary_tokens)
    if estimated_freed < SNIP_MIN_TOKENS_TO_FREE:
        return no_snip_result(messages, tokens_before, "below_min_tokens")
    boundary = {**boundary, "content": build_snip_boundary_content({"removedCount": len(removed), "tokensFreed": estimated_freed}), "tokensFreed": estimated_freed}
    after = mark_retained_usage_stale([*messages[:deletion["start"]], boundary, *messages[deletion["end"]:]])
    tokens_after = token_count_with_estimation(after)["totalTokens"]
    tokens_freed = max(0, tokens_before - tokens_after)
    if tokens_after >= tokens_before:
        return no_snip_result(messages, tokens_before, "no_token_reduction")
    return {"messages": after, "didSnip": True, "tokensBefore": tokens_before, "tokensAfter": tokens_after, "tokensFreed": tokens_freed, "removedMessageIds": removed_ids, "boundaryMessage": after[deletion["start"]], "reason": "snipped_safe_middle_interval"}

buildSnipBoundaryContent = build_snip_boundary_content
buildAnthropicSnipBoundaryText = build_anthropic_snip_boundary_text
snipCompactConversation = snip_compact_conversation
