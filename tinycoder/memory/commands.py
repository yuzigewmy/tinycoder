from __future__ import annotations

from typing import TYPE_CHECKING

from .models import VALID_KINDS, VALID_SCOPES

if TYPE_CHECKING:
    from .service import MemoryService


def _format_item(item: object) -> str:
    data = vars(item)
    return (
        f"id={data['id']} scope={data['scope']} kind={data['kind']} "
        f"status={data['status']} confidence={data['confidence']:.2f} "
        f"key={data['canonical_key']}\n{data['content']}"
    )


def _usage() -> str:
    return "\n".join(
        [
            "Memory commands:",
            "  /memory status",
            "  /memory list [scope]",
            "  /memory show <id>",
            "  /memory add <scope> <kind> <key>::<content>",
            "  /memory forget <id>",
            "  /memory conflicts",
            "  /memory mode <off|read_only|suggest|auto>",
        ]
    )


def handle_memory_command(command: str, service: MemoryService | None) -> str:
    if service is None:
        return "memory service is unavailable"
    text = command.strip()
    if text in {"/memory", "/memory help"}:
        return _usage()
    if text == "/memory status":
        status = service.status()
        return "\n".join(f"{key}: {value}" for key, value in status.items())
    if text == "/memory list" or text.startswith("/memory list "):
        scope = text[len("/memory list") :].strip() or None
        if scope and scope not in VALID_SCOPES:
            return f"invalid memory scope: {scope}"
        items = service.list(scope=scope)
        return "\n\n".join(_format_item(item) for item in items) or "no memories"
    if text.startswith("/memory show "):
        memory_id = text[len("/memory show ") :].strip()
        item = service.get(memory_id)
        return _format_item(item) if item else f"memory not found: {memory_id}"
    if text.startswith("/memory forget "):
        memory_id = text[len("/memory forget ") :].strip()
        try:
            deleted = service.forget(memory_id)
        except (RuntimeError, ValueError) as error:
            return f"memory forget rejected: {error}"
        return f"forgotten id={memory_id}" if deleted else f"memory not found: {memory_id}"
    if text == "/memory conflicts":
        items = [item for item in service.list(limit=200) if item.status == "disputed"]
        return "\n\n".join(_format_item(item) for item in items) or "no memory conflicts"
    if text.startswith("/memory add "):
        payload = text[len("/memory add ") :].strip()
        header, separator, content = payload.partition("::")
        parts = header.split(maxsplit=2)
        if not separator or len(parts) != 3:
            return "usage: /memory add <scope> <kind> <key>::<content>"
        scope, kind, canonical_key = parts
        if scope not in VALID_SCOPES:
            return f"invalid memory scope: {scope}"
        if kind not in VALID_KINDS:
            return f"invalid memory kind: {kind}"
        try:
            item, outcome = service.add(
                scope=scope,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                canonical_key=canonical_key,
                content=content,
                confidence=0.98,
                status="active",
                source_uri="explicit:/memory",
            )
        except (RuntimeError, ValueError) as error:
            return f"memory add rejected: {error}"
        return f"stored id={item.id} action={outcome.action} key={item.canonical_key}"
    return _usage()
