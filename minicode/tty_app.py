from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from .agent_loop import run_agent_turn
from .background_tasks import list_background_tasks
from .cli_commands import find_matching_slash_commands, try_handle_local_command
from .compact.context_collapse import apply_context_collapse_if_needed, create_context_collapse_state
from .compact.manual_compact import manual_compact
from .compact.snip_compact import snip_compact_conversation
from .history import load_history_entries, save_history_entries
from .local_tool_shortcuts import parse_local_tool_shortcut
from .permissions import PermissionManager
from .prompt import build_system_prompt
from .session import append_compact_boundary, append_context_collapse_span, append_snip_boundary, clear_session, fork_session, list_sessions, load_context_collapse_state, load_session, rename_session, save_session
from .ui import render_banner, render_permission_prompt
from .utils.token_estimator import compute_context_stats


def keep_selection_after_mouse_release(selection: dict[str, Any] | None) -> dict[str, Any] | None:
    return selection


def encode_clipboard_text_for_platform(platform: str, text: str) -> str | bytes:
    if platform == "win32":
        return b"\xff\xfe" + text.encode("utf-16le")
    return text


async def _permission_prompt(request: dict[str, Any]) -> dict[str, Any]:
    print("\n" + render_permission_prompt(request) + "\n")
    choice_map = {str(choice.get("key")): choice for choice in request.get("choices") or []}
    while True:
        answer = input("permission choice: ").strip()
        if answer in choice_map:
            choice = choice_map[answer]
            result = {"decision": choice.get("decision")}
            if choice.get("decision") == "deny_with_feedback":
                result["feedback"] = input("feedback to model: ")
            return result
        print("Invalid choice.")


