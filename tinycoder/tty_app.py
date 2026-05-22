from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from .agent_loop import run_agent_turn
from .background_tasks import list_background_tasks
from .cli_commands import complete_slash_command_name, find_matching_slash_commands, try_handle_local_command
from .compact.context_collapse import apply_context_collapse_if_needed, create_context_collapse_state
from .compact.manual_compact import manual_compact
from .compact.snip_compact import snip_compact_conversation
from .history import clear_history_entries, load_history_entries, save_history_entries
from .local_tool_shortcuts import parse_local_tool_shortcut
from .permissions import PermissionManager
from .prompt import build_system_prompt
from .session import append_compact_boundary, append_context_collapse_span, append_snip_boundary, clear_session, fork_session, list_sessions, load_context_collapse_state, load_session, load_transcript, rename_session, save_session
from .tui.markdown import MarkdownStreamPrinter, is_markdown_path, render_markdownish
from .tui.transcript import render_transcript_lines
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


def _render_assistant_output(content: Any) -> str:
    return render_markdownish(str(content or ""))


def _render_shortcut_output(shortcut: dict[str, Any], output: Any) -> str:
    text = str(output or "")
    input_value = shortcut.get("input") if isinstance(shortcut.get("input"), dict) else {}
    path = str(input_value.get("path") or "")
    if shortcut.get("renderMarkdown") or (shortcut.get("toolName") == "read_file" and is_markdown_path(path)):
        return render_markdownish(text)
    return text


MAX_STATUS_PREVIEW = 120


def _single_line_preview(value: Any, limit: int = MAX_STATUS_PREVIEW) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = " ".join(text.split())
    return text[:limit - 3] + "..." if len(text) > limit else text


def _render_progress_node(content: Any) -> str:
    preview = _single_line_preview(content)
    return f"[thinking] {preview}" if preview else "[thinking] working..."


def _render_tool_start(name: Any, input_value: Any) -> None:
    preview = _single_line_preview(input_value)
    suffix = f" {preview}" if preview else ""
    print(f"[tool] {name}{suffix}")


def _render_tool_result(name: Any, output: Any, is_error: bool) -> None:
    status = "err" if is_error else "ok"
    preview = _single_line_preview(output)
    suffix = f" - {preview}" if preview else ""
    print(f"[tool:{name} {status}]{suffix}")


SENSITIVE_MODEL_COMMANDS = ("/apikey ", "/use ")
MODEL_CONFIG_COMMANDS = ("/provider ", "/model ", "/apikey ", "/base-url ", "/use ")
PROMPT_RED = "\033[31m"
PROMPT_RESET = "\033[0m"
PROMPT_TEXT = "tinycoder> "
COLORED_PROMPT = f"{PROMPT_RED}{PROMPT_TEXT}{PROMPT_RESET}"
INTERRUPTED_MESSAGE = "已中断当前模型输出。"


def _is_sensitive_model_command(input_text: str) -> bool:
    return any(input_text.startswith(prefix) for prefix in SENSITIVE_MODEL_COMMANDS)


def _is_model_config_command(input_text: str) -> bool:
    return input_text in {"/provider", "/model", "/apikey", "/base-url", "/status"} or any(input_text.startswith(prefix) for prefix in MODEL_CONFIG_COMMANDS)


def _read_interactive_line(prompt: str, history_entries: list[str] | None = None) -> str:
    if os.name == "nt":
        return _read_interactive_line_windows(prompt, history_entries or [])
    return _read_interactive_line_posix(prompt, history_entries or [])


def _redraw_prompt(prompt: str, buffer: str) -> None:
    print(f"\r{prompt}{buffer}\033[K", end="", flush=True)


def _apply_tab_completion(prompt: str, buffer: str) -> str:
    completed = complete_slash_command_name(buffer)
    if completed and completed != buffer:
        buffer = completed
        _redraw_prompt(prompt, buffer)
    return buffer


def _history_candidates(history_entries: list[str]) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []
    for entry in reversed(history_entries):
        value = entry.strip()
        if not value.startswith("/") or value in seen:
            continue
        candidates.append(value)
        seen.add(value)
    return candidates


