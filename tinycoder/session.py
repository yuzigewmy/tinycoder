from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .config import TINYCODER_PROJECTS_DIR
from .compact.context_collapse import create_context_collapse_state

MAX_TITLE_LENGTH = 60


def project_dir_name(cwd: str) -> str:
    name = "".join("-" if ch in "/\\:" else ch for ch in cwd)
    return name.lstrip("-") or "root"


def project_dir(cwd: str) -> Path:
    return Path(TINYCODER_PROJECTS_DIR) / project_dir_name(cwd)


def session_file_path(cwd: str, session_id: str) -> Path:
    return project_dir(cwd) / f"{session_id}.jsonl"


def role_to_type(role: str) -> str:
    return {
        "system": "system",
        "user": "user",
        "assistant": "assistant",
        "assistant_thinking": "thinking",
        "assistant_progress": "progress",
        "assistant_tool_call": "tool_call",
        "tool_result": "tool_result",
        "context_summary": "summary",
        "snip_boundary": "snip_boundary",
    }.get(role, "user")


def ensure_message_id(message: dict[str, Any]) -> str:
    if message.get("id"):
        return str(message["id"])
    message["id"] = str(uuid.uuid4())
    return message["id"]


def wrap_event(message: dict[str, Any], session_id: str, cwd: str, parent_uuid: str | None) -> str:
    event_uuid = ensure_message_id(message)
    event: dict[str, Any] = {
        "type": role_to_type(str(message.get("role") or "user")),
        "message": message,
        "uuid": event_uuid,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time()*1000)%1000:03d}Z",
        "sessionId": session_id,
        "cwd": cwd,
        "parentUuid": parent_uuid,
    }
    if message.get("role") == "snip_boundary":
        event["snipMetadata"] = {
            "type": "snip_boundary",
            "removedMessageIds": message.get("removedMessageIds") or [],
            "removedCount": message.get("removedCount") or 0,
            "tokensFreed": message.get("tokensFreed") or 0,
            "timestamp": event["timestamp"],
            "createdAt": event["timestamp"],
        }
    return json.dumps(event, ensure_ascii=False)


def parse_event(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def unwrap_message(event: dict[str, Any]) -> dict[str, Any] | None:
    message = event.get("message")
    if isinstance(message, dict):
        next_message = dict(message)
        next_message["id"] = event.get("uuid")
        return next_message
    return None


def reconstruct_snipped_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snips = [event for event in events if event.get("type") == "snip_boundary" and event.get("snipMetadata", {}).get("removedMessageIds")]
    if not snips:
        return events
    removed_to_snips: dict[str, list[dict[str, Any]]] = {}
    for snip in snips:
        for removed_id in snip.get("snipMetadata", {}).get("removedMessageIds") or []:
            removed_to_snips.setdefault(str(removed_id), []).append(snip)
    inserted: set[str] = set()
    result: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") == "snip_boundary":
            continue
        matching = removed_to_snips.get(str(event.get("uuid")), [])
        if matching:
            for snip in matching:
                if str(snip.get("uuid")) not in inserted:
                    result.append(snip)
                    inserted.add(str(snip.get("uuid")))
            continue
        result.append(event)
    return result


def extract_title_from_events(lines: list[str]) -> str | None:
    rename_title = None
    for line in lines:
        event = parse_event(line)
        if event and event.get("type") == "rename" and isinstance(event.get("title"), str):
            rename_title = event["title"]
    if rename_title:
        return rename_title
    for line in lines:
        event = parse_event(line)
        if not event or event.get("type") != "user":
            continue
        message = event.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            text = content.strip()
            return text[:MAX_TITLE_LENGTH] + "..." if len(text) > MAX_TITLE_LENGTH else text
    return None


def _read_lines(path: Path) -> list[str]:
    try:
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def read_last_event_uuid(file_path: Path) -> str | None:
    lines = _read_lines(file_path)
    if not lines:
        return None
    event = parse_event(lines[-1])
    return str(event.get("uuid")) if event and event.get("uuid") else None


def read_existing_event_uuids(file_path: Path) -> set[str]:
    ids: set[str] = set()
    for line in _read_lines(file_path):
        event = parse_event(line)
        if event and event.get("uuid"):
            ids.add(str(event["uuid"]))
    return ids


async def save_session(cwd: str, session_id: str, messages: list[dict[str, Any]], already_saved_count: int = 0) -> None:
    directory = project_dir(cwd)
    file_path = session_file_path(cwd, session_id)
    directory.mkdir(parents=True, exist_ok=True)
    existing_ids = read_existing_event_uuids(file_path)
    non_system = messages[1:] if messages and messages[0].get("role") == "system" else messages
    to_save = []
    for index, message in enumerate(non_system):
        if message.get("id") and str(message["id"]) in existing_ids:
            continue
        if message.get("id") or index >= already_saved_count:
            to_save.append(message)
    if not to_save:
        return
    parent_uuid = read_last_event_uuid(file_path)
    lines: list[str] = []
    for message in to_save:
        line = wrap_event(message, session_id, cwd, parent_uuid)
        parsed = json.loads(line)
        parent_uuid = parsed.get("uuid")
        lines.append(line)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


async def append_snip_boundary(cwd: str, session_id: str, boundary_message: dict[str, Any]) -> None:
    directory = project_dir(cwd); directory.mkdir(parents=True, exist_ok=True)
    file_path = session_file_path(cwd, session_id)
    last_uuid = read_last_event_uuid(file_path)
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time()*1000)%1000:03d}Z"
    event_uuid = ensure_message_id(boundary_message)
    event = {
        "type": "snip_boundary",
        "subtype": "snip_boundary",
        "message": boundary_message,
        "uuid": event_uuid,
        "timestamp": now,
        "sessionId": session_id,
        "cwd": cwd,
        "parentUuid": None,
        "logicalParentUuid": last_uuid,
        "snipMetadata": {
            "type": "snip_boundary",
            "removedMessageIds": boundary_message.get("removedMessageIds") or [],
            "removedCount": boundary_message.get("removedCount") or 0,
            "tokensFreed": boundary_message.get("tokensFreed") or 0,
            "timestamp": now,
            "createdAt": now,
        },
    }
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


