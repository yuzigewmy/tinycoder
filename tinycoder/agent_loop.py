from __future__ import annotations

from typing import Any

from .compact.auto_compact import auto_compact
from .compact.context_collapse import apply_context_collapse_if_needed, create_context_collapse_state
from .compact.microcompact import microcompact
from .compact.snip_compact import snip_compact_conversation
from .tool import ToolRegistry
from .utils.token_estimator import compute_context_stats
from .utils.tool_result_storage import apply_tool_result_budget, create_content_replacement_state, replace_large_tool_result


def is_empty_assistant_response(content: str) -> bool:
    return len(content.strip()) == 0


def with_provider_usage(message: dict[str, Any], usage: dict[str, Any] | None) -> dict[str, Any]:
    if not usage:
        return message
    if message.get("role") in {"assistant", "assistant_progress", "assistant_tool_call"}:
        next_message = dict(message)
        next_message["providerUsage"] = usage
        return next_message
    return message


def should_treat_assistant_as_progress(args: dict[str, Any]) -> bool:
    if args.get("kind") == "progress":
        return True
    if args.get("kind") == "final":
        return False
    if not args.get("sawToolResultThisTurn"):
        return False
    return False


def format_diagnostics(args: dict[str, Any]) -> str:
    parts: list[str] = []
    if args.get("stopReason"):
        parts.append(f"stop_reason={args.get('stopReason')}")
    if args.get("blockTypes"):
        parts.append("blocks=" + ",".join(args.get("blockTypes") or []))
    if args.get("ignoredBlockTypes"):
        parts.append("ignored=" + ",".join(args.get("ignoredBlockTypes") or []))
    return f" 诊断信息: {'; '.join(parts)}。" if parts else ""


def is_recoverable_thinking_stop(args: dict[str, Any]) -> bool:
    if not args.get("isEmpty"):
        return False
    if args.get("stopReason") not in {"pause_turn", "max_tokens"}:
        return False
    return "thinking" in (args.get("blockTypes") or []) or "thinking" in (args.get("ignoredBlockTypes") or [])


async def _maybe_call(callback: Any, *args: Any) -> None:
    if callback is None:
        return
    result = callback(*args)
    if hasattr(result, "__await__"):
        await result


