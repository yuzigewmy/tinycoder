from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

PathIntent = Literal["read", "write", "list", "search"]


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


async def resolve_tool_path(context: dict[str, Any], target_path: str, intent: PathIntent) -> str:
    cwd = Path(context.get("cwd") or ".").resolve()
    resolved = (cwd / target_path).resolve()
    permissions = context.get("permissions")
    if permissions is None:
        if not _is_relative_to(resolved, cwd):
            raise RuntimeError(f"Path escapes workspace: {target_path}")
        return str(resolved)
    await permissions.ensure_path_access(str(resolved), intent)
    return str(resolved)

# TS-style alias
resolveToolPath = resolve_tool_path