def _apply_history_navigation(prompt: str, buffer: str, direction: int, state: dict[str, Any]) -> str:
    if not buffer.startswith("/"):
        return buffer
    candidates = state.get("candidates") or []
    if not candidates:
        return buffer
    if state.get("index") is None:
        if direction > 0:
            return buffer
        state["draft"] = buffer
        state["index"] = 0
    else:
        next_index = int(state["index"]) + (1 if direction < 0 else -1)
        if next_index < 0:
            state["index"] = None
            next_buffer = str(state.get("draft") or "")
            _redraw_prompt(prompt, next_buffer)
            return next_buffer
        state["index"] = min(len(candidates) - 1, next_index)
    next_buffer = candidates[int(state["index"])]
    _redraw_prompt(prompt, next_buffer)
    return next_buffer


def _reset_history_navigation(state: dict[str, Any]) -> None:
    state["index"] = None


def _format_history_entries(history_entries: list[str], limit: int = 50) -> str:
    candidates = list(reversed(_history_candidates(history_entries)))[:limit]
    if not candidates:
        return "No command history."
    width = len(str(len(candidates)))
    return "\n".join(f"{str(index).rjust(width)}. {entry}" for index, entry in enumerate(candidates, 1))


def _read_interactive_line_windows(prompt: str, history_entries: list[str]) -> str:
    import msvcrt

    buffer = ""
    history_state: dict[str, Any] = {"candidates": _history_candidates(history_entries), "index": None, "draft": ""}
    print(prompt, end="", flush=True)
    while True:
        char = msvcrt.getwch()
        if char in {"\r", "\n"}:
            print("")
            return buffer
        if char == "\u0003":
            raise KeyboardInterrupt
        if char == "\t":
            buffer = _apply_tab_completion(prompt, buffer)
            _reset_history_navigation(history_state)
            continue
        if char in {"\b", "\x7f"}:
            if buffer:
                buffer = buffer[:-1]
                _reset_history_navigation(history_state)
                _redraw_prompt(prompt, buffer)
            continue
        if char in {"\x00", "\xe0"}:
            key = msvcrt.getwch()
            if key == "H":
                buffer = _apply_history_navigation(prompt, buffer, -1, history_state)
            elif key == "P":
                buffer = _apply_history_navigation(prompt, buffer, 1, history_state)
            continue
        if char >= " ":
            buffer += char
            _reset_history_navigation(history_state)
            print(char, end="", flush=True)


