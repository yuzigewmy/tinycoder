from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Literal, TypedDict, cast

from .config import TINYCODER_DIR

PermissionMode = Literal["request_approval", "auto_approve", "full_access"]
RiskLevel = Literal["low", "medium", "high", "critical"]
PermissionDecision = Literal[
    "allow_once",
    "allow_always",
    "allow_turn",
    "allow_all_turn",
    "deny_once",
    "deny_always",
    "deny_with_feedback",
]
PathIntent = Literal["read", "write", "list", "search", "command_cwd"]


class PermissionChoice(TypedDict):
    key: str
    label: str
    decision: PermissionDecision


class PermissionRequest(TypedDict):
    kind: Literal["path", "command", "edit", "external"]
    summary: str
    details: list[str]
    scope: str
    risk: RiskLevel
    choices: list[PermissionChoice]


class AutoReviewRecord(TypedDict):
    timestamp: str
    action: str
    scopeFingerprint: str
    risk: RiskLevel
    decision: Literal["allow", "deny", "require_user"]
    reason: str


PermissionPromptHandler = Callable[[PermissionRequest], Awaitable[dict[str, Any]]]
PERMISSIONS_PATH = TINYCODER_DIR / "permissions.json"
DEFAULT_PERMISSION_MODE: PermissionMode = "request_approval"
VALID_RISK_LEVELS: frozenset[str] = frozenset({"low", "medium", "high", "critical"})

PERMISSION_MODE_PROFILES: dict[PermissionMode, dict[str, str]] = {
    "request_approval": {
        "label": "请求批准",
        "description": "允许工作区内常规读写；越界、危险命令、联网和 MCP 动作由用户审批。",
    },
    "auto_approve": {
        "label": "替我审批",
        "description": "保留工作区边界；低/中风险自动放行，严重风险拒绝，高风险回退给用户。",
    },
    "full_access": {
        "label": "完全访问",
        "description": "绕过 TinyCoder 应用层权限检查；不提供操作系统级沙箱保护。",
    },
}

_MODE_ALIASES: dict[str, PermissionMode] = {
    "request": "request_approval",
    "ask": "request_approval",
    "request-approval": "request_approval",
    "request_approval": "request_approval",
    "请求批准": "request_approval",
    "auto": "auto_approve",
    "approve": "auto_approve",
    "auto-approve": "auto_approve",
    "auto_approve": "auto_approve",
    "替我审批": "auto_approve",
    "full": "full_access",
    "full-access": "full_access",
    "full_access": "full_access",
    "完全访问": "full_access",
}

_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(
        r"(?i)(?:api[_-]?key|auth[_-]?token|access[_-]?token|password|passwd|secret)"
        r"\s*[:=]\s*[^\s,;]{6,}"
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:^|[/\\])\.ssh(?:[/\\]|$)"),
    re.compile(r"(?i)(?:^|[/\\])\.aws[/\\]credentials(?=$|[/\\\"'])"),
    re.compile(r"(?i)(?:^|[/\\])\.env(?:\.[^/\\\"']+)?(?=$|[/\\\"'])"),
    re.compile(
        r"(?i)(?:^|[/\\])id_(?:rsa|ed25519|ecdsa)(?:\.pub)?(?=$|[/\\\"'])"
    ),
)


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


def parse_permission_mode(value: str | PermissionMode) -> PermissionMode:
    normalized = str(value).strip().lower()
    mode = _MODE_ALIASES.get(normalized)
    if mode is None:
        raise ValueError(
            "Unknown permission mode. Use request-approval, auto-approve, or full-access."
        )
    return mode


def permission_mode_label(mode: str | PermissionMode) -> str:
    return PERMISSION_MODE_PROFILES[parse_permission_mode(mode)]["label"]