async def append_context_collapse_span(cwd: str, session_id: str, span: dict[str, Any]) -> None:
    directory = project_dir(cwd); directory.mkdir(parents=True, exist_ok=True)
    file_path = session_file_path(cwd, session_id)
    last_uuid = read_last_event_uuid(file_path)
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time()*1000)%1000:03d}Z"
    event = {"type": "context_collapse", "subtype": "context_collapse", "uuid": span.get("id") or str(uuid.uuid4()), "timestamp": now, "sessionId": session_id, "cwd": cwd, "parentUuid": None, "logicalParentUuid": last_uuid, "contextCollapseSpan": span}
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


async def append_compact_boundary(cwd: str, session_id: str, summary_text: str, trigger: str, pre_tokens: int, post_tokens: int, retained_messages: list[dict[str, Any]] | None = None) -> None:
    retained_messages = retained_messages or []
    directory = project_dir(cwd); directory.mkdir(parents=True, exist_ok=True)
    file_path = session_file_path(cwd, session_id)
    last_uuid = read_last_event_uuid(file_path)
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time()*1000)%1000:03d}Z"
    boundary_uuid = str(uuid.uuid4())
    summary_uuid = str(uuid.uuid4())
    boundary = {"type": "compact_boundary", "subtype": "compact_boundary", "uuid": boundary_uuid, "timestamp": now, "sessionId": session_id, "cwd": cwd, "parentUuid": None, "logicalParentUuid": last_uuid, "compactMetadata": {"trigger": trigger, "preTokens": pre_tokens, "postTokens": post_tokens}}
    summary = {"type": "user", "message": {"role": "user", "content": summary_text}, "uuid": summary_uuid, "timestamp": now, "sessionId": session_id, "cwd": cwd, "parentUuid": boundary_uuid}
    lines = [json.dumps(boundary, ensure_ascii=False), json.dumps(summary, ensure_ascii=False)]
    parent_uuid = summary_uuid
    for message in retained_messages:
        line = wrap_event(message, session_id, cwd, parent_uuid)
        parent_uuid = json.loads(line).get("uuid")
        lines.append(line)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


async def load_session(cwd: str, session_id: str) -> list[dict[str, Any]] | None:
    lines = _read_lines(session_file_path(cwd, session_id))
    if not lines:
        return None
    last_boundary = -1
    for i in range(len(lines) - 1, -1, -1):
        event = parse_event(lines[i])
        if event and event.get("type") == "compact_boundary":
            last_boundary = i
            break
    events = [event for line in lines[last_boundary + 1:] if (event := parse_event(line))]
    messages = [msg for event in reconstruct_snipped_events(events) if (msg := unwrap_message(event))]
    return messages or None


async def load_context_collapse_state(cwd: str, session_id: str) -> dict[str, Any] | None:
    lines = _read_lines(session_file_path(cwd, session_id))
    if not lines:
        return None
    last_boundary = -1
    for i in range(len(lines) - 1, -1, -1):
        event = parse_event(lines[i])
        if event and event.get("type") == "compact_boundary":
            last_boundary = i
            break
    state = create_context_collapse_state()
    for line in lines[last_boundary + 1:]:
        event = parse_event(line)
        span = event.get("contextCollapseSpan") if event and event.get("type") == "context_collapse" else None
        if isinstance(span, dict) and span.get("status") == "committed":
            state["spans"].append(span)
    return state if state.get("spans") else None


async def clear_session(cwd: str, session_id: str) -> None:
    file_path = session_file_path(cwd, session_id)
    try:
        file_path.unlink()
    except OSError:
        pass
    try:
        directory = project_dir(cwd)
        if directory.exists() and not any(directory.iterdir()):
            shutil.rmtree(directory, ignore_errors=True)
    except OSError:
        pass


async def list_sessions(cwd: str) -> list[dict[str, Any]]:
    directory = project_dir(cwd)
    if not directory.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in directory.glob("*.jsonl"):
        try:
            lines = _read_lines(path)
            results.append({"id": path.stem, "title": extract_title_from_events(lines), "messageCount": len(lines), "updatedAt": path.stat().st_mtime * 1000})
        except OSError:
            continue
    results.sort(key=lambda x: x["updatedAt"], reverse=True)
    return results


