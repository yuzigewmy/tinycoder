from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import TINYCODER_DIR, TINYCODER_HISTORY_PATH

MAX_ENTRIES = 500


def load_history_entries() -> list[str]:
    try:
        raw = Path(TINYCODER_HISTORY_PATH).read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        display = entry.get("display") if isinstance(entry, dict) else None
        if isinstance(display, str):
            entries.append(display)
    return entries


async def load_history_entries_async() -> list[str]:
    return load_history_entries()


def save_history_entries(entries: list[str], cwd: str, session_id: str) -> None:
    Path(TINYCODER_DIR).mkdir(parents=True, exist_ok=True)
    existing = set(load_history_entries())
    new_entries = [entry for entry in entries if entry not in existing]
    if not new_entries:
        return
    now = int(time.time() * 1000)
    with Path(TINYCODER_HISTORY_PATH).open("a", encoding="utf-8") as handle:
        for display in new_entries:
            handle.write(json.dumps({"display": display, "timestamp": now, "project": cwd, "sessionId": session_id}, ensure_ascii=False) + "\n")
    try:
        lines = [line for line in Path(TINYCODER_HISTORY_PATH).read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) > MAX_ENTRIES:
            Path(TINYCODER_HISTORY_PATH).write_text("\n".join(lines[-MAX_ENTRIES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def clear_history_entries() -> None:
    try:
        Path(TINYCODER_HISTORY_PATH).unlink()
    except FileNotFoundError:
        pass


async def save_history_entries_async(entries: list[str], cwd: str, session_id: str) -> None:
    save_history_entries(entries, cwd, session_id)


async def clear_history_entries_async() -> None:
    clear_history_entries()


# TypeScript-compatible aliases.
loadHistoryEntries = load_history_entries_async
saveHistoryEntries = save_history_entries_async
clearHistoryEntries = clear_history_entries_async
