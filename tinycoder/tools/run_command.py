from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Any

from ..background_tasks import register_background_shell_task
from ..tool import ToolDefinition
from ..workspace import resolve_tool_path

READONLY_COMMANDS = {"pwd", "ls", "find", "rg", "grep", "cat", "head", "tail", "wc", "sed", "echo", "df", "du", "free", "uname", "uptime", "whoami"}
DEVELOPMENT_COMMANDS = {"git", "npm", "node", "python3", "python", "pytest", "bash", "sh", "bun"}
MAX_OUTPUT_BYTES = 1024 * 1024


def is_allowed_command(command: str) -> bool:
    return command in READONLY_COMMANDS or command in DEVELOPMENT_COMMANDS


def is_read_only_command(command: str) -> bool:
    return command in READONLY_COMMANDS


def split_command_line(command_line: str) -> list[str]:
    try:
        return shlex.split(command_line)
    except ValueError:
        return command_line.split()


def normalize_command_input(input_value: dict[str, Any]) -> dict[str, Any]:
    args = input_value.get("args") or []
    if args:
        return {"command": str(input_value.get("command") or "").strip(), "args": [str(arg) for arg in args]}
    trimmed = str(input_value.get("command") or "").strip()
    if not trimmed:
        return {"command": "", "args": []}
    parsed = split_command_line(trimmed)
    return {"command": parsed[0] if parsed else "", "args": parsed[1:] if len(parsed) > 1 else []}


def looks_like_shell_snippet(command: str, args: list[str] | None = None) -> bool:
    if args:
        return False
    return re.search(r"[|&;<>()$`]", command) is not None


def is_background_shell_snippet(command: str, args: list[str] | None = None) -> bool:
    if args:
        return False
    trimmed = command.strip()
    return trimmed.endswith("&") and not trimmed.endswith("&&")


def strip_trailing_background_operator(command: str) -> str:
    return re.sub(r"&\s*$", "", command.strip()).strip()


def _validate(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict) or not isinstance(input_value.get("command"), str):
        raise ValueError("command must be a string")
    args = input_value.get("args")
    if args is not None and (not isinstance(args, list) or not all(isinstance(a, str) for a in args)):
        raise ValueError("args must be an array of strings")
    cwd = input_value.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError("cwd must be a string")
    return {"command": input_value["command"], "args": args or [], "cwd": cwd}


async def _run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    effective_cwd = await resolve_tool_path(context, input_value["cwd"], "list") if input_value.get("cwd") else str(context.get("cwd") or os.getcwd())
    normalized = normalize_command_input(input_value)
    if not normalized["command"]:
        return {"ok": False, "output": "Command not allowed: empty command"}

    use_shell = looks_like_shell_snippet(input_value["command"], input_value.get("args"))
    background_shell = is_background_shell_snippet(input_value["command"], input_value.get("args"))
    known_command = is_allowed_command(normalized["command"])

    command = "bash" if use_shell else normalized["command"]
    args = ["-lc", strip_trailing_background_operator(input_value["command"]) if background_shell else input_value["command"]] if use_shell else list(normalized["args"])

    permissions = context.get("permissions")
    force_reason = None if use_shell or known_command else f"Unknown command '{normalized['command']}' is not in the built-in read-only/development set"
    if permissions is not None:
        if force_reason:
            await permissions.ensure_command(command, args, effective_cwd, {"forcePromptReason": force_reason})
        elif use_shell or not is_read_only_command(normalized["command"]):
            await permissions.ensure_command(command, args, effective_cwd)

    if use_shell and background_shell:
        child = subprocess.Popen([command, *args], cwd=effective_cwd, env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        background_task = register_background_shell_task({"command": strip_trailing_background_operator(input_value["command"]), "pid": child.pid, "cwd": effective_cwd})
        return {"ok": True, "output": f"Background command started.\nTASK: {background_task['taskId']}\nPID: {background_task['pid']}", "backgroundTask": background_task}

    try:
        proc = subprocess.run([command, *args], cwd=effective_cwd, env=os.environ.copy(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except FileNotFoundError:
        return {"ok": False, "output": f"Command not found: {command}"}
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        return {"ok": False, "output": (stdout + "\n" + stderr + "\nCommand timed out after 120 seconds").strip()}

    output = "\n".join(part for part in [proc.stdout, proc.stderr] if part).strip()
    if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        output = output.encode("utf-8")[:MAX_OUTPUT_BYTES].decode("utf-8", "ignore") + "\n...[truncated]"
    return {"ok": proc.returncode == 0, "output": output, "exitCode": proc.returncode}


run_command_tool = ToolDefinition(
    name="run_command",
    description="Run a common development command from an allowlist. For shell pipelines or variable expansion, pass the full snippet in command and tinycoder will run it via bash -lc.",
    input_schema={"type": "object", "properties": {"command": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}, "cwd": {"type": "string"}}, "required": ["command"]},
    validator=_validate,
    run=_run,
)

runCommandTool = run_command_tool
splitCommandLine = split_command_line
normalizeCommandInput = normalize_command_input
looksLikeShellSnippet = looks_like_shell_snippet
isBackgroundShellSnippet = is_background_shell_snippet
stripTrailingBackgroundOperator = strip_trailing_background_operator