async def run_agent_turn(args: dict[str, Any]) -> list[dict[str, Any]]:
    max_steps = args.get("maxSteps")
    model_name = args.get("modelName") or ""
    messages: list[dict[str, Any]] = args["messages"]
    empty_response_retry_count = 0
    recoverable_thinking_retry_count = 0
    tool_error_count = 0
    saw_tool_result_this_turn = False
    snipped_this_turn = False
    content_replacement_state = args.get("contentReplacementState") or create_content_replacement_state()
    context_collapse_state = args.get("contextCollapseState") or create_context_collapse_state()

    def replace_context_collapse_state(next_state: dict[str, Any]) -> None:
        nonlocal context_collapse_state
        context_collapse_state = next_state
        if args.get("contextCollapseState") is not None:
            target = args["contextCollapseState"]
            target["spans"] = list(next_state.get("spans") or [])
            target["enabled"] = bool(next_state.get("enabled", True))
            target["consecutiveFailures"] = int(next_state.get("consecutiveFailures", 0))

    def push_continuation_prompt(content: str) -> None:
        nonlocal messages
        messages = [*messages, {"role": "user", "content": content}]

    def append_thinking_blocks(blocks: list[dict[str, Any]] | None) -> None:
        nonlocal messages
        if blocks:
            messages = [*messages, {"role": "assistant_thinking", "blocks": blocks}]

    step = 0
    while max_steps is None or step < int(max_steps):
        latest_stats: dict[str, Any] | None = None
        model_messages = messages
        if model_name:
            latest_stats = compute_context_stats(messages, model_name)
            if not snipped_this_turn:
                snip_result = await snip_compact_conversation({"messages": messages, "contextStats": latest_stats, "modelContextWindow": latest_stats.get("effectiveInput")})
                if snip_result.get("didSnip"):
                    messages = snip_result["messages"]
                    snipped_this_turn = True
                    await _maybe_call(args.get("onSnipCompact"), snip_result)
                    latest_stats = compute_context_stats(messages, model_name)
                    await _maybe_call(args.get("onContextStats"), latest_stats)
            before = messages
            messages = microcompact(messages, model_name)
            if messages is not before:
                latest_stats = compute_context_stats(messages, model_name)
                await _maybe_call(args.get("onContextStats"), latest_stats)
            collapse_result = await apply_context_collapse_if_needed(messages, model_name, args["model"], context_collapse_state)
            replace_context_collapse_state(collapse_result["state"])
            model_messages = collapse_result["messages"]
            if collapse_result.get("collapsed"):
                await _maybe_call(args.get("onContextCollapse"), collapse_result)
                latest_stats = compute_context_stats(model_messages, model_name)
                await _maybe_call(args.get("onContextStats"), latest_stats)
            elif model_messages is not messages:
                latest_stats = compute_context_stats(model_messages, model_name)
                await _maybe_call(args.get("onContextStats"), latest_stats)

        if step == 0 and model_name:
            latest_stats = latest_stats or compute_context_stats(model_messages, model_name)
            await _maybe_call(args.get("onContextStats"), latest_stats)
            if latest_stats.get("warningLevel") in {"critical", "blocked"}:
                result = await auto_compact(model_messages, model_name, args["model"])
                if result:
                    messages = result["messages"]
                    model_messages = messages
                    replace_context_collapse_state(create_context_collapse_state())
                    await _maybe_call(args.get("onAutoCompact"), result)
                    latest_stats = compute_context_stats(messages, model_name)
                    await _maybe_call(args.get("onContextStats"), latest_stats)

        model_obj = args["model"]
        stream_next = getattr(model_obj, "stream_next", None)
        if stream_next is not None and args.get("onAssistantDelta") is not None:
            next_step = await stream_next(model_messages, on_text_delta=args.get("onAssistantDelta"))
        else:
            next_step = await model_obj.next(model_messages)
        if next_step.get("type") == "assistant":
            content = str(next_step.get("content") or "")
            is_empty = is_empty_assistant_response(content)
            if not is_empty and should_treat_assistant_as_progress({"kind": next_step.get("kind"), "content": content, "sawToolResultThisTurn": saw_tool_result_this_turn}):
                if not next_step.get("streamed"):
                    await _maybe_call(args.get("onProgressMessage"), content)
                append_thinking_blocks(next_step.get("thinkingBlocks"))
                messages = [*messages, {"role": "assistant_progress", "content": content}]
                push_continuation_prompt("Continue from your progress update. You have already used tools in this turn, so treat plain status text as progress, not a final answer. Respond with the next concrete tool call, code change, or an explicit <final> answer only if the task is truly complete." if saw_tool_result_this_turn and next_step.get("kind") != "progress" else "Continue immediately from your <progress> update with concrete tool calls, code changes, or an explicit <final> answer only if the task is complete.")
                step += 1
                continue

            diagnostics = next_step.get("diagnostics") or {}
            if is_recoverable_thinking_stop({"isEmpty": is_empty, "stopReason": diagnostics.get("stopReason"), "blockTypes": diagnostics.get("blockTypes"), "ignoredBlockTypes": diagnostics.get("ignoredBlockTypes")}) and recoverable_thinking_retry_count < 3:
                recoverable_thinking_retry_count += 1
                stop_reason = diagnostics.get("stopReason")
                progress = "模型在 thinking 阶段触发 max_tokens，正在继续请求后续步骤..." if stop_reason == "max_tokens" else "模型返回 pause_turn，正在继续请求后续步骤..."
                await _maybe_call(args.get("onProgressMessage"), progress)
                messages = [*messages, {"role": "assistant_progress", "content": progress}]
                push_continuation_prompt("Your previous response hit max_tokens during thinking before producing the next actionable step. Resume immediately and continue with the next concrete tool call, code change, or an explicit <final> answer only if the task is complete. Do not repeat the earlier plan." if stop_reason == "max_tokens" else "Resume from the previous pause_turn and continue the task immediately. Produce the next concrete tool call, code change, or an explicit <final> answer only if the task is complete.")
                step += 1
                continue

            if is_empty and empty_response_retry_count < 2:
                empty_response_retry_count += 1
                push_continuation_prompt("Your last response was empty after recent tool results. Continue immediately by trying the next concrete step, adapting to any tool errors, or giving an explicit <final> answer only if the task is complete." if saw_tool_result_this_turn else "Your last response was empty. Continue immediately with concrete tool calls, code changes, or an explicit <final> answer only if the task is complete.")
                step += 1
                continue

            if is_empty:
                suffix = format_diagnostics({"stopReason": diagnostics.get("stopReason"), "blockTypes": diagnostics.get("blockTypes"), "ignoredBlockTypes": diagnostics.get("ignoredBlockTypes")})
                if saw_tool_result_this_turn:
                    fallback = f"工具执行后模型返回空响应，已停止当前回合。最近有 {tool_error_count} 个工具报错；请重试、调整命令，或让模型改用其他方案。{suffix}" if tool_error_count > 0 else f"工具执行后模型返回空响应，已停止当前回合。请重试，或要求模型继续完成剩余步骤。{suffix}"
                else:
                    fallback = f"模型返回空响应，已停止当前回合。请重试，或要求模型继续。{suffix}"
                await _maybe_call(args.get("onAssistantMessage"), fallback)
                append_thinking_blocks(next_step.get("thinkingBlocks"))
                return [*messages, {"role": "assistant", "content": fallback}]

            assistant_message = {"role": "assistant", "content": content}
            append_thinking_blocks(next_step.get("thinkingBlocks"))
            if not next_step.get("streamed"):
                await _maybe_call(args.get("onAssistantMessage"), content)
            return [*messages, with_provider_usage(assistant_message, next_step.get("usage"))]

        append_thinking_blocks(next_step.get("thinkingBlocks"))
        if next_step.get("content"):
            content = str(next_step.get("content"))
            if next_step.get("contentKind") == "progress":
                if not next_step.get("streamed"):
                    await _maybe_call(args.get("onProgressMessage"), content)
                messages = [*messages, with_provider_usage({"role": "assistant_progress", "content": content}, next_step.get("usage"))]
                push_continuation_prompt("Continue immediately from your <progress> update with concrete tool calls, code changes, or an explicit <final> answer only if the task is complete.")
            else:
                if not next_step.get("streamed"):
                    await _maybe_call(args.get("onAssistantMessage"), content)
                usage = None if next_step.get("calls") else next_step.get("usage")
                messages = [*messages, with_provider_usage({"role": "assistant", "content": content}, usage)]
        calls = next_step.get("calls") or []
        if not calls and next_step.get("content") and next_step.get("contentKind") != "progress":
            return messages

        executed: list[dict[str, Any]] = []
        for call in calls:
            await _maybe_call(args.get("onToolStart"), call.get("toolName"), call.get("input"))
            result = await args["tools"].execute(call.get("toolName"), call.get("input"), {"cwd": args.get("cwd"), "permissions": args.get("permissions")})
            saw_tool_result_this_turn = True
            if not result.get("ok"):
                tool_error_count += 1
            await _maybe_call(args.get("onToolResult"), call.get("toolName"), str(result.get("output") or ""), not bool(result.get("ok")))
            tool_result = await replace_large_tool_result({"role": "tool_result", "toolUseId": call.get("id"), "toolName": call.get("toolName"), "content": str(result.get("output") or ""), "isError": not bool(result.get("ok"))}, content_replacement_state)
            executed.append({"call": call, "result": result, "toolResult": tool_result})

        budgeted = await apply_tool_result_budget([entry["toolResult"] for entry in executed], content_replacement_state)
        tool_result_by_id = {result.get("toolUseId"): result for result in budgeted.get("results") or []}
        tool_call_messages: list[dict[str, Any]] = []
        for i, entry in enumerate(executed):
            call = entry["call"]
            msg = {"role": "assistant_tool_call", "toolUseId": call.get("id"), "toolName": call.get("toolName"), "input": call.get("input")}
            tool_call_messages.append(with_provider_usage(msg, next_step.get("usage") if i == len(executed) - 1 else None))
        tool_results = [tool_result_by_id.get(entry["call"].get("id"), entry["toolResult"]) for entry in executed]
        messages = [*messages, *tool_call_messages, *tool_results]

        await_user = next((entry for entry in executed if entry["result"].get("awaitUser")), None)
        if await_user:
            question = str(await_user["result"].get("output") or "").strip()
            if question:
                await _maybe_call(args.get("onAssistantMessage"), question)
                messages = [*messages, {"role": "assistant", "content": question}]
            return messages
        step += 1

    max_step_content = "达到最大工具步数限制，已停止当前回合。"
    await _maybe_call(args.get("onAssistantMessage"), max_step_content)
    return [*messages, {"role": "assistant", "content": max_step_content}]


runAgentTurn = run_agent_turn
isEmptyAssistantResponse = is_empty_assistant_response
withProviderUsage = with_provider_usage
shouldTreatAssistantAsProgress = should_treat_assistant_as_progress
formatDiagnostics = format_diagnostics
isRecoverableThinkingStop = is_recoverable_thinking_stop