async def rename_session(cwd: str, session_id: str, new_title: str) -> bool:
    file_path = session_file_path(cwd, session_id)
    if not file_path.exists():
        return False
    event = {"type": "rename", "title": new_title, "uuid": str(uuid.uuid4()), "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sessionId": session_id, "cwd": cwd}
    project_dir(cwd).mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return True


async def fork_session(cwd: str, session_id: str) -> str | None:
    loaded = await load_session(cwd, session_id)
    if not loaded:
        return None
    new_id = str(uuid.uuid4())[:8]
    await save_session(cwd, new_id, [{"role": "system", "content": ""}, *loaded])
    sessions = await list_sessions(cwd)
    source = next((item for item in sessions if item.get("id") == session_id), None)
    base_title = source.get("title") if source else "session"
    existing_nums = []
    prefix = f"{base_title}_fork"
    for session in sessions:
        title = session.get("title") or ""
        if title.startswith(prefix):
            try:
                existing_nums.append(int(title[len(prefix):]))
            except ValueError:
                pass
    next_num = max(existing_nums) + 1 if existing_nums else 1
    await rename_session(cwd, new_id, f"{base_title}_fork{next_num}")
    return new_id


async def cleanup_expired_sessions(cwd: str, max_age_ms: int) -> int:
    directory = project_dir(cwd)
    if not directory.exists():
        return 0
    now = time.time() * 1000
    removed = 0
    for path in directory.glob("*.jsonl"):
        try:
            if now - path.stat().st_mtime * 1000 > max_age_ms:
                path.unlink()
                removed += 1
        except OSError:
            pass
    try:
        if directory.exists() and not any(directory.iterdir()):
            shutil.rmtree(directory, ignore_errors=True)
    except OSError:
        pass
    return removed


async def list_all_projects() -> list[dict[str, Any]]:
    root = Path(TINYCODER_PROJECTS_DIR)
    if not root.exists():
        return []
    results: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        files = list(entry.glob("*.jsonl"))
        if not files:
            continue
        latest = max(path.stat().st_mtime * 1000 for path in files)
        results.append({"dir": entry.name, "sessionCount": len(files), "latestUpdatedAt": latest})
    results.sort(key=lambda x: x["latestUpdatedAt"], reverse=True)
    return results


async def load_transcript(cwd: str, session_id: str) -> list[dict[str, Any]] | None:
    lines = _read_lines(session_file_path(cwd, session_id))
    if not lines:
        return None
    events = reconstruct_snipped_events([event for line in lines if (event := parse_event(line))])
    entries: list[dict[str, Any]] = []
    for event in events:
        msg = event.get("message") if isinstance(event.get("message"), dict) else {}
        event_type = event.get("type")
        if event_type == "user":
            entries.append({"kind": "user", "body": msg.get("content") if isinstance(msg.get("content"), str) else ""})
        elif event_type == "assistant":
            entries.append({"kind": "assistant", "body": msg.get("content") if isinstance(msg.get("content"), str) else ""})
        elif event_type == "progress":
            entries.append({"kind": "progress", "body": msg.get("content") if isinstance(msg.get("content"), str) else ""})
        elif event_type == "tool_call":
            entries.append({"kind": "tool", "toolName": msg.get("toolName") if isinstance(msg.get("toolName"), str) else "unknown", "status": "success", "body": json.dumps(msg.get("input") or "", ensure_ascii=False)})
        elif event_type == "summary":
            entries.append({"kind": "assistant", "body": f"[Context summary: {msg.get('compressedCount') or 0} messages compressed]"})
        elif event_type == "compact_boundary":
            meta = event.get("compactMetadata") or {}
            entries.append({"kind": "assistant", "body": f"[Context compacted: {meta.get('preTokens') or '?'} → {meta.get('postTokens') or '?'} tokens]"})
        elif event_type == "snip_boundary":
            meta = event.get("snipMetadata") or {}
            entries.append({"kind": "assistant", "body": f"[Snipped earlier context: removed {meta.get('removedCount') or '?'} messages, freed ~{meta.get('tokensFreed') or '?'} tokens]"})
    return entries or None


projectDirName = project_dir_name
projectDir = project_dir
sessionFilePath = session_file_path
roleToType = role_to_type
ensureMessageId = ensure_message_id
wrapEvent = wrap_event
parseEvent = parse_event
unwrapMessage = unwrap_message
reconstructSnippedEvents = reconstruct_snipped_events
extractTitleFromEvents = extract_title_from_events
saveSession = save_session
appendSnipBoundary = append_snip_boundary
appendContextCollapseSpan = append_context_collapse_span
appendCompactBoundary = append_compact_boundary
loadSession = load_session
loadContextCollapseState = load_context_collapse_state
clearSession = clear_session
listSessions = list_sessions
renameSession = rename_session
forkSession = fork_session
cleanupExpiredSessions = cleanup_expired_sessions
listAllProjects = list_all_projects
loadTranscript = load_transcript
