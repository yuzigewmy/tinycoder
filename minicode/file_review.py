from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any


def build_unified_diff(file_path: str, before: str, after: str) -> str:
    if before == after:
        return f"(no changes for {file_path})"
    lines = difflib.unified_diff(
        before.splitlines(True),
        after.splitlines(True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=3,
    )
    return "".join(lines)


async def load_existing_file(target_path: str) -> str:
    try:
        return Path(target_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


async def apply_reviewed_file_change(context: dict[str, Any], file_path: str, target_path: str, next_content: str) -> dict[str, Any]:
    previous = await load_existing_file(target_path)
    if previous == next_content:
        return {"ok": True, "output": f"No changes needed for {file_path}"}
    diff = build_unified_diff(file_path, previous, next_content)
    permissions = context.get("permissions")
    if permissions is not None:
        await permissions.ensure_edit(target_path, diff)
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(next_content, encoding="utf-8")
    return {"ok": True, "output": f"Applied reviewed changes to {file_path}"}

# TS-style aliases
buildUnifiedDiff = build_unified_diff
loadExistingFile = load_existing_file
applyReviewedFileChange = apply_reviewed_file_change
