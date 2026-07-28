from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from .compact.auto_compact import auto_compact
from .compact.context_collapse import apply_context_collapse_if_needed, create_context_collapse_state
from .compact.microcompact import microcompact
from .compact.snip_compact import snip_compact_conversation
from .memory.context import inject_context_message, inject_memory_context
from .tool import ToolRegistry
from .turn_controller import (
    GuardDecision,
    TurnBudget,
    TurnController,
    TurnStopReason,
    format_recovery_prompt,
    format_stop_message,
)
from .utils.token_estimator import compute_context_stats
from .utils.tool_result_storage import apply_tool_result_budget, create_content_replacement_state, replace_large_tool_result


MUTATING_FILE_TOOLS = {"write_file", "modify_file", "edit_file", "patch_file"}


def _workspace_file_fingerprint(cwd: str, tool_name: str, input_value: Any) -> str | None:
    if tool_name not in MUTATING_FILE_TOOLS or not isinstance(input_value, dict):
        return None
    raw_path = input_value.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    workspace = Path(cwd).resolve()
    target = (workspace / raw_path).resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        return None
    if not target.exists():
        return "missing"
    if not target.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


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


async def _maybe_call_value(callback: Any, *args: Any) -> Any:
    if callback is None:
        return None
    result = callback(*args)
    if hasattr(result, "__await__"):
        return await result
    return result