def _read_interactive_line_posix(prompt: str, history_entries: list[str]) -> str:
    import select
    import termios
    import tty

    buffer = ""
    history_state: dict[str, Any] = {"candidates": _history_candidates(history_entries), "index": None, "draft": ""}
    stdin = sys.stdin
    fd = stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    print(prompt, end="", flush=True)

    def read_escape_tail() -> str:
        ready, _, _ = select.select([stdin], [], [], 0.05)
        if not ready:
            return ""
        first = stdin.read(1)
        ready, _, _ = select.select([stdin], [], [], 0.02)
        second = stdin.read(1) if ready else ""
        return first + second

    try:
        tty.setraw(fd)
        while True:
            char = stdin.read(1)
            if char in {"\r", "\n"}:
                print("")
                return buffer
            if char == "\u0003":
                raise KeyboardInterrupt
            if char == "\t":
                buffer = _apply_tab_completion(prompt, buffer)
                _reset_history_navigation(history_state)
                continue
            if char in {"\x7f", "\b"}:
                if buffer:
                    buffer = buffer[:-1]
                    _reset_history_navigation(history_state)
                    _redraw_prompt(prompt, buffer)
                continue
            if char == "\u001b":
                next_chars = read_escape_tail()
                if next_chars == "[A":
                    buffer = _apply_history_navigation(prompt, buffer, -1, history_state)
                elif next_chars == "[B":
                    buffer = _apply_history_navigation(prompt, buffer, 1, history_state)
                continue
            if char >= " ":
                buffer += char
                _reset_history_navigation(history_state)
                print(char, end="", flush=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _format_session_option(meta: dict[str, Any], selected: bool) -> str:
    marker = ">" if selected else " "
    title = str(meta.get("title") or "(untitled)")
    return f"{marker} {meta.get('id')}  {title}  messages={meta.get('messageCount')}"


def _render_session_picker(sessions: list[dict[str, Any]], index: int) -> None:
    print("\033[2J\033[H", end="")
    print("Select a session with Up/Down, Enter to resume, Esc/Ctrl+C to cancel.\n")
    for i, meta in enumerate(sessions):
        print(_format_session_option(meta, i == index))


def _pick_session_windows(sessions: list[dict[str, Any]]) -> str | None:
    import msvcrt

    index = 0
    _render_session_picker(sessions, index)
    while True:
        char = msvcrt.getwch()
        if char in {"\r", "\n"}:
            print("")
            return str(sessions[index].get("id"))
        if char in {"\u001b", "\u0003"}:
            print("\nCanceled.")
            return None
        if char in {"\x00", "\xe0"}:
            key = msvcrt.getwch()
            if key == "H":
                index = max(0, index - 1)
            elif key == "P":
                index = min(len(sessions) - 1, index + 1)
            _render_session_picker(sessions, index)


def _pick_session_posix(sessions: list[dict[str, Any]]) -> str | None:
    import select
    import termios
    import tty

    index = 0
    stdin = sys.stdin
    fd = stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    _render_session_picker(sessions, index)

    def read_escape_tail() -> str:
        ready, _, _ = select.select([stdin], [], [], 0.05)
        if not ready:
            return ""
        first = stdin.read(1)
        ready, _, _ = select.select([stdin], [], [], 0.02)
        second = stdin.read(1) if ready else ""
        return first + second

    try:
        tty.setraw(fd)
        while True:
            char = stdin.read(1)
            if char in {"\r", "\n"}:
                print("")
                return str(sessions[index].get("id"))
            if char in {"\u001b", "\u0003"}:
                if char == "\u001b":
                    next_chars = read_escape_tail()
                    if next_chars == "[A":
                        index = max(0, index - 1)
                        _render_session_picker(sessions, index)
                        continue
                    if next_chars == "[B":
                        index = min(len(sessions) - 1, index + 1)
                        _render_session_picker(sessions, index)
                        continue
                print("\nCanceled.")
                return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


async def _pick_resume_session(cwd: str) -> str | None:
    sessions = await list_sessions(cwd)
    if not sessions:
        print("No saved sessions.")
        return None
    visible = sessions[:20]
    return _pick_session_windows(visible) if os.name == "nt" else _pick_session_posix(visible)


async def _resume_session(cwd: str, messages: list[dict[str, Any]], target: str, args: dict[str, Any]) -> str | None:
    loaded = await load_session(cwd, target)
    if not loaded:
        print("Session not found.")
        return None
    messages[:] = [messages[0], *loaded]
    restored_collapse = await load_context_collapse_state(cwd, target)
    if restored_collapse and args.get("contextCollapseState") is not None:
        args["contextCollapseState"].update(restored_collapse)
    print(f"Resumed session {target}.")
    return target


async def _view_session(cwd: str, session_id: str) -> None:
    entries = await load_transcript(cwd, session_id)
    if not entries:
        print("当前会话暂无可查看内容。")
        return
    print(f"\n[session {session_id}]\n")
    print("\n".join(render_transcript_lines(entries)))
    print("")


async def _refresh_runtime(args: dict[str, Any]) -> dict[str, Any]:
    getter = args.get("getRuntimeConfig")
    if getter is None:
        return args.get("runtime") or {}
    try:
        runtime = await getter()
    except Exception:
        runtime = args.get("runtime") or {}
    args["runtime"] = runtime
    return runtime


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
        resumed = await _resume_session(cwd, messages, str(resume_target), args)
        if resumed:
            session_id = resumed
    elif resume_target == "picker":
        target = await _pick_resume_session(cwd)
        if target:
            resumed = await _resume_session(cwd, messages, target, args)
            if resumed:
                session_id = resumed

    print(render_banner(args.get("runtime") or {}, cwd))
    print("输入 /help 查看中文命令说明，输入 /exit 退出。")

    while True:
        try:
            raw = _read_interactive_line(COLORED_PROMPT, history)
        except (EOFError, KeyboardInterrupt):
            print("")
            break
        input_text = raw.strip()
        if not input_text:
            continue
        if input_text == "/exit":
            break
        if not _is_sensitive_model_command(input_text):
            history.append(input_text)
        try:
            if input_text == "/new":
                await clear_session(cwd, session_id)
                messages[:] = [messages[0]]
                print("已开始新会话。")
                continue
            if input_text.startswith("/rename "):
                title = input_text[len("/rename "):].strip()
                if title:
                    ok = await rename_session(cwd, session_id, title)
                    print("已重命名。" if ok else "未找到会话。")
                continue
            if input_text == "/history":
                print(_format_history_entries(history))
                continue
            if input_text == "/clear":
                history.clear()
                clear_history_entries()
                print("已清空所有历史输入指令。")
                continue
            if input_text == "/resume":
                target = await _pick_resume_session(cwd)
                if target:
                    resumed = await _resume_session(cwd, messages, target, args)
                    if resumed:
                        session_id = resumed
                continue
            if input_text.startswith("/resume "):
                target = input_text[len("/resume "):].strip()
                resumed = await _resume_session(cwd, messages, target, args)
                if resumed:
                    session_id = resumed
                continue
            if input_text == "/view":
                await _view_session(cwd, session_id)
                continue
            if input_text == "/fork":
                forked = await fork_session(cwd, session_id)
                print(f"已分叉会话: {forked}" if forked else "当前没有可分叉内容。")
                continue
            if input_text == "/compact":
                result = await manual_compact(messages, args["model"])
                if not result:
                    print("暂无可压缩内容。")
                    continue
                messages[:] = result["messages"]
                await append_compact_boundary(cwd, session_id, result.get("summaryText") or "", "manual", result.get("preTokens") or 0, result.get("postTokens") or 0, result.get("retainedMessages") or [])
                print("上下文已压缩。")
                continue
            if input_text == "/collapse":
                runtime = args.get("runtime") or {}
                model_name = runtime.get("model") or ""
                if not model_name:
                    print("未配置模型，无法进行上下文折叠。")
                    continue
                state = args.get("contextCollapseState") or create_context_collapse_state()
                result = await apply_context_collapse_if_needed(messages, model_name, args["model"], state, {"utilizationThreshold": 0, "reason": "manual"})
                if args.get("contextCollapseState") is not None:
                    args["contextCollapseState"].update(result["state"])
                for span in result.get("spans") or []:
                    await append_context_collapse_span(cwd, session_id, span)
                print(f"已折叠 {len(result.get('spans') or [])} 个上下文片段。" if result.get("collapsed") else "暂无可安全折叠的上下文。")
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
                    print("已裁剪上下文。")
                else:
                    print("暂无可安全裁剪的上下文。")
                continue
            if input_text == "/background":
                tasks = list_background_tasks()
                print("\n".join(f"{t.get('taskId')} pid={t.get('pid')} status={t.get('status')} {t.get('command')}" for t in tasks) if tasks else "No background tasks.")
                continue
            local_result = await try_handle_local_command(input_text, {"tools": args["tools"]})
            if local_result is not None:
                if _is_model_config_command(input_text):
                    await _refresh_runtime(args)
                print(local_result)
                continue
            shortcut = parse_local_tool_shortcut(input_text)
            if shortcut:
                result = await args["tools"].execute(shortcut["toolName"], shortcut.get("input"), {"cwd": cwd, "permissions": permissions})
                print(_render_shortcut_output(shortcut, result.get("output")))
                continue
            if input_text.startswith("/"):
                matches = find_matching_slash_commands(input_text)
                print("未识别命令。" + ("你是不是想输入：\n" + "\n".join(matches) if matches else "输入 /help 查看可用命令。"))
                continue

            await _refresh_system_prompt(args)
            runtime = await _refresh_runtime(args)
            messages.append({"role": "user", "content": input_text})
            permissions.begin_turn()
            stream_printer = MarkdownStreamPrinter()
            interrupted = False
            try:
                messages[:] = await run_agent_turn({
                    "model": args["model"],
                    "tools": args["tools"],
                    "messages": messages,
                    "cwd": cwd,
                    "permissions": permissions,
                    "modelName": (runtime or {}).get("model") or "",
                    "contentReplacementState": args.get("contentReplacementState"),
                    "contextCollapseState": args.get("contextCollapseState"),
                    "onToolStart": _render_tool_start,
                    "onToolResult": _render_tool_result,
                    "onAssistantDelta": stream_printer.write,
                    "onAssistantMessage": lambda content: print(f"\n{_render_assistant_output(content)}\n"),
                    "onProgressMessage": lambda content: print(_render_progress_node(content)),
                })
            except KeyboardInterrupt:
                interrupted = True
                messages.append({"role": "assistant", "content": INTERRUPTED_MESSAGE})
            finally:
                stream_printer.finish()
                if interrupted:
                    print(f"\n{INTERRUPTED_MESSAGE}\n")
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
