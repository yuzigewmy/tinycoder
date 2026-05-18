from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, Literal, Optional, TypedDict

from .config import MINI_CODE_DIR

PermissionDecision = Literal[
    "allow_once", "allow_always", "allow_turn", "allow_all_turn",
    "deny_once", "deny_always", "deny_with_feedback",
]
PathIntent = Literal["read", "write", "list", "search", "command_cwd"]


class PermissionChoice(TypedDict):
    key: str
    label: str
    decision: PermissionDecision


class PermissionRequest(TypedDict):
    kind: Literal["path", "command", "edit"]
    summary: str
    details: list[str]
    scope: str
    choices: list[PermissionChoice]


PermissionPromptHandler = Callable[[PermissionRequest], Awaitable[dict[str, Any]]]
PERMISSIONS_PATH = MINI_CODE_DIR / "permissions.json"


def normalize_path(target_path: str) -> str:
    return str(Path(target_path).resolve())


def is_within_directory(root: str, target: str) -> bool:
    try:
        Path(target).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def matches_directory_prefix(target_path: str, directories: Iterable[str]) -> bool:
    return any(is_within_directory(directory, target_path) for directory in directories)


def format_command_signature(command: str, args: list[str]) -> str:
    return " ".join([command, *args]).strip()


def classify_dangerous_command(command: str, args: list[str]) -> str | None:
    normalized = [a.strip() for a in args if a.strip()]
    signature = format_command_signature(command, normalized)
    if command == "git":
        if "reset" in normalized and "--hard" in normalized:
            return f"git reset --hard can discard local changes ({signature})"
        if "clean" in normalized:
            return f"git clean can delete untracked files ({signature})"
        if "checkout" in normalized and "--" in normalized:
            return f"git checkout -- can overwrite working tree files ({signature})"
        if "restore" in normalized and any(a.startswith("--source") for a in normalized):
            return f"git restore --source can overwrite local files ({signature})"
        if "push" in normalized and any(a in {"--force", "-f"} for a in normalized):
            return f"git push --force rewrites remote history ({signature})"
    if command == "npm" and "publish" in normalized:
        return f"npm publish affects a registry outside this machine ({signature})"
    if command in {"node", "python3", "python", "bun", "bash", "sh"}:
        return f"{command} can execute arbitrary local code ({signature})"
    return None


