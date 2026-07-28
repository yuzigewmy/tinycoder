from __future__ import annotations

from typing import Any


def inject_context_message(
    messages: list[dict[str, Any]],
    content: str,
    *,
    context_kind: str,
) -> list[dict[str, Any]]:
    projected = [
        dict(message)
        for message in messages
        if not (
            message.get("synthetic")
            and message.get("contextKind") == context_kind
        )
    ]
    content = str(content or "").strip()
    if not content:
        return projected
    context_message = {
        "role": "user",
        "content": content,
        "synthetic": True,
        "contextKind": context_kind,
    }
    latest_user = next(
        (index for index in range(len(projected) - 1, -1, -1) if projected[index].get("role") == "user"),
        len(projected),
    )
    projected.insert(latest_user, context_message)
    return projected


def inject_memory_context(
    messages: list[dict[str, Any]],
    memory_context: str,
) -> list[dict[str, Any]]:
    return inject_context_message(
        messages,
        memory_context,
        context_kind="memory",
    )
