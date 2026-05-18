from __future__ import annotations

import json
import time
from typing import Any

from ..utils.token_estimator import compute_context_stats, estimate_messages_tokens, mark_provider_usage_stale
from .constants import (
    CONTEXT_COLLAPSE_KEEP_RECENT_MESSAGES,
    CONTEXT_COLLAPSE_MAX_FAILURES,
    CONTEXT_COLLAPSE_MAX_SPANS_PER_PASS,
    CONTEXT_COLLAPSE_MIN_TOKENS_TO_SAVE,
    CONTEXT_COLLAPSE_TARGET_USAGE,
    CONTEXT_COLLAPSE_UTILIZATION,
)
from .prompt import parse_summary_from_response

CONTEXT_COLLAPSE_STALE_REASON = "conversation was context-collapsed in the model-visible projection after this provider usage was recorded"


def create_context_collapse_state() -> dict[str, Any]:
    return {"spans": [], "enabled": True, "consecutiveFailures": 0}


def normalize_context_collapse_state(state: dict[str, Any]) -> dict[str, Any]:
    return {"spans": list(state.get("spans") or []), "enabled": bool(state.get("enabled", True)), "consecutiveFailures": int(state.get("consecutiveFailures", 0))}


def with_default_options(options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    return {
        "utilizationThreshold": options.get("utilizationThreshold", CONTEXT_COLLAPSE_UTILIZATION),
        "targetUsage": options.get("targetUsage", CONTEXT_COLLAPSE_TARGET_USAGE),
        "keepRecentMessages": options.get("keepRecentMessages", CONTEXT_COLLAPSE_KEEP_RECENT_MESSAGES),
        "minTokensToSave": options.get("minTokensToSave", CONTEXT_COLLAPSE_MIN_TOKENS_TO_SAVE),
        "currentTokens": options.get("currentTokens"),
        "effectiveInput": options.get("effectiveInput"),
        "maxSpansPerPass": options.get("maxSpansPerPass", CONTEXT_COLLAPSE_MAX_SPANS_PER_PASS),
        "maxFailures": options.get("maxFailures", CONTEXT_COLLAPSE_MAX_FAILURES),
        "reason": options.get("reason", "context_pressure"),
    }


def message_id(message: dict[str, Any], index: int) -> str:
    return message.get("id") or f"message-{index}"


def is_collapse_boundary(message: dict[str, Any]) -> bool:
    return message.get("role") in {"system", "context_summary", "snip_boundary"}


def estimate_collapse_summary_tokens(tokens_before: int) -> int:
    import math
    return max(128, math.ceil(tokens_before * 0.15))


def build_collapsed_summary_content(span: dict[str, Any]) -> str:
    return "\n".join([
        "[Collapsed context summary]",
        f"This summary replaces messages {span['startMessageId']} through {span['endMessageId']} in the model-visible context only.",
        "The original transcript is preserved in the session/UI.",
        "",
        span["summary"],
    ])


def build_collapsed_summary_message(span: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"collapse-summary-{span['id']}", "role": "context_summary", "content": build_collapsed_summary_content(span), "compressedCount": len(span.get("messageIds") or []), "timestamp": span["createdAt"]}


def project_span(messages: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any] | None:
    if span.get("status") != "committed" or not span.get("messageIds"):
        return None
    index_by_id = {message_id(m, i): i for i, m in enumerate(messages)}
    indices: list[int] = []
    for mid in span["messageIds"]:
        if mid not in index_by_id:
            return None
        indices.append(index_by_id[mid])
    for i in range(1, len(indices)):
        if indices[i] != indices[i - 1] + 1:
            return None
    start, end = indices[0], indices[-1] + 1
    if message_id(messages[start], start) != span["startMessageId"] or message_id(messages[end - 1], end - 1) != span["endMessageId"]:
        return None
    return {"start": start, "end": end, "message": build_collapsed_summary_message(span)}


def project_collapsed_view(messages: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    if not state.get("enabled", True) or not state.get("spans"):
        return messages
    projections = [p for p in (project_span(messages, span) for span in state.get("spans") or []) if p]
    projections.sort(key=lambda p: p["start"])
    if not projections:
        return messages
    result: list[dict[str, Any]] = []
    occupied: set[int] = set()
    cursor = 0
    for projection in projections:
        if any(i in occupied for i in range(projection["start"], projection["end"])):
            continue
        while cursor < projection["start"]:
            result.append(mark_provider_usage_stale(messages[cursor], CONTEXT_COLLAPSE_STALE_REASON)); cursor += 1
        result.append(projection["message"])
        occupied.update(range(projection["start"], projection["end"]))
        cursor = projection["end"]
    while cursor < len(messages):
        result.append(mark_provider_usage_stale(messages[cursor], CONTEXT_COLLAPSE_STALE_REASON)); cursor += 1
    return result


def tool_group_is_closed(messages: list[dict[str, Any]]) -> bool:
    calls = {m.get("toolUseId") for m in messages if m.get("role") == "assistant_tool_call"}
    results = {m.get("toolUseId") for m in messages if m.get("role") == "tool_result"}
    if not calls and not results: return True
    if not calls or not results: return False
    return calls == results


def build_message_groups(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        if message.get("role") == "assistant_thinking":
            grouped = [message]; cursor = i + 1
            while cursor < len(messages) and messages[cursor].get("role") == "assistant_tool_call": grouped.append(messages[cursor]); cursor += 1
            while cursor < len(messages) and messages[cursor].get("role") == "tool_result": grouped.append(messages[cursor]); cursor += 1
            has_call = any(m.get("role") == "assistant_tool_call" for m in grouped)
            groups.append({"start": i, "end": cursor, "messages": grouped, "tokens": estimate_messages_tokens(grouped), "protected": has_call and not tool_group_is_closed(grouped)})
            i = cursor; continue
        if message.get("role") == "assistant_tool_call":
            grouped = []; cursor = i
            while cursor < len(messages) and messages[cursor].get("role") == "assistant_tool_call": grouped.append(messages[cursor]); cursor += 1
            while cursor < len(messages) and messages[cursor].get("role") == "tool_result": grouped.append(messages[cursor]); cursor += 1
            groups.append({"start": i, "end": cursor, "messages": grouped, "tokens": estimate_messages_tokens(grouped), "protected": not tool_group_is_closed(grouped)})
            i = cursor; continue
        if message.get("role") == "tool_result":
            groups.append({"start": i, "end": i + 1, "messages": [message], "tokens": estimate_messages_tokens([message]), "protected": True}); i += 1; continue
        groups.append({"start": i, "end": i + 1, "messages": [message], "tokens": estimate_messages_tokens([message]), "protected": False}); i += 1
    return groups


def committed_collapsed_message_ids(state: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for span in state.get("spans") or []:
        if span.get("status") not in {"committed", "staged"}: continue
        ids.update(span.get("messageIds") or [])
    return ids


def desired_tokens_to_save(options: dict[str, Any]) -> int:
    if options.get("currentTokens") is not None and options.get("effectiveInput"):
        return max(options["minTokensToSave"], int(options["currentTokens"] - options["effectiveInput"] * options["targetUsage"] + 0.9999))
    return options["minTokensToSave"]


def build_candidate_from_groups(messages: list[dict[str, Any]], groups: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any] | None:
    desired = desired_tokens_to_save(options)
    tokens = 0; end_group_index = -1
    for i, group in enumerate(groups):
        tokens += group["tokens"]
        after = estimate_collapse_summary_tokens(tokens)
        if max(0, tokens - after) >= desired:
            end_group_index = i; break
        end_group_index = i
    if end_group_index < 0: return None
    selected = groups[:end_group_index + 1]
    first, last = selected[0], selected[-1]
    selected_messages = messages[first["start"]:last["end"]]
    ids = [message_id(m, first["start"] + offset) for offset, m in enumerate(selected_messages)]
    after = estimate_collapse_summary_tokens(tokens)
    save = max(0, tokens - after)
    if save < options["minTokensToSave"]: return None
    return {"startIndex": first["start"], "endIndex": last["end"], "startMessageId": ids[0], "endMessageId": ids[-1], "messageIds": ids, "messages": selected_messages, "tokensBefore": tokens, "estimatedTokensAfter": after, "estimatedTokensToSave": save}


def find_collapse_candidate(messages: list[dict[str, Any]], state: dict[str, Any], raw_options: dict[str, Any] | None = None) -> dict[str, Any] | None:
    options = with_default_options(raw_options)
    if not messages: return None
    last_user = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user": last_user = i; break
    keep_recent_start = max(0, len(messages) - options["keepRecentMessages"])
    protected_start = min(keep_recent_start, last_user if last_user >= 0 else len(messages))
    if protected_start <= 0: return None
    collapsed_ids = committed_collapsed_message_ids(state)
    groups = build_message_groups(messages)
    safe_runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    def flush() -> None:
        nonlocal current
        if current: safe_runs.append(current); current = []
    for group in groups:
        protected = group["protected"] or group["end"] > protected_start or any(is_collapse_boundary(m) for m in group["messages"]) or any(message_id(m, group["start"] + offset) in collapsed_ids for offset, m in enumerate(group["messages"]))
        if protected: flush(); continue
        current.append(group)
    flush()
    for run in safe_runs:
        cand = build_candidate_from_groups(messages, run, options)
        if cand: return cand
    return None


def message_to_collapse_text(message: dict[str, Any]) -> str:
    role = message.get("role")
    if role == "user": return f"[User]: {message.get('content', '')}"
    if role in {"assistant", "assistant_progress"}: return f"[Assistant]: {message.get('content', '')}"
    if role == "assistant_thinking": return "[Assistant Thinking]: preserved provider reasoning block"
    if role == "assistant_tool_call": return f"[Tool Call: {message.get('toolName')} {message.get('toolUseId')}]: {json.dumps(message.get('input'), ensure_ascii=False)}"
    if role == "tool_result": return f"[Tool Result: {message.get('toolName')} {message.get('toolUseId')}{' ERROR' if message.get('isError') else ''}]: {message.get('content', '')}"
    if role == "context_summary": return f"[Context Summary]: {message.get('content', '')}"
    if role == "snip_boundary": return f"[Snip Boundary]: {message.get('content', '')}"
    if role == "system": return "[System]: protected system message"
    return str(message)


def messages_to_collapse_text(messages: list[dict[str, Any]]) -> str:
    return "\n\n".join(message_to_collapse_text(m) for m in messages)


def build_context_collapse_summary_prompt(conversation_text: str) -> str:
    return f"""You are creating a local context-collapse summary for an AI coding session.
The summary will replace only this older message span in the model-visible context.
The original transcript remains preserved outside the model-visible projection.

Produce the final summary in <summary> tags.

Preserve:
- User intent and active goals
- Completed tasks and current state
- Important decisions and constraints
- Tool calls and tool results that still matter
- File reads/writes and code changes, with paths, function names, config names, and commands
- Errors, failures, warnings, and exact messages when relevant
- TODOs, uncertainty, follow-up constraints, and anything still relevant later

Rules:
- Do not invent facts or outcomes
- Do not omit critical paths, function names, configuration keys, file paths, or error text
- Keep it concise, but prefer specificity over vague compression
- This is not a full conversation compact; summarize only the provided span

Messages to summarize:

{conversation_text}"""


def failed_collapse_result(messages: list[dict[str, Any]], state: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    failures = state.get("consecutiveFailures", 0) + 1
    return {"messages": messages, "state": {**state, "spans": list(state.get("spans") or []), "consecutiveFailures": failures, "enabled": False if failures >= options["maxFailures"] else state.get("enabled", True)}, "collapsed": False, "spans": []}


def unchanged_collapse_result(messages: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    return {"messages": messages, "state": state, "collapsed": False, "spans": []}


def committed_collapse_result(messages: list[dict[str, Any]], state: dict[str, Any], planned_spans: list[dict[str, Any]]) -> dict[str, Any]:
    committed = [{**s, "status": "committed"} for s in planned_spans]
    next_state = {**state, "spans": [*(state.get("spans") or []), *committed], "consecutiveFailures": 0}
    return {"messages": project_collapsed_view(messages, next_state), "state": next_state, "collapsed": bool(committed), "span": committed[0] if committed else None, "spans": committed}


async def apply_context_collapse_if_needed(messages: list[dict[str, Any]], model: str, adapter: Any, state: dict[str, Any], raw_options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = with_default_options(raw_options)
    current_state = normalize_context_collapse_state(state)
    if not current_state.get("enabled", True):
        return unchanged_collapse_result(messages, current_state)
    current_projected = project_collapsed_view(messages, current_state)
    stats = compute_context_stats(current_projected, model)
    if stats["utilization"] < options["utilizationThreshold"]:
        return unchanged_collapse_result(current_projected, current_state)
    planned: list[dict[str, Any]] = []
    max_spans = max(1, int(options["maxSpansPerPass"]))
    for pass_idx in range(max_spans):
        selection_state = {**current_state, "spans": [*current_state.get("spans", []), *planned]}
        projected = project_collapsed_view(messages, selection_state)
        stats = compute_context_stats(projected, model)
        if planned and stats["utilization"] <= options["targetUsage"]:
            break
        candidate = find_collapse_candidate(messages, selection_state, {**options, "currentTokens": stats["totalTokens"], "effectiveInput": stats["effectiveInput"]})
        if not candidate:
            break
        summary_prompt = build_context_collapse_summary_prompt(messages_to_collapse_text(candidate["messages"]))
        req = [
            {"role": "system", "content": "You are a precise assistant that summarizes older coding-session context without inventing details."},
            {"role": "user", "content": summary_prompt},
        ]
        try:
            response = await adapter.next(req)
            if response.get("type") != "assistant" or not str(response.get("content", "")).strip():
                return failed_collapse_result(current_projected, current_state, options)
            summary = parse_summary_from_response(str(response.get("content", "")))
            if not summary:
                return failed_collapse_result(current_projected, current_state, options)
            now = int(time.time() * 1000)
            draft = {"id": f"collapse-{now}-{pass_idx}-{candidate['startMessageId']}", "startMessageId": candidate["startMessageId"], "endMessageId": candidate["endMessageId"], "messageIds": candidate["messageIds"], "summary": summary, "tokensBefore": candidate["tokensBefore"], "tokensAfter": 0, "status": "staged", "createdAt": now, "reason": options["reason"]}
            summary_tokens = estimate_messages_tokens([build_collapsed_summary_message(draft)])
            if max(0, candidate["tokensBefore"] - summary_tokens) < options["minTokensToSave"]:
                if planned: break
                return failed_collapse_result(current_projected, current_state, options)
            planned.append({**draft, "tokensAfter": summary_tokens})
        except Exception:
            return failed_collapse_result(current_projected, current_state, options)
    if not planned:
        return unchanged_collapse_result(current_projected, current_state)
    return committed_collapse_result(messages, current_state, planned)

createContextCollapseState = create_context_collapse_state
projectCollapsedView = project_collapsed_view
findCollapseCandidate = find_collapse_candidate
buildContextCollapseSummaryPrompt = build_context_collapse_summary_prompt
applyContextCollapseIfNeeded = apply_context_collapse_if_needed
