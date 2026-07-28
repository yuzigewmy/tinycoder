from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..config import TINYCODER_MEMORY_DB_PATH
from .service import MemoryService
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
        )
        return service.render_context(recall)

    return provide
