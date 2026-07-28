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
            "  /memory resolve <winner-id>",
            "  /memory pending",
            "  /memory approve <id>",
            "  /memory reject <id>",
            "  /memory stale <id>",
            "  /memory history <id>",
            "  /memory export",
            "  /memory import <json>",
            "  /memory graph",
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
    if text.startswith("/memory resolve "):
        memory_id = text[len("/memory resolve ") :].strip()
        try:
            resolved = service.resolve_conflict(memory_id)
        except (RuntimeError, ValueError) as error:
            return f"memory conflict resolution rejected: {error}"
        return (
            f"resolved conflict winner={memory_id} superseded={resolved}"
            if resolved
            else f"memory conflict not found: {memory_id}"
        )
    if text == "/memory pending":
        items = [item for item in service.list(limit=200) if item.status == "pending_review"]
        return "\n\n".join(_format_item(item) for item in items) or "no pending memories"
    for command, status, reason, verb in (
        ("/memory approve ", "active", "user approved", "approved"),
        ("/memory reject ", "quarantined", "user rejected", "rejected"),
        ("/memory stale ", "stale", "user marked stale", "marked stale"),
    ):
        if text.startswith(command):
            memory_id = text[len(command) :].strip()
            try:
                changed = service.set_status(memory_id, status, reason=reason)
            except (RuntimeError, ValueError) as error:
                return f"memory update rejected: {error}"
            return f"{verb} id={memory_id}" if changed else f"memory not found: {memory_id}"
    if text.startswith("/memory history "):
        memory_id = text[len("/memory history ") :].strip()
        history = service.history(memory_id)
        if not history:
            return f"memory not found or has no history: {memory_id}"
        return "\n".join(
            (
                f"{entry['createdAt']} operation={entry['operation']} "
                f"reason={entry.get('reason') or '-'}"
            )
            for entry in history
        )
    if text == "/memory export":
        return service.export_json()
    if text.startswith("/memory import "):
        payload = text[len("/memory import ") :].strip()
        try:
            count = service.import_json(payload)
        except (RuntimeError, ValueError) as error:
            return f"memory import rejected: {error}"
        return f"imported {count} memories"
    if text == "/memory graph":
        graph = service.project_graph()
        lines = [
            f"entities={len(graph['entities'])} edges={len(graph['edges'])}",
            *[
                (
                    f"{edge['source']} -[{edge['relation']}]-> "
                    f"{edge['target']} memory={edge.get('memoryId') or '-'}"
                )
                for edge in graph["edges"]
            ],
        ]
        return "\n".join(lines)
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
