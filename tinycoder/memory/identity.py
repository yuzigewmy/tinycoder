from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProjectIdentity:
    project_id: str
    root: str
    source: str
    canonical_source: str


def normalize_git_remote(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.match(r"^[^/@:]+@[^/:]+:", raw):
        _, remainder = raw.split("@", 1)
        host, path = remainder.split(":", 1)
        normalized = f"{host}/{path}"
    else:
        parsed = urlsplit(raw if "://" in raw else f"ssh://{raw}")
        host = parsed.hostname or ""
        path = parsed.path.lstrip("/")
        normalized = f"{host}/{path}" if host else path
    normalized = normalized.replace("\\", "/").strip("/")
    if normalized.lower().endswith(".git"):
        normalized = normalized[:-4]
    return normalized.lower()


def _git(cwd: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def _stable_id(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def resolve_project_identity(cwd: str | Path) -> ProjectIdentity:
    workspace = Path(cwd).expanduser().resolve()
    root_raw = _git(workspace, "rev-parse", "--show-toplevel")
    if root_raw:
        root = Path(root_raw).resolve()
        remote = normalize_git_remote(_git(root, "config", "--get", "remote.origin.url") or "")
        if remote:
            return ProjectIdentity(_stable_id(f"remote:{remote}"), str(root), "remote", remote)
        common_raw = _git(root, "rev-parse", "--git-common-dir")
        if common_raw:
            common = Path(common_raw)
            if not common.is_absolute():
                common = (root / common).resolve()
            canonical = str(common).replace("\\", "/").casefold()
            return ProjectIdentity(_stable_id(f"git:{canonical}"), str(root), "git", canonical)
    canonical_path = str(workspace).replace("\\", "/").casefold()
    return ProjectIdentity(_stable_id(f"path:{canonical_path}"), str(workspace), "path", canonical_path)