def _serialize_for_review(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def contains_sensitive_data(value: Any) -> bool:
    text = _serialize_for_review(value)
    return any(pattern.search(text) for pattern in _SENSITIVE_TEXT_PATTERNS)


def classify_command_risk(
    command: str,
    args: list[str],
    force_reason: str | None = None,
) -> tuple[RiskLevel, str] | None:
    normalized = [arg.strip() for arg in args if arg.strip()]
    signature = format_command_signature(command, normalized)
    lowered = signature.lower()

    if contains_sensitive_data(signature):
        return "critical", "command may access or expose credentials or private keys"
    if (
        ("git reset" in lowered and "--hard" in lowered)
        or re.search(r"\bgit\s+clean\b", lowered)
        or (
            "git push" in lowered
            and any(flag in normalized for flag in {"--force", "-f", "--force-with-lease"})
        )
        or re.search(r"\brm\s+(?:-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\b", lowered)
        or ("remove-item" in lowered and "-recurse" in lowered)
        or re.search(r"\bterraform\s+destroy\b", lowered)
        or re.search(r"\bkubectl\s+delete\s+namespace\b", lowered)
        or re.search(r"\b(?:drop\s+database|truncate\s+table)\b", lowered)
    ):
        return "critical", "command can cause destructive or difficult-to-recover changes"
    if command == "git":
        known_subcommands = {
            "fetch",
            "ls-remote",
            "push",
            "pull",
            "clone",
            "checkout",
            "switch",
            "commit",
            "merge",
            "rebase",
            "am",
            "submodule",
        }
        subcommand = next(
            (arg for arg in normalized if arg in known_subcommands),
            "",
        )
        if "checkout" in normalized and "--" in normalized:
            return "high", "git checkout -- can overwrite working tree files"
        if "restore" in normalized and any(arg.startswith("--source") for arg in normalized):
            return "high", "git restore --source can overwrite local files"
        if subcommand in {"fetch", "ls-remote"}:
            return "medium", f"git {subcommand} accesses a remote network service"
        if subcommand in {"push", "pull", "clone"}:
            return "high", f"git {subcommand} accesses or changes remote state"
        if subcommand in {
            "checkout",
            "switch",
            "commit",
            "merge",
            "rebase",
            "am",
            "submodule",
        }:
            return "high", f"git {subcommand} may run hooks or make broad workspace changes"
    if command == "npm":
        known_subcommands = {
            "publish",
            "view",
            "info",
            "search",
            "outdated",
            "audit",
            "install",
            "i",
            "ci",
            "update",
            "run",
            "test",
            "start",
            "exec",
        }
        subcommand = next(
            (arg for arg in normalized if arg in known_subcommands),
            "",
        )
        if subcommand == "publish":
            return "high", "npm publish changes an external package registry"
        if subcommand in {"view", "info", "search", "outdated", "audit"}:
            return "medium", f"npm {subcommand} accesses the package registry"
        if subcommand in {"install", "i", "ci", "update", "run", "test", "start", "exec"}:
            return "high", f"npm {subcommand} may download or execute package code"
    if force_reason:
        return "high", str(force_reason)
    if command in {
        "node",
        "python3",
        "python",
        "pytest",
        "bun",
        "bash",
        "sh",
        "powershell",
        "pwsh",
    }:
        return "high", f"{command} can execute arbitrary local code"
    return None


def classify_dangerous_command(command: str, args: list[str]) -> str | None:
    assessment = classify_command_risk(command, args)
    return assessment[1] if assessment else None


def _read_store(path: Path = PERMISSIONS_PATH) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_store(store: dict[str, Any], path: Path = PERMISSIONS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(json.dumps(store, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def format_permission_mode_status(manager: PermissionManager) -> str:
    profile = PERMISSION_MODE_PROFILES[manager.mode]
    lines = [
        f"当前权限: {profile['label']} ({manager.mode})",
        profile["description"],
        "",
        "可用模式:",
    ]
    for mode in ("request_approval", "auto_approve", "full_access"):
        item = PERMISSION_MODE_PROFILES[cast(PermissionMode, mode)]
        marker = "*" if mode == manager.mode else " "
        lines.append(f"{marker} {item['label']} ({mode.replace('_', '-')})")
        lines.append(f"  {item['description']}")
    lines.extend(
        [
            "",
            "切换: /permissions <request-approval|auto-approve|full-access>",
            "完全访问需再次确认: /permissions full-access confirm",
            f"permission store: {manager.store_path}",
            "说明: 这是 TinyCoder 应用层权限控制，不是操作系统级沙箱。",
        ]
    )
    return "\n".join(lines)


class PermissionManager:
    def __init__(
        self,
        workspace_root: str,
        prompt: PermissionPromptHandler | None = None,
        *,
        mode: PermissionMode | str | None = None,
        store_path: str | Path | None = None,
    ) -> None:
        self.workspace_root = normalize_path(workspace_root)
        self.prompt = prompt
        self.store_path = Path(store_path or PERMISSIONS_PATH).expanduser().resolve()
        self.mode: PermissionMode = DEFAULT_PERMISSION_MODE
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
        self.allowed_external_actions: set[str] = set()
        self.denied_external_actions: set[str] = set()
        self.session_allowed_external_actions: set[str] = set()
        self.session_denied_external_actions: set[str] = set()
        self._review_history: list[AutoReviewRecord] = []
        self._initialize(mode)

    def _initialize(self, explicit_mode: PermissionMode | str | None) -> None:
        store = _read_store(self.store_path)
        stored_mode = store.get("mode")
        if explicit_mode is not None:
            self.mode = parse_permission_mode(explicit_mode)
        elif stored_mode is not None:
            try:
                self.mode = parse_permission_mode(str(stored_mode))
            except ValueError:
                self.mode = DEFAULT_PERMISSION_MODE
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
        for action in store.get("allowedExternalActions") or []:
            self.allowed_external_actions.add(str(action))
        for action in store.get("deniedExternalActions") or []:
            self.denied_external_actions.add(str(action))

    async def when_ready(self) -> None:
        return None

    async def whenReady(self) -> None:
        return await self.when_ready()

    async def set_mode(
        self,
        mode: PermissionMode | str,
        *,
        confirm_full_access: bool = False,
    ) -> None:
        selected = parse_permission_mode(mode)
        if selected == "full_access" and not confirm_full_access:
            raise ValueError(
                "Full access requires explicit confirmation because it bypasses "
                "TinyCoder application-level permission checks."
            )
        previous_mode = self.mode
        self.mode = selected
        try:
            await self._persist()
        except Exception:
            self.mode = previous_mode
            raise
        self._clear_session_decisions()

    async def setMode(
        self,
        mode: PermissionMode | str,
        *,
        confirmFullAccess: bool = False,
    ) -> None:
        await self.set_mode(mode, confirm_full_access=confirmFullAccess)

    def _clear_session_decisions(self) -> None:
        self.session_allowed_paths.clear()
        self.session_denied_paths.clear()
        self.session_allowed_commands.clear()
        self.session_denied_commands.clear()
        self.session_allowed_edits.clear()
        self.session_denied_edits.clear()
        self.session_allowed_external_actions.clear()
        self.session_denied_external_actions.clear()
        self.begin_turn()

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
        profile = PERMISSION_MODE_PROFILES[self.mode]
        summary = [
            f"cwd: {self.workspace_root}",
            f"permission mode: {self.mode} ({profile['label']})",
            f"permission policy: {profile['description']}",
        ]
        summary.append(
            "extra allowed dirs: " + ", ".join(sorted(self.allowed_directory_prefixes)[:4])
            if self.allowed_directory_prefixes
            else "extra allowed dirs: none"
        )
        summary.append(
            "dangerous allowlist: " + ", ".join(sorted(self.allowed_command_patterns)[:4])
            if self.allowed_command_patterns
            else "dangerous allowlist: none"
        )
        if self.allowed_edit_patterns:
            summary.append(
                "trusted edit targets: " + ", ".join(sorted(self.allowed_edit_patterns)[:2])
            )
        if self.mode == "full_access":
            summary.append(
                "warning: full_access bypasses TinyCoder checks and is not an OS sandbox"
            )
        return summary

    def getSummary(self) -> list[str]:
        return self.get_summary()

    def get_review_history(self) -> list[AutoReviewRecord]:
        return [dict(record) for record in self._review_history]

    def getReviewHistory(self) -> list[AutoReviewRecord]:
        return self.get_review_history()

    def _record_auto_review(
        self,
        *,
        action: str,
        scope: str,
        risk: RiskLevel,
        decision: Literal["allow", "deny", "require_user"],
        reason: str,
    ) -> None:
        fingerprint = hashlib.sha256(scope.encode("utf-8", "replace")).hexdigest()[:16]
        self._review_history.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "scopeFingerprint": fingerprint,
                "risk": risk,
                "decision": decision,
                "reason": reason,
            }
        )
        self._review_history = self._review_history[-50:]

    def _auto_review(
        self,
        *,
        action: str,
        scope: str,
        risk: RiskLevel,
        reason: str,
        payload: Any = None,
    ) -> tuple[Literal["allow", "deny", "require_user"], RiskLevel]:
        effective_risk: RiskLevel = (
            "critical" if contains_sensitive_data(payload) else risk
        )
        if effective_risk == "critical":
            decision: Literal["allow", "deny", "require_user"] = "deny"
        elif effective_risk == "high":
            decision = "require_user"
        else:
            decision = "allow"
        self._record_auto_review(
            action=action,
            scope=scope,
            risk=effective_risk,
            decision=decision,
            reason=reason,
        )
        return decision, effective_risk

    async def _persist(self) -> None:
        store = _read_store(self.store_path)
        store.update(
            {
                "mode": self.mode,
                "allowedDirectoryPrefixes": sorted(self.allowed_directory_prefixes),
                "deniedDirectoryPrefixes": sorted(self.denied_directory_prefixes),
                "allowedCommandPatterns": sorted(self.allowed_command_patterns),
                "deniedCommandPatterns": sorted(self.denied_command_patterns),
                "allowedEditPatterns": sorted(self.allowed_edit_patterns),
                "deniedEditPatterns": sorted(self.denied_edit_patterns),
                "allowedExternalActions": sorted(self.allowed_external_actions),
                "deniedExternalActions": sorted(self.denied_external_actions),
            }
        )
        _write_store(store, self.store_path)

    async def _prompt_for_scope(
        self,
        request: PermissionRequest,
        *,
        session_allowed: set[str],
        session_denied: set[str],
        always_allowed: set[str],
        always_denied: set[str],
        denied_message: str,
        session_scope: str | None = None,
        always_scope: str | None = None,
    ) -> None:
        if self.prompt is None:
            raise RuntimeError(
                f"{denied_message} This action requires user approval, "
                "but no interactive permission prompt is available."
            )
        result = await self.prompt(request)
        decision = result.get("decision")
        scope = request["scope"]
        if decision == "allow_once":
            session_allowed.add(session_scope or scope)
            return
        if decision == "allow_always":
            always_allowed.add(always_scope or scope)
            await self._persist()
            return
        if decision == "deny_always":
            always_denied.add(always_scope or scope)
            await self._persist()
        else:
            session_denied.add(session_scope or scope)
        raise RuntimeError(denied_message)

    @staticmethod
    def _approval_choices() -> list[PermissionChoice]:
        return [
            {"key": "y", "label": "allow once", "decision": "allow_once"},
            {"key": "a", "label": "always allow this action", "decision": "allow_always"},
            {"key": "n", "label": "deny once", "decision": "deny_once"},
            {"key": "d", "label": "always deny this action", "decision": "deny_always"},
        ]

    async def ensure_path_access(self, target_path: str, intent: PathIntent) -> None:
        if self.mode == "full_access":
            return
        target = normalize_path(target_path)
        if is_within_directory(self.workspace_root, target):
            return
        if target in self.session_denied_paths or matches_directory_prefix(
            target, self.denied_directory_prefixes
        ):
            raise RuntimeError(f"Access denied for path outside cwd: {target}")

        risk: RiskLevel = "medium" if intent in {"read", "list", "search"} else "high"
        if contains_sensitive_data(target):
            risk = "critical"

        if self.mode == "auto_approve" and risk == "critical":
            decision, effective_risk = self._auto_review(
                action=f"path:{intent}",
                scope=target,
                risk=risk,
                reason="sensitive path outside the workspace",
                payload=target,
            )
            raise RuntimeError(
                f"Permission auto-review denied {effective_risk}-risk path access: {target}"
            )

        if target in self.session_allowed_paths or matches_directory_prefix(
            target, self.allowed_directory_prefixes
        ):
            return

        scope_directory = (
            target if intent in {"list", "command_cwd"} else str(Path(target).parent)
        )
        if self.mode == "auto_approve":
            decision, effective_risk = self._auto_review(
                action=f"path:{intent}",
                scope=scope_directory,
                risk=risk,
                reason="path is outside the active workspace",
                payload=None,
            )
            if decision == "allow":
                self.session_allowed_paths.add(target)
                return
            if decision == "deny":
                raise RuntimeError(
                    f"Permission auto-review denied {effective_risk}-risk path access: {target}"
                )
            summary = "TinyCoder automatic reviewer requires user approval for path access"
        else:
            summary = (
                f"TinyCoder wants {intent.replace('_', ' ')} access outside "
                "the current workspace"
            )

        await self._prompt_for_scope(
            {
                "kind": "path",
                "summary": summary,
                "details": [
                    f"cwd: {self.workspace_root}",
                    f"target: {target}",
                    f"scope directory: {scope_directory}",
                    f"risk: {risk}",
                ],
                "scope": scope_directory,
                "risk": risk,
                "choices": self._approval_choices(),
            },
            session_allowed=self.session_allowed_paths,
            session_denied=self.session_denied_paths,
            always_allowed=self.allowed_directory_prefixes,
            always_denied=self.denied_directory_prefixes,
            denied_message=f"Access denied for path outside cwd: {target}",
            session_scope=target,
            always_scope=scope_directory,
        )

    async def ensurePathAccess(self, target_path: str, intent: PathIntent) -> None:
        await self.ensure_path_access(target_path, intent)

    async def ensure_command(
        self,
        command: str,
        args: list[str],
        command_cwd: str,
        options: dict[str, Any] | None = None,
    ) -> None:
        if self.mode == "full_access":
            return
        await self.ensure_path_access(command_cwd, "command_cwd")
        assessment = classify_command_risk(
            command,
            args,
            str((options or {}).get("forcePromptReason") or "") or None,
        )
        if assessment is None:
            return
        risk, reason = assessment
        signature = format_command_signature(command, args)
        if (
            signature in self.session_denied_commands
            or signature in self.denied_command_patterns
        ):
            raise RuntimeError(f"Command denied: {signature}")

        if self.mode == "auto_approve" and risk == "critical":
            _, effective_risk = self._auto_review(
                action="command",
                scope=signature,
                risk=risk,
                reason=reason,
            )
            raise RuntimeError(
                f"Permission auto-review denied {effective_risk}-risk command: {signature}"
            )

        if (
            signature in self.session_allowed_commands
            or signature in self.allowed_command_patterns
        ):
            return

        if self.mode == "auto_approve":
            decision, effective_risk = self._auto_review(
                action="command",
                scope=signature,
                risk=risk,
                reason=reason,
            )
            if decision == "allow":
                self.session_allowed_commands.add(signature)
                return
            if decision == "deny":
                raise RuntimeError(
                    f"Permission auto-review denied {effective_risk}-risk command: {signature}"
                )
            summary = "TinyCoder automatic reviewer requires user approval for a command"
        else:
            summary = "TinyCoder wants approval for a high-risk command"

        await self._prompt_for_scope(
            {
                "kind": "command",
                "summary": summary,
                "details": [
                    f"cwd: {command_cwd}",
                    f"command: {signature}",
                    f"reason: {reason}",
                    f"risk: {risk}",
                ],
                "scope": signature,
                "risk": risk,
                "choices": self._approval_choices(),
            },
            session_allowed=self.session_allowed_commands,
            session_denied=self.session_denied_commands,
            always_allowed=self.allowed_command_patterns,
            always_denied=self.denied_command_patterns,
            denied_message=f"Command denied: {signature}",
        )

    async def ensureCommand(
        self,
        command: str,
        args: list[str],
        command_cwd: str,
        options: dict[str, Any] | None = None,
    ) -> None:
        await self.ensure_command(command, args, command_cwd, options)

    async def ensure_edit(self, target_path: str, diff_preview: str) -> None:
        if self.mode == "full_access":
            return
        target = normalize_path(target_path)
        if target in self.session_denied_edits or target in self.denied_edit_patterns:
            raise RuntimeError(f"Edit denied: {target}")
        await self.ensure_path_access(target, "write")
        if target in self.session_allowed_edits or target in self.allowed_edit_patterns:
            return
        # Matching Codex workspace-write semantics: edits inside the approved
        # workspace do not need a second, per-diff approval. Outside edits were
        # already reviewed by ensure_path_access above.
        return

    async def ensureEdit(self, target_path: str, diff_preview: str) -> None:
        await self.ensure_edit(target_path, diff_preview)

    async def ensure_external_action(
        self,
        action: str,
        payload: Any,
        *,
        risk: RiskLevel = "high",
        reason: str,
        scope: str | None = None,
        details: list[str] | None = None,
    ) -> None:
        if risk not in VALID_RISK_LEVELS:
            raise ValueError(f"Unknown risk level: {risk}")
        if self.mode == "full_access":
            return
        normalized_risk = cast(RiskLevel, risk)
        action_scope = scope or action
        if (
            action_scope in self.session_denied_external_actions
            or action_scope in self.denied_external_actions
        ):
            raise RuntimeError(f"External action denied: {action_scope}")

        effective_risk: RiskLevel = (
            "critical" if contains_sensitive_data(payload) else normalized_risk
        )
        if self.mode == "auto_approve" and effective_risk == "critical":
            _, reviewed_risk = self._auto_review(
                action=action,
                scope=action_scope,
                risk=effective_risk,
                reason=reason,
                payload=payload,
            )
            raise RuntimeError(
                "Permission auto-review denied "
                f"{reviewed_risk}-risk external action: {action_scope}"
            )

        if (
            action_scope in self.session_allowed_external_actions
            or action_scope in self.allowed_external_actions
        ):
            return

        if self.mode == "auto_approve":
            decision, reviewed_risk = self._auto_review(
                action=action,
                scope=action_scope,
                risk=effective_risk,
                reason=reason,
                payload=payload,
            )
            if decision == "allow":
                self.session_allowed_external_actions.add(action_scope)
                return
            if decision == "deny":
                raise RuntimeError(
                    "Permission auto-review denied "
                    f"{reviewed_risk}-risk external action: {action_scope}"
                )
            summary = (
                "TinyCoder automatic reviewer requires user approval for "
                "an external action"
            )
        else:
            summary = "TinyCoder wants approval for an external action"

        await self._prompt_for_scope(
            {
                "kind": "external",
                "summary": summary,
                "details": [
                    f"action: {action}",
                    f"scope: {action_scope}",
                    f"reason: {reason}",
                    f"risk: {effective_risk}",
                    *(details or []),
                ],
                "scope": action_scope,
                "risk": effective_risk,
                "choices": self._approval_choices(),
            },
            session_allowed=self.session_allowed_external_actions,
            session_denied=self.session_denied_external_actions,
            always_allowed=self.allowed_external_actions,
            always_denied=self.denied_external_actions,
            denied_message=f"External action denied: {action_scope}",
        )

    async def ensureExternalAction(
        self,
        action: str,
        payload: Any,
        *,
        risk: RiskLevel = "high",
        reason: str,
        scope: str | None = None,
        details: list[str] | None = None,
    ) -> None:
        await self.ensure_external_action(
            action,
            payload,
            risk=risk,
            reason=reason,
            scope=scope,
            details=details,
        )


def get_permissions_path() -> str:
    return str(PERMISSIONS_PATH)


getPermissionsPath = get_permissions_path
