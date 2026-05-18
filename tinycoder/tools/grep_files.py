from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..tool import ToolDefinition
from ..workspace import resolve_tool_path


def _validate(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict) or not isinstance(input_value.get("pattern"), str) or not input_value["pattern"]:
        raise ValueError("pattern must be a non-empty string")
    path = input_value.get("path")
    if path is not None and not isinstance(path, str):
        raise ValueError("path must be a string")
    return {"pattern": input_value["pattern"], "path": path}


def _python_grep(root: Path, pattern: str) -> str:
    lines: list[str] = []
    targets: list[Path]
    if root.is_file():
        targets = [root]
    else:
        targets = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    for file_path in targets:
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            if pattern in line:
                try:
                    label = str(file_path.relative_to(root if root.is_dir() else root.parent))
                except ValueError:
                    label = str(file_path)
                lines.append(f"{label}:{line_no}:{line}")
                if len(lines) >= 1000:
                    return "\n".join(lines)
    return "\n".join(lines)


async def _run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    cwd = str(context.get("cwd") or os.getcwd())
    if input_value.get("path"):
        target = await resolve_tool_path(context, input_value["path"], "search")
        rg_target = target
        fallback_root = Path(target)
    else:
        rg_target = "."
        fallback_root = Path(cwd)
    if shutil.which("rg"):
        proc = subprocess.run(["rg", "-n", "--no-heading", input_value["pattern"], rg_target], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        output = (proc.stdout or proc.stderr or "").strip()
        if proc.returncode in (0, 1):
            return {"ok": True, "output": output or "(no matches)"}
        return {"ok": False, "output": output or f"rg exited with code {proc.returncode}"}
    output = _python_grep(fallback_root, input_value["pattern"]).strip()
    return {"ok": True, "output": output or "(no matches)"}


grep_files_tool = ToolDefinition(
    name="grep_files",
    description="Search for text in files using ripgrep, falling back to a Python scan when ripgrep is unavailable.",
    input_schema={"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]},
    validator=_validate,
    run=_run,
)

grepFilesTool = grep_files_tool
