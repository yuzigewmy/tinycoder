from __future__ import annotations
from typing import Any
from .compact import compact_conversation
from .auto_compact import reset_auto_compact_state

async def manual_compact(messages: list[dict[str, Any]], model_adapter: Any) -> dict[str, Any] | None:
    result = await compact_conversation(messages, model_adapter)
    if result:
        reset_auto_compact_state()
    return result

manualCompact = manual_compact
