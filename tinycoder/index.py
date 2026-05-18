from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any

from .agent_loop import run_agent_turn
from .anthropic_adapter import AnthropicModelAdapter
from .cli_commands import complete_slash_command, find_matching_slash_commands, try_handle_local_command
from .compact.context_collapse import apply_context_collapse_if_needed, create_context_collapse_state
from .config import load_runtime_config
from .manage_cli import maybe_handle_management_command
from .mcp_status import summarize_mcp_servers
from .mock_model import MockModelAdapter
from .permissions import PermissionManager
from .prompt import build_system_prompt
from .session import fork_session
from .tools.index import create_default_tool_registry, hydrate_mcp_tools
from .tty_app import run_tty_app
from .ui import render_banner
from .utils.tool_result_storage import create_content_replacement_state


async def main(argv: list[str] | None = None) -> None:
    cwd = os.getcwd()
    argv = list(sys.argv[1:] if argv is None else argv)

    resume_target: str | None = None
    if "--resume" in argv:
        index = argv.index("--resume")
        del argv[index]
        if index < len(argv) and not argv[index].startswith("-"):
            resume_target = argv[index]
            del argv[index]
        else:
            resume_target = "picker"

    fork_target: str | None = None
    if "--fork" in argv:
        index = argv.index("--fork")
        del argv[index]
        if index < len(argv) and not argv[index].startswith("-"):
            fork_target = argv[index]
            del argv[index]

    if await maybe_handle_management_command(cwd, argv):
        return

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    try:
        runtime = await load_runtime_config()
    except Exception:
        runtime = None

    tools = await create_default_tool_registry({"cwd": cwd, "runtime": runtime})
    try:
        await hydrate_mcp_tools({"cwd": cwd, "runtime": runtime, "tools": tools})
    except Exception:
        pass

    permissions = PermissionManager(cwd)
    await permissions.when_ready()
    model = MockModelAdapter() if os.environ.get("TINYCODER_MODEL_MODE") == "mock" or runtime is None else AnthropicModelAdapter(tools, load_runtime_config)
    messages: list[dict[str, Any]] = [{"role": "system", "content": await build_system_prompt(cwd, permissions.get_summary(), {"skills": tools.get_skills(), "mcpServers": tools.get_mcp_servers()})}]
    content_replacement_state = create_content_replacement_state()
    context_collapse_state = create_context_collapse_state()

    async def refresh_system_prompt() -> None:
        messages[0] = {"role": "system", "content": await build_system_prompt(cwd, permissions.get_summary(), {"skills": tools.get_skills(), "mcpServers": tools.get_mcp_servers()})}

    try:
        if interactive:
            session_id = str(uuid.uuid4())[:8]
            resolved_resume = resume_target
            if fork_target:
                forked_id = await fork_session(cwd, fork_target)
                if forked_id:
                    session_id = forked_id
                    resolved_resume = forked_id
                else:
                    print(f"Session {fork_target} not found or empty.", file=sys.stderr)
            await run_tty_app({
                "runtime": runtime,
                "tools": tools,
                "model": model,
                "messages": messages,
                "cwd": cwd,
                "permissions": permissions,
                "contentReplacementState": content_replacement_state,
                "contextCollapseState": context_collapse_state,
                "sessionId": session_id,
                "alreadySavedCount": 0,
                "resumeTarget": resolved_resume,
            })
            return

        mcp_status = summarize_mcp_servers(tools.get_mcp_servers())
        print(render_banner(runtime or {"model": "mock"}, cwd))
        print("")
        for raw_input in sys.stdin:
            input_text = raw_input.strip()
            if not input_text:
                continue
            if input_text == "/exit":
                break
            try:
                if input_text == "/collapse":
                    model_name = (runtime or {}).get("model") or ""
                    if not model_name:
                        print("\nNo model configured. Cannot collapse context.\n")
                        continue
                    result = await apply_context_collapse_if_needed(messages, model_name, model, context_collapse_state, {"utilizationThreshold": 0, "reason": "manual"})
                    context_collapse_state.update(result["state"])
                    if not result.get("collapsed"):
                        print("\nNothing safe to collapse.\n" if result["state"].get("enabled", True) else "\nContext collapse is disabled after repeated summary failures.\n")
                        continue
                    saved = sum(max(0, span.get("tokensBefore", 0) - span.get("tokensAfter", 0)) for span in result.get("spans") or [])
                    print(f"\nContext collapse projected {len(result.get('spans') or [])} span(s) into summaries, saving ~{round(saved)} tokens. Original transcript is preserved.\n")
                    continue
                local_command_result = await try_handle_local_command(input_text, {"tools": tools})
                if local_command_result is not None:
                    print(f"\n{local_command_result}\n")
                    continue
                if input_text.startswith("/"):
                    matches = find_matching_slash_commands(input_text)
                    print("\n未识别命令。" + ("你是不是想输入：\n" + "\n".join(matches) if matches else "输入 /help 查看可用命令。") + "\n")
                    continue
            except Exception as error:
                print(f"\n{error}\n")
                continue

            await refresh_system_prompt()
            messages.append({"role": "user", "content": input_text})
            permissions.begin_turn()
            try:
                messages[:] = await run_agent_turn({
                    "model": model,
                    "tools": tools,
                    "messages": messages,
                    "cwd": cwd,
                    "permissions": permissions,
                    "modelName": (runtime or {}).get("model") or "",
                    "contentReplacementState": content_replacement_state,
                    "contextCollapseState": context_collapse_state,
                })
            except Exception as error:
                messages.append({"role": "assistant", "content": f"请求失败: {error}"})
            finally:
                permissions.end_turn()
            last = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
            if last:
                print(f"\n{last.get('content')}\n")
    finally:
        await tools.dispose()


def main_sync(argv: list[str] | None = None) -> None:
    try:
        asyncio.run(main(argv))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main_sync()