def _last_assistant_content(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return str(message.get("content") or "")
    return None


async def _refresh_system_prompt(args: dict[str, Any]) -> None:
    args["messages"][0] = {"role": "system", "content": await build_system_prompt(args["cwd"], args["permissions"].get_summary(), {"skills": args["tools"].get_skills(), "mcpServers": args["tools"].get_mcp_servers()})}


async def run_tty_app(args: dict[str, Any]) -> None:
    cwd = args["cwd"]
    permissions: PermissionManager = args["permissions"]
    permissions.prompt = _permission_prompt
    messages = args["messages"]
    session_id = args.get("sessionId") or "default"
    already_saved_count = int(args.get("alreadySavedCount") or 0)
    history = load_history_entries()
    resume_target = args.get("resumeTarget")
    if resume_target and resume_target != "picker":
        loaded = await load_session(cwd, str(resume_target))
        if loaded:
            messages[:] = [messages[0], *loaded]
            session_id = str(resume_target)
            restored_collapse = await load_context_collapse_state(cwd, session_id)
            if restored_collapse and args.get("contextCollapseState") is not None:
                args["contextCollapseState"].update(restored_collapse)
            print(f"Resumed session {session_id}.")
        else:
            print(f"Session {resume_target} not found.")
    elif resume_target == "picker":
        sessions = await list_sessions(cwd)
        if sessions:
            for i, meta in enumerate(sessions, 1):
                print(f"{i}. {meta.get('id')}  {meta.get('title') or '(untitled)'}")
            chosen = input("resume number or id: ").strip()
            target = None
            if chosen.isdigit() and 1 <= int(chosen) <= len(sessions):
                target = sessions[int(chosen) - 1]["id"]
            elif chosen:
                target = chosen
            if target:
                loaded = await load_session(cwd, target)
                if loaded:
                    messages[:] = [messages[0], *loaded]
                    session_id = target
                    print(f"Resumed session {session_id}.")

    print(render_banner(args.get("runtime") or {}, cwd))
    print("Type /help for commands, /exit to quit.")

    while True:
        try:
            raw = input("mini-code> ")
        except (EOFError, KeyboardInterrupt):
            print("")
            break
        input_text = raw.strip()
        if not input_text:
            continue
        if input_text == "/exit":
            break
        history.append(input_text)
        try:
            if input_text == "/new":
                await clear_session(cwd, session_id)
                messages[:] = [messages[0]]
                print("Started a new session.")
                continue
            if input_text.startswith("/rename "):
                title = input_text[len("/rename "):].strip()
                if title:
                    ok = await rename_session(cwd, session_id, title)
                    print("Renamed." if ok else "Session not found.")
                continue
            if input_text == "/resume":
                sessions = await list_sessions(cwd)
                if not sessions:
                    print("No saved sessions.")
                else:
                    for meta in sessions[:20]:
                        print(f"{meta.get('id')}  {meta.get('title') or '(untitled)'}")
                continue
            if input_text.startswith("/resume "):
                target = input_text[len("/resume "):].strip()
                loaded = await load_session(cwd, target)
                if not loaded:
                    print("Session not found.")
                else:
                    messages[:] = [messages[0], *loaded]
                    session_id = target
                    print(f"Resumed session {session_id}.")
                continue
            if input_text == "/fork":
                forked = await fork_session(cwd, session_id)
                print(f"Forked session: {forked}" if forked else "Nothing to fork.")
                continue
            if input_text == "/compact":
                result = await manual_compact(messages, args["model"])
                if not result:
                    print("Nothing to compact.")
                    continue
                messages[:] = result["messages"]
                await append_compact_boundary(cwd, session_id, result.get("summaryText") or "", "manual", result.get("preTokens") or 0, result.get("postTokens") or 0, result.get("retainedMessages") or [])
                print("Context compacted.")
                continue
            if input_text == "/collapse":
                runtime = args.get("runtime") or {}
                model_name = runtime.get("model") or ""
                if not model_name:
                    print("No model configured. Cannot collapse context.")
                    continue
                state = args.get("contextCollapseState") or create_context_collapse_state()
                result = await apply_context_collapse_if_needed(messages, model_name, args["model"], state, {"utilizationThreshold": 0, "reason": "manual"})
                if args.get("contextCollapseState") is not None:
                    args["contextCollapseState"].update(result["state"])
                for span in result.get("spans") or []:
                    await append_context_collapse_span(cwd, session_id, span)
                print(f"Collapsed {len(result.get('spans') or [])} span(s)." if result.get("collapsed") else "Nothing safe to collapse.")
                continue
            if input_text == "/snip":
                runtime = args.get("runtime") or {}
                model_name = runtime.get("model") or ""
                stats = compute_context_stats(messages, model_name) if model_name else {"effectiveInput": 1}
                result = await snip_compact_conversation({"messages": messages, "contextStats": stats, "modelContextWindow": stats.get("effectiveInput")})
                if result.get("didSnip"):
                    messages[:] = result["messages"]
                    if result.get("boundaryMessage"):
                        await append_snip_boundary(cwd, session_id, result["boundaryMessage"])
                    print("Snipped context.")
                else:
                    print("Nothing safe to snip.")
                continue
            if input_text == "/background":
                tasks = list_background_tasks()
                print("\n".join(f"{t.get('taskId')} pid={t.get('pid')} status={t.get('status')} {t.get('command')}" for t in tasks) if tasks else "No background tasks.")
                continue
            local_result = await try_handle_local_command(input_text, {"tools": args["tools"]})
            if local_result is not None:
                print(local_result)
                continue
            shortcut = parse_local_tool_shortcut(input_text)
            if shortcut:
                result = await args["tools"].execute(shortcut["toolName"], shortcut.get("input"), {"cwd": cwd, "permissions": permissions})
                print(str(result.get("output") or ""))
                continue
            if input_text.startswith("/"):
                matches = find_matching_slash_commands(input_text)
                print("未识别命令。" + ("你是不是想输入：\n" + "\n".join(matches) if matches else "输入 /help 查看可用命令。"))
                continue

            await _refresh_system_prompt(args)
            messages.append({"role": "user", "content": input_text})
            permissions.begin_turn()
            try:
                messages[:] = await run_agent_turn({
                    "model": args["model"],
                    "tools": args["tools"],
                    "messages": messages,
                    "cwd": cwd,
                    "permissions": permissions,
                    "modelName": (args.get("runtime") or {}).get("model") or "",
                    "contentReplacementState": args.get("contentReplacementState"),
                    "contextCollapseState": args.get("contextCollapseState"),
                    "onToolStart": lambda name, inp: print(f"[tool] {name} {inp}"),
                    "onToolResult": lambda name, out, is_error: print(f"[tool:{name} {'err' if is_error else 'ok'}]\n{out}"),
                    "onAssistantMessage": lambda content: print(f"\n{content}\n"),
                    "onProgressMessage": lambda content: print(f"\n[progress] {content}\n"),
                })
            finally:
                permissions.end_turn()
                await save_session(cwd, session_id, messages, already_saved_count)
        except Exception as error:
            print(f"error: {error}")
    try:
        save_history_entries(history, cwd, session_id)
    except Exception:
        pass


runTtyApp = run_tty_app
keepSelectionAfterMouseRelease = keep_selection_after_mouse_release
encodeClipboardTextForPlatform = encode_clipboard_text_for_platform