def _read_store() -> dict[str, Any]:
    try:
        parsed = json.loads(PERMISSIONS_PATH.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except FileNotFoundError:
        return {}


def _write_store(store: dict[str, Any]) -> None:
    MINI_CODE_DIR.mkdir(parents=True, exist_ok=True)
    PERMISSIONS_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class PermissionManager:
    def __init__(self, workspace_root: str, prompt: PermissionPromptHandler | None = None) -> None:
        self.workspace_root = normalize_path(workspace_root)
        self.prompt = prompt
        self.allowed_directory_prefixes: set[str] = set()
        self.denied_directory_prefixes: set[str] = set()
        self.session_allowed_paths: set[str] = set()
        self.session_denied_paths: set[str] = set()
        self.allowed_command_patterns: set[str] = set()
        self.denied_command_patterns: set[str] = set()
        self.session_allowed_commands: set[str] = set()
        self.session_denied_commands: set[str] = set()
        self.allowed_edit_patterns: set[str] = set()
        self.denied_edit_patterns: set[str] = set()
        self.session_allowed_edits: set[str] = set()
        self.session_denied_edits: set[str] = set()
        self.turn_allowed_edits: set[str] = set()
        self.turn_allow_all_edits = False
        self._initialize()

    def _initialize(self) -> None:
        store = _read_store()
        for directory in store.get("allowedDirectoryPrefixes") or []:
            self.allowed_directory_prefixes.add(normalize_path(str(directory)))
        for directory in store.get("deniedDirectoryPrefixes") or []:
            self.denied_directory_prefixes.add(normalize_path(str(directory)))
        for pattern in store.get("allowedCommandPatterns") or []:
            self.allowed_command_patterns.add(str(pattern))
        for pattern in store.get("deniedCommandPatterns") or []:
            self.denied_command_patterns.add(str(pattern))
        for pattern in store.get("allowedEditPatterns") or []:
            self.allowed_edit_patterns.add(normalize_path(str(pattern)))
        for pattern in store.get("deniedEditPatterns") or []:
            self.denied_edit_patterns.add(normalize_path(str(pattern)))

    async def when_ready(self) -> None:
        return None

    async def whenReady(self) -> None:
        return await self.when_ready()

    def begin_turn(self) -> None:
        self.turn_allowed_edits.clear()
        self.turn_allow_all_edits = False

    def beginTurn(self) -> None:
        self.begin_turn()

    def end_turn(self) -> None:
        self.turn_allowed_edits.clear()
        self.turn_allow_all_edits = False

    def endTurn(self) -> None:
        self.end_turn()

    def get_summary(self) -> list[str]:
        summary = [f"cwd: {self.workspace_root}"]
        summary.append(
            "extra allowed dirs: " + ", ".join(list(self.allowed_directory_prefixes)[:4])
            if self.allowed_directory_prefixes else "extra allowed dirs: none"
        )
        summary.append(
            "dangerous allowlist: " + ", ".join(list(self.allowed_command_patterns)[:4])
            if self.allowed_command_patterns else "dangerous allowlist: none"
        )
        if self.allowed_edit_patterns:
            summary.append("trusted edit targets: " + ", ".join(list(self.allowed_edit_patterns)[:2]))
        return summary

    def getSummary(self) -> list[str]:
        return self.get_summary()

    async def _persist(self) -> None:
        _write_store({
            "allowedDirectoryPrefixes": sorted(self.allowed_directory_prefixes),
            "deniedDirectoryPrefixes": sorted(self.denied_directory_prefixes),
            "allowedCommandPatterns": sorted(self.allowed_command_patterns),
            "deniedCommandPatterns": sorted(self.denied_command_patterns),
            "allowedEditPatterns": sorted(self.allowed_edit_patterns),
            "deniedEditPatterns": sorted(self.denied_edit_patterns),
        })

    async def ensure_path_access(self, target_path: str, intent: PathIntent) -> None:
        target = normalize_path(target_path)
        if is_within_directory(self.workspace_root, target):
            return
        if target in self.session_denied_paths or matches_directory_prefix(target, self.denied_directory_prefixes):
            raise RuntimeError(f"Access denied for path outside cwd: {target}")
        if target in self.session_allowed_paths or matches_directory_prefix(target, self.allowed_directory_prefixes):
            return
        if not self.prompt:
            raise RuntimeError(f"Path {target} is outside cwd {self.workspace_root}. Start minicode in TTY mode to approve it.")
        scope_directory = target if intent in {"list", "command_cwd"} else str(Path(target).parent)
        result = await self.prompt({
            "kind": "path",
            "summary": f"mini-code wants {intent.replace('_', ' ')} access outside the current cwd",
            "details": [f"cwd: {self.workspace_root}", f"target: {target}", f"scope directory: {scope_directory}"],
            "scope": scope_directory,
            "choices": [
                {"key": "y", "label": "allow once", "decision": "allow_once"},
                {"key": "a", "label": "allow this directory", "decision": "allow_always"},
                {"key": "n", "label": "deny once", "decision": "deny_once"},
                {"key": "d", "label": "deny this directory", "decision": "deny_always"},
            ],
        })
        decision = result.get("decision")
        if decision == "allow_once":
            self.session_allowed_paths.add(target)
            return
        if decision == "allow_always":
            self.allowed_directory_prefixes.add(scope_directory)
            await self._persist()
            return
        if decision == "deny_always":
            self.denied_directory_prefixes.add(scope_directory)
            await self._persist()
        else:
            self.session_denied_paths.add(target)
        raise RuntimeError(f"Access denied for path outside cwd: {target}")

    async def ensurePathAccess(self, target_path: str, intent: PathIntent) -> None:
        return await self.ensure_path_access(target_path, intent)

    async def ensure_command(self, command: str, args: list[str], command_cwd: str, options: dict[str, Any] | None = None) -> None:
        await self.ensure_path_access(command_cwd, "command_cwd")
        reason = (options or {}).get("forcePromptReason") or classify_dangerous_command(command, args)
        if not reason:
            return
        signature = format_command_signature(command, args)
        if signature in self.session_denied_commands or signature in self.denied_command_patterns:
            raise RuntimeError(f"Command denied: {signature}")
        if signature in self.session_allowed_commands or signature in self.allowed_command_patterns:
            return
        if not self.prompt:
            raise RuntimeError(f"Command requires approval: {signature}. Start minicode in TTY mode to approve it.")
        result = await self.prompt({
            "kind": "command",
            "summary": "mini-code wants approval for this command" if (options or {}).get("forcePromptReason") else "mini-code wants to run a dangerous command",
            "details": [f"cwd: {command_cwd}", f"command: {signature}", f"reason: {reason}"],
            "scope": signature,
            "choices": [
                {"key": "y", "label": "allow once", "decision": "allow_once"},
                {"key": "a", "label": "always allow this command", "decision": "allow_always"},
                {"key": "n", "label": "deny once", "decision": "deny_once"},
                {"key": "d", "label": "always deny this command", "decision": "deny_always"},
            ],
        })
        decision = result.get("decision")
        if decision == "allow_once":
            self.session_allowed_commands.add(signature)
            return
        if decision == "allow_always":
            self.allowed_command_patterns.add(signature)
            await self._persist()
            return
        if decision == "deny_always":
            self.denied_command_patterns.add(signature)
            await self._persist()
        else:
            self.session_denied_commands.add(signature)
        raise RuntimeError(f"Command denied: {signature}")

    async def ensureCommand(self, command: str, args: list[str], command_cwd: str, options: dict[str, Any] | None = None) -> None:
        return await self.ensure_command(command, args, command_cwd, options)

    async def ensure_edit(self, target_path: str, diff_preview: str) -> None:
        target = normalize_path(target_path)
        if target in self.session_denied_edits or target in self.denied_edit_patterns:
            raise RuntimeError(f"Edit denied: {target}")
        if target in self.session_allowed_edits or target in self.turn_allowed_edits or self.turn_allow_all_edits or target in self.allowed_edit_patterns:
            return
        if not self.prompt:
            raise RuntimeError(f"Edit requires approval: {target}. Start minicode in TTY mode to review it.")
        result = await self.prompt({
            "kind": "edit",
            "summary": "mini-code wants to apply a file modification",
            "details": [f"target: {target}", "", diff_preview],
            "scope": target,
            "choices": [
                {"key": "1", "label": "apply once", "decision": "allow_once"},
                {"key": "2", "label": "allow this file in this turn", "decision": "allow_turn"},
                {"key": "3", "label": "allow all edits in this turn", "decision": "allow_all_turn"},
                {"key": "4", "label": "always allow this file", "decision": "allow_always"},
                {"key": "5", "label": "reject once", "decision": "deny_once"},
                {"key": "6", "label": "reject and send guidance to model", "decision": "deny_with_feedback"},
                {"key": "7", "label": "always reject this file", "decision": "deny_always"},
            ],
        })
        decision = result.get("decision")
        if decision == "allow_once":
            self.session_allowed_edits.add(target)
            return
        if decision == "allow_turn":
            self.turn_allowed_edits.add(target)
            return
        if decision == "allow_all_turn":
            self.turn_allow_all_edits = True
            return
        if decision == "allow_always":
            self.allowed_edit_patterns.add(target)
            await self._persist()
            return
        if decision == "deny_with_feedback":
            guidance = str(result.get("feedback") or "").strip()
            self.session_denied_edits.add(target)
            if guidance:
                raise RuntimeError(f"Edit denied: {target}\nUser guidance: {guidance}")
            raise RuntimeError(f"Edit denied: {target}")
        if decision == "deny_always":
            self.denied_edit_patterns.add(target)
            await self._persist()
        else:
            self.session_denied_edits.add(target)
        raise RuntimeError(f"Edit denied: {target}")

    async def ensureEdit(self, target_path: str, diff_preview: str) -> None:
        return await self.ensure_edit(target_path, diff_preview)


def get_permissions_path() -> str:
    return str(PERMISSIONS_PATH)

getPermissionsPath = get_permissions_path
