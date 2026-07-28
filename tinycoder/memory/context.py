from __future__ import annotations

from typing import Any


def inject_memory_context(
    messages: list[dict[str, Any]],
    memory_context: str,
) -> list[dict[str, Any]]:
    projected = [
        dict(message)
        for message in messages
        if not (message.get("synthetic") and message.get("contextKind") == "memory")
    ]
    content = str(memory_context or "").strip()
    if not content:
        return projected
    memory_message = {
        "role": "user",
        "content": content,
        "synthetic": True,
        "contextKind": "memory",
    }
    latest_user = next(
        (index for index in range(len(projected) - 1, -1, -1) if projected[index].get("role") == "user"),
        len(projected),
    )
    projected.insert(latest_user, memory_message)
    return projected