async def run_agent_turn(args: dict[str, Any]) -> list[dict[str, Any]]:
    model_name = args.get("modelName") or ""
    message_sink: list[dict[str, Any]] = args["messages"]
    messages: list[dict[str, Any]] = message_sink
    turn_controller = TurnController(TurnBudget.from_args(args))
    empty_response_retry_count = 0
    recoverable_thinking_retry_count = 0
    tool_error_count = 0
    saw_tool_result_this_turn = False
    snipped_this_turn = False
    content_replacement_state = args.get("contentReplacementState") or create_content_replacement_state()
    context_collapse_state = args.get("contextCollapseState") or create_context_collapse_state()
    instruction_context = str(args.get("instructionContext") or "")
    memory_context = ""
    memory_context_loaded = False

    def commit_messages(next_messages: list[dict[str, Any]]) -> None:
        nonlocal messages
        messages = next_messages
        if message_sink is not next_messages:
            message_sink[:] = next_messages

    async def checkpoint() -> None:
        await _maybe_call(
            args.get("onTurnCheckpoint"),
            list(messages),
            turn_controller.snapshot(),
        )

    async def stop_turn(reason: TurnStopReason) -> list[dict[str, Any]]:
        content = format_stop_message(reason, turn_controller.snapshot())
        await _maybe_call(args.get("onAssistantMessage"), content)
        commit_messages([
            *messages,
            {
                "role": "assistant",
                "content": content,
                "stopReason": reason.to_dict(),
                "turnState": turn_controller.snapshot(),
            },
        ])
        await checkpoint()
        return messages

    async def recover_or_stop(decision: GuardDecision) -> list[dict[str, Any]] | None:
        if decision.action == "stop" and decision.reason:
            return await stop_turn(decision.reason)
        if decision.action == "recover" and decision.reason:
            commit_messages([
                *messages,
                {
                    "role": "user",
                    "content": format_recovery_prompt(decision.reason),
                    "synthetic": True,
                    "contextKind": "agent_recovery",
                },
            ])
            await checkpoint()
        return None

    def replace_context_collapse_state(next_state: dict[str, Any]) -> None:
        nonlocal context_collapse_state
        context_collapse_state = next_state
        if args.get("contextCollapseState") is not None:
            target = args["contextCollapseState"]
            target["spans"] = list(next_state.get("spans") or [])
            target["enabled"] = bool(next_state.get("enabled", True))
            target["consecutiveFailures"] = int(next_state.get("consecutiveFailures", 0))

    def push_continuation_prompt(content: str) -> None:
        commit_messages(
            [
                *messages,
                {
                    "role": "user",
                    "content": content,
                    "synthetic": True,
                    "contextKind": "agent_recovery",
                },
            ]
        )

    def append_thinking_blocks(blocks: list[dict[str, Any]] | None) -> None:
        if blocks:
            commit_messages([*messages, {"role": "assistant_thinking", "blocks": blocks}])

    step = 0
    while True:
        limit_reason = turn_controller.before_model_step()
        if limit_reason:
            return await stop_turn(limit_reason)
        latest_stats: dict[str, Any] | None = None
        model_messages = messages
        if model_name:
            latest_stats = compute_context_stats(messages, model_name)
            if not snipped_this_turn:
                snip_result = await snip_compact_conversation({"messages": messages, "contextStats": latest_stats, "modelContextWindow": latest_stats.get("effectiveInput")})
                if snip_result.get("didSnip"):
                    commit_messages(snip_result["messages"])
                    snipped_this_turn = True
                    await _maybe_call(args.get("onSnipCompact"), snip_result)
                    latest_stats = compute_context_stats(messages, model_name)
                    await _maybe_call(args.get("onContextStats"), latest_stats)
            before = messages
            compacted_messages = microcompact(messages, model_name)
            if compacted_messages is not before:
                commit_messages(compacted_messages)
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
                    commit_messages(result["messages"])
                    model_messages = messages
                    replace_context_collapse_state(create_context_collapse_state())
                    await _maybe_call(args.get("onAutoCompact"), result)
                    latest_stats = compute_context_stats(messages, model_name)
                    await _maybe_call(args.get("onContextStats"), latest_stats)

        if not memory_context_loaded:
            memory_context_loaded = True
            try:
                memory_context = str(
                    await _maybe_call_value(
                        args.get("memoryContextProvider"),
                        list(model_messages),
                    )
                    or ""
                )
            except Exception as error:
                memory_context = ""
                await _maybe_call(args.get("onMemoryError"), error)
        model_messages = inject_context_message(
            model_messages,
            instruction_context,
            context_kind="instructions",
        )
        model_messages = inject_memory_context(model_messages, memory_context)
        if model_name and (instruction_context or memory_context):
            latest_stats = compute_context_stats(model_messages, model_name)
            await _maybe_call(args.get("onContextStats"), latest_stats)

        model_obj = args["model"]
        stream_next = getattr(model_obj, "stream_next", None)
        try:
            if stream_next is not None and args.get("onAssistantDelta") is not None:
                model_request = stream_next(model_messages, on_text_delta=args.get("onAssistantDelta"))
            else:
                model_request = model_obj.next(model_messages)
            next_step = await asyncio.wait_for(
                model_request,
                timeout=turn_controller.remaining_seconds(),
            )
        except asyncio.TimeoutError:
            return await stop_turn(turn_controller.wall_timeout_reason())
        usage_limit_reason = turn_controller.record_model_usage(next_step.get("usage"))
        if next_step.get("type") == "assistant":
            content = str(next_step.get("content") or "")
            is_empty = is_empty_assistant_response(content)
            if not is_empty and should_treat_assistant_as_progress({"kind": next_step.get("kind"), "content": content, "sawToolResultThisTurn": saw_tool_result_this_turn}):
                if usage_limit_reason:
                    return await stop_turn(usage_limit_reason)
                progress_decision = turn_controller.record_progress()
                if progress_decision.action == "stop" and progress_decision.reason:
                    return await stop_turn(progress_decision.reason)
                if not next_step.get("streamed"):
                    await _maybe_call(args.get("onProgressMessage"), content)
                append_thinking_blocks(next_step.get("thinkingBlocks"))
                commit_messages([*messages, {"role": "assistant_progress", "content": content}])
                if progress_decision.action == "recover" and progress_decision.reason:
                    push_continuation_prompt(format_recovery_prompt(progress_decision.reason))
                else:
                    push_continuation_prompt("Continue from your progress update. You have already used tools in this turn, so treat plain status text as progress, not a final answer. Respond with the next concrete tool call, code change, or an explicit <final> answer only if the task is truly complete." if saw_tool_result_this_turn and next_step.get("kind") != "progress" else "Continue immediately from your <progress> update with concrete tool calls, code changes, or an explicit <final> answer only if the task is complete.")
                await checkpoint()
                step += 1
                continue

            diagnostics = next_step.get("diagnostics") or {}
            if is_recoverable_thinking_stop({"isEmpty": is_empty, "stopReason": diagnostics.get("stopReason"), "blockTypes": diagnostics.get("blockTypes"), "ignoredBlockTypes": diagnostics.get("ignoredBlockTypes")}) and recoverable_thinking_retry_count < turn_controller.budget.max_thinking_retries:
                if usage_limit_reason:
                    return await stop_turn(usage_limit_reason)
                recoverable_thinking_retry_count += 1
                progress_decision = turn_controller.record_progress()
                if progress_decision.action == "stop" and progress_decision.reason:
                    return await stop_turn(progress_decision.reason)
                stop_reason = diagnostics.get("stopReason")
                progress = "模型在 thinking 阶段触发 max_tokens，正在继续请求后续步骤..." if stop_reason == "max_tokens" else "模型返回 pause_turn，正在继续请求后续步骤..."
                await _maybe_call(args.get("onProgressMessage"), progress)
                commit_messages([*messages, {"role": "assistant_progress", "content": progress}])
                if progress_decision.action == "recover" and progress_decision.reason:
                    push_continuation_prompt(format_recovery_prompt(progress_decision.reason))
                else:
                    push_continuation_prompt("Your previous response hit max_tokens during thinking before producing the next actionable step. Resume immediately and continue with the next concrete tool call, code change, or an explicit <final> answer only if the task is complete. Do not repeat the earlier plan." if stop_reason == "max_tokens" else "Resume from the previous pause_turn and continue the task immediately. Produce the next concrete tool call, code change, or an explicit <final> answer only if the task is complete.")
                await checkpoint()
                step += 1
                continue

            if is_empty and empty_response_retry_count < turn_controller.budget.max_empty_responses:
                if usage_limit_reason:
                    return await stop_turn(usage_limit_reason)
                empty_response_retry_count += 1
                push_continuation_prompt("Your last response was empty after recent tool results. Continue immediately by trying the next concrete step, adapting to any tool errors, or giving an explicit <final> answer only if the task is complete." if saw_tool_result_this_turn else "Your last response was empty. Continue immediately with concrete tool calls, code changes, or an explicit <final> answer only if the task is complete.")
                await checkpoint()
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
                commit_messages([*messages, {"role": "assistant", "content": fallback}])
                await checkpoint()
                return messages

            assistant_message = {"role": "assistant", "content": content}
            append_thinking_blocks(next_step.get("thinkingBlocks"))
            if not next_step.get("streamed"):
                await _maybe_call(args.get("onAssistantMessage"), content)
            commit_messages([*messages, with_provider_usage(assistant_message, next_step.get("usage"))])
            await checkpoint()
            return messages

        if usage_limit_reason:
            return await stop_turn(usage_limit_reason)
        append_thinking_blocks(next_step.get("thinkingBlocks"))
        calls = next_step.get("calls") or []
        if next_step.get("content"):
            content = str(next_step.get("content"))
            if next_step.get("contentKind") == "progress":
                if not next_step.get("streamed"):
                    await _maybe_call(args.get("onProgressMessage"), content)
                commit_messages([*messages, with_provider_usage({"role": "assistant_progress", "content": content}, next_step.get("usage"))])
                if not calls:
                    push_continuation_prompt("Continue immediately from your <progress> update with concrete tool calls, code changes, or an explicit <final> answer only if the task is complete.")
            else:
                if not next_step.get("streamed"):
                    await _maybe_call(args.get("onAssistantMessage"), content)
                usage = None if calls else next_step.get("usage")
                commit_messages([*messages, with_provider_usage({"role": "assistant", "content": content}, usage)])
        if not calls and next_step.get("content") and next_step.get("contentKind") != "progress":
            await checkpoint()
            return messages
        if not calls:
            progress_decision = turn_controller.record_progress()
            if progress_decision.action == "stop" and progress_decision.reason:
                return await stop_turn(progress_decision.reason)
            if progress_decision.action == "recover" and progress_decision.reason:
                push_continuation_prompt(format_recovery_prompt(progress_decision.reason))
            await checkpoint()
            step += 1
            continue

        executed: list[dict[str, Any]] = []
        pending_decision: GuardDecision | None = None
        await_user: dict[str, Any] | None = None
        for call in calls:
            tool_name = str(call.get("toolName") or "")
            input_value = call.get("input")
            guard_decision = turn_controller.before_tool_call(tool_name, input_value)
            if guard_decision.action != "continue":
                reason = guard_decision.reason
                output = (
                    f"Agent loop guard blocked this tool call: {reason.summary}. "
                    f"{reason.detail}"
                    if reason
                    else "Agent loop guard blocked this tool call."
                )
                result = {"ok": False, "output": output, "loopGuard": True}
                await _maybe_call(args.get("onToolResult"), tool_name, output, True)
                tool_result = await replace_large_tool_result(
                    {
                        "role": "tool_result",
                        "toolUseId": call.get("id"),
                        "toolName": tool_name,
                        "content": output,
                        "isError": True,
                    },
                    content_replacement_state,
                )
                executed.append({"call": call, "result": result, "toolResult": tool_result})
                pending_decision = guard_decision
                break

            file_before = _workspace_file_fingerprint(
                str(args.get("cwd") or "."),
                tool_name,
                input_value,
            )
            await _maybe_call(args.get("onToolStart"), call.get("toolName"), call.get("input"))
            try:
                result = await asyncio.wait_for(
                    args["tools"].execute(
                        tool_name,
                        input_value,
                        {"cwd": args.get("cwd"), "permissions": args.get("permissions")},
                    ),
                    timeout=turn_controller.remaining_seconds(),
                )
            except asyncio.TimeoutError:
                output = (
                    "Tool execution timed out at the Agent Loop wall-clock limit. "
                    "The operation may have produced partial side effects; inspect the "
                    "workspace before retrying."
                )
                await _maybe_call(args.get("onToolResult"), tool_name, output, True)
                tool_result = {
                    "role": "tool_result",
                    "toolUseId": call.get("id"),
                    "toolName": tool_name,
                    "content": output,
                    "isError": True,
                    "executionUncertain": True,
                }
                executed.append(
                    {
                        "call": call,
                        "result": {"ok": False, "output": output},
                        "toolResult": tool_result,
                    }
                )
                pending_decision = GuardDecision(
                    "stop",
                    turn_controller.wall_timeout_reason(),
                )
                break
            except (asyncio.CancelledError, KeyboardInterrupt):
                output = (
                    "Tool execution was interrupted. Completion and side effects are "
                    "uncertain; inspect the workspace before retrying."
                )
                interrupted_result = {
                    "role": "tool_result",
                    "toolUseId": call.get("id"),
                    "toolName": tool_name,
                    "content": output,
                    "isError": True,
                    "executionUncertain": True,
                }
                checkpoint_entries = [
                    *executed,
                    {
                        "call": call,
                        "result": {"ok": False, "output": output},
                        "toolResult": interrupted_result,
                    },
                ]
                checkpoint_calls: list[dict[str, Any]] = []
                for index, checkpoint_entry in enumerate(checkpoint_entries):
                    checkpoint_call = checkpoint_entry["call"]
                    checkpoint_calls.append(
                        with_provider_usage(
                            {
                                "role": "assistant_tool_call",
                                "toolUseId": checkpoint_call.get("id"),
                                "toolName": checkpoint_call.get("toolName"),
                                "input": checkpoint_call.get("input"),
                            },
                            next_step.get("usage")
                            if index == len(checkpoint_entries) - 1
                            else None,
                        )
                    )
                commit_messages([
                    *messages,
                    *checkpoint_calls,
                    *[entry["toolResult"] for entry in checkpoint_entries],
                ])
                await checkpoint()
                raise
            file_after = _workspace_file_fingerprint(
                str(args.get("cwd") or "."),
                tool_name,
                input_value,
            )
            file_changed = (
                file_before != file_after
                if file_before is not None and file_after is not None
                else None
            )
            saw_tool_result_this_turn = True
            if not result.get("ok"):
                tool_error_count += 1
            output = str(result.get("output") or "")
            await _maybe_call(args.get("onToolResult"), tool_name, output, not bool(result.get("ok")))
            tool_result = await replace_large_tool_result({"role": "tool_result", "toolUseId": call.get("id"), "toolName": tool_name, "content": output, "isError": not bool(result.get("ok"))}, content_replacement_state)
            entry = {"call": call, "result": result, "toolResult": tool_result}
            executed.append(entry)
            result_decision = turn_controller.record_tool_result(
                tool_name,
                bool(result.get("ok")),
                output,
                file_changed=file_changed,
            )
            if result.get("awaitUser"):
                await_user = entry
                break
            if result_decision.action != "continue":
                pending_decision = result_decision
                break

        budgeted = await apply_tool_result_budget([entry["toolResult"] for entry in executed], content_replacement_state)
        tool_result_by_id = {result.get("toolUseId"): result for result in budgeted.get("results") or []}
        tool_call_messages: list[dict[str, Any]] = []
        for i, entry in enumerate(executed):
            call = entry["call"]
            msg = {"role": "assistant_tool_call", "toolUseId": call.get("id"), "toolName": call.get("toolName"), "input": call.get("input")}
            tool_call_messages.append(with_provider_usage(msg, next_step.get("usage") if i == len(executed) - 1 else None))
        tool_results = [tool_result_by_id.get(entry["call"].get("id"), entry["toolResult"]) for entry in executed]
        commit_messages([*messages, *tool_call_messages, *tool_results])
        await checkpoint()

        if await_user:
            question = str(await_user["result"].get("output") or "").strip()
            if question:
                await _maybe_call(args.get("onAssistantMessage"), question)
                commit_messages([*messages, {"role": "assistant", "content": question}])
                await checkpoint()
            return messages
        if pending_decision:
            stopped = await recover_or_stop(pending_decision)
            if stopped is not None:
                return stopped
        step += 1

runAgentTurn = run_agent_turn
isEmptyAssistantResponse = is_empty_assistant_response
withProviderUsage = with_provider_usage
shouldTreatAssistantAsProgress = should_treat_assistant_as_progress
formatDiagnostics = format_diagnostics
isRecoverableThinkingStop = is_recoverable_thinking_stop
