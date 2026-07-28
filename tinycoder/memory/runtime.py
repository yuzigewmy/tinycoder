from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ..config import TINYCODER_MEMORY_DB_PATH
from .service import MemoryCaptureReport, MemoryService
from .settings import MemorySettings


def create_memory_service(
    cwd: str | Path,
    effective_settings: dict[str, Any] | None,
    *,
    store_path: str | Path | None = None,
) -> MemoryService:
    effective_settings = effective_settings or {}
    settings = MemorySettings.from_mapping(effective_settings.get("memory"))
    return MemoryService(
        cwd,
        store_path=store_path or TINYCODER_MEMORY_DB_PATH,
        settings=settings,
    )


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user" or message.get("synthetic"):
            continue
        content = str(message.get("content") or "").strip()
        if content:
            return content
    return ""


def latest_user_event_id(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "user" or message.get("synthetic"):
            continue
        event_id = str(message.get("eventId") or "").strip()
        if event_id:
            return event_id
    return None


def active_paths_from_messages(
    messages: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        path = str(value or "").strip().replace("\\", "/")
        if not path or "://" in path or path in seen:
            return
        seen.add(path)
        paths.append(path)

    for message in reversed(messages[-50:]):
        if message.get("role") == "assistant_tool_call":
            input_value = message.get("input")
            if isinstance(input_value, dict):
                for key in ("path", "filePath"):
                    if key in input_value:
                        add(input_value[key])
        elif message.get("role") == "user" and not message.get("synthetic"):
            content = str(message.get("content") or "")
            for match in re.findall(
                r"(?<![:\w])(?:[\w.-]+/)+[\w.-]+|(?<![\w])[\w.-]+\.[A-Za-z0-9]{1,8}\b",
                content,
            ):
                add(match)
        if len(paths) >= limit:
            break
    return paths[:limit]


def _recent_text(messages: list[dict[str, Any]], *, limit: int = 6) -> list[str]:
    selected: list[str] = []
    for message in reversed(messages):
        if message.get("synthetic") or message.get("role") not in {"user", "assistant"}:
            continue
        content = str(message.get("content") or "").strip()
        if content:
            selected.append(content[:2_000])
        if len(selected) >= limit:
            break
    return list(reversed(selected))


def create_memory_context_provider(
    service: MemoryService | None,
    *,
    session_id: str | None,
) -> Callable[[list[dict[str, Any]]], str]:
    def provide(messages: list[dict[str, Any]]) -> str:
        if service is None:
            return ""
        user_text = latest_user_text(messages)
        if not user_text:
            return ""
        recall = service.recall(
            user_text,
            session_id=session_id,
            recent_messages=_recent_text(messages),
            active_paths=active_paths_from_messages(messages),
        )
        return service.render_context(recall)

    return provide


def capture_memory_turn(
    service: MemoryService | None,
    messages: list[dict[str, Any]],
    *,
    session_id: str | None,
    event_id: str | None = None,
) -> MemoryCaptureReport | None:
    if service is None:
        return None
    try:
        return service.capture_turn(
            messages,
            session_id=session_id,
            event_id=event_id or latest_user_event_id(messages),
        )
    except Exception:
        # Memory is an optional enhancement and must never break the agent turn.
        return None
