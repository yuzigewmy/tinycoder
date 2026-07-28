from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .identity import resolve_project_identity


MEMORY_MAX_LINES = 200
MEMORY_MAX_BYTES = 25 * 1024
INSTRUCTION_MAX_BYTES = 100 * 1024
TOTAL_CONTEXT_MAX_BYTES = 512 * 1024


@dataclass(frozen=True)
class InstructionDocument:
    path: str
    scope: str
    content: str
    is_memory: bool = False


def _read_text(path: Path, *, memory_limit: bool = False) -> str | None:
    limit = MEMORY_MAX_BYTES if memory_limit else INSTRUCTION_MAX_BYTES
    try:
        with path.open("rb") as handle:
            text = handle.read(limit + 1)[:limit].decode("utf-8", "ignore")
    except OSError:
        return None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if memory_limit:
        text = "\n".join(text.splitlines()[:MEMORY_MAX_LINES])
    return text.strip() or None


def _frontmatter_paths(content: str) -> tuple[list[str], str]:
    if not content.startswith("---\n"):
        return [], content
    end = content.find("\n---", 4)
    if end < 0:
        return [], content
    header = content[4:end]
    body = content[end + 4 :].lstrip("\r\n")
    paths: list[str] = []
    in_paths = False
    for raw_line in header.splitlines():
        line = raw_line.strip()
        if line == "paths:":
            in_paths = True
            continue
        if in_paths and line.startswith("-"):
            value = line[1:].strip().strip("\"'")
            if value:
                paths.append(value)
            continue
        if line and not line.startswith("#"):
            in_paths = False
    return paths, body


def _path_matches(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    normalized_pattern = pattern.replace("\\", "/").lstrip("./")
    return fnmatch.fnmatch(normalized, normalized_pattern) or (
        "**/" in normalized_pattern
        and fnmatch.fnmatch(normalized, normalized_pattern.replace("**/", ""))
    )


def _rule_applies(patterns: list[str], active_paths: list[str]) -> bool:
    if not patterns:
        return True
    return any(_path_matches(path, pattern) for path in active_paths for pattern in patterns)


def _instruction_paths(project_root: Path, cwd: Path) -> list[tuple[Path, str]]:
    try:
        relative = cwd.relative_to(project_root)
        levels = [project_root]
        cursor = project_root
        for part in relative.parts:
            cursor = cursor / part
            levels.append(cursor)
    except ValueError:
        levels = [cwd]
    results: list[tuple[Path, str]] = []
    for level in levels:
        results.extend(
            [
                (level / "CLAUDE.md", "project"),
                (level / ".claude" / "CLAUDE.md", "project"),
                (level / ".tinycoder" / "CLAUDE.md", "project"),
                (level / "CLAUDE.local.md", "project_local"),
            ]
        )
    return results


def load_instruction_documents(
    cwd: str | Path,
    *,
    active_paths: list[str] | None = None,
    user_home: str | Path | None = None,
    tinycoder_home: str | Path | None = None,
) -> list[InstructionDocument]:
    workspace = Path(cwd).expanduser().resolve()
    home = Path(user_home).expanduser().resolve() if user_home else Path.home()
    app_home = (
        Path(tinycoder_home).expanduser().resolve()
        if tinycoder_home
        else Path(os.environ.get("TINYCODER_HOME", home / ".tinycoder")).expanduser().resolve()
    )
    identity = resolve_project_identity(workspace)
    project_root = Path(identity.root)
    active = list(active_paths or [])
    candidates: list[tuple[Path, str, bool]] = [
        (app_home / "CLAUDE.md", "user", False),
        (home / ".claude" / "CLAUDE.md", "user_compat", False),
        *[(path, scope, False) for path, scope in _instruction_paths(project_root, workspace)],
    ]
    rules_roots = [project_root / ".tinycoder" / "rules", project_root / ".claude" / "rules"]
    for rules_root in rules_roots:
        if rules_root.is_dir():
            candidates.extend((path, "project_rule", False) for path in sorted(rules_root.rglob("*.md")))
    candidates.extend(
        [
            (app_home / "memory" / "global" / "MEMORY.md", "user_memory", True),
            (
                app_home / "projects" / identity.project_id / "memory" / "MEMORY.md",
                "project_local_memory",
                True,
            ),
            (project_root / ".tinycoder" / "memory" / "MEMORY.md", "project_shared_memory", True),
        ]
    )

    documents: list[InstructionDocument] = []
    seen: set[str] = set()
    total_bytes = 0
    for path, scope, is_memory in candidates:
        key = str(path.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        content = _read_text(path, memory_limit=is_memory)
        if not content:
            continue
        if scope == "project_rule":
            patterns, content = _frontmatter_paths(content)
            if not _rule_applies(patterns, active):
                continue
        remaining = TOTAL_CONTEXT_MAX_BYTES - total_bytes
        if remaining <= 0:
            break
        encoded = content.encode("utf-8")
        if len(encoded) > remaining:
            content = encoded[:remaining].decode("utf-8", "ignore").rstrip()
        total_bytes += len(content.encode("utf-8"))
        documents.append(
            InstructionDocument(path=str(path), scope=scope, content=content, is_memory=is_memory)
        )
    return documents


def render_instruction_documents(documents: list[InstructionDocument]) -> str:
    return "\n\n".join(
        f"[{document.scope} from {document.path}]\n{document.content}"
        for document in documents
    )
