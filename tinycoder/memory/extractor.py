from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .models import MemoryKind, MemoryScope


EXTRACTOR_VERSION = "rules-v1"


@dataclass(frozen=True)
class MemoryCandidate:
    scope: MemoryScope
    kind: MemoryKind
    canonical_key: str
    content: str
    confidence: float
    explicit: bool
    evidence_type: str
    source_uri: str


class MemoryExtractor(Protocol):
    version: str

    def extract(
        self,
        messages: list[dict[str, Any]],
        *,
        max_candidates: int,
    ) -> list[MemoryCandidate]: ...


def _key(prefix: str, content: str) -> str:
    normalized = " ".join(content.casefold().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}.{digest}"


def _latest_completed_turn(
    messages: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    terminal_entry = next(
        (
            (index, messages[index])
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].get("role") in {"assistant", "assistant_progress"}
        ),
        None,
    )
    if not terminal_entry:
        return None
    terminal_index, terminal = terminal_entry
    if not terminal or terminal.get("role") != "assistant":
        return None
    if terminal.get("stopReason"):
        return None
    assistant_text = str(terminal.get("content") or "").strip()
    if not assistant_text or assistant_text == "已中断当前模型输出。":
        return None
    for message in reversed(messages[:terminal_index]):
        if message.get("role") == "user" and not message.get("synthetic"):
            user_text = str(message.get("content") or "").strip()
            if user_text:
                return user_text, terminal
    return None


class RuleBasedMemoryExtractor:
    version = EXTRACTOR_VERSION

    _explicit_patterns = (
        re.compile(
            r"(?:please\s+)?remember(?:\s+that)?\s*[:：]?\s*(.+)",
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            r"(?:请)?记住(?:这(?:一点|件事))?\s*[:：]?\s*(.+)",
            re.DOTALL,
        ),
    )
    _implicit_preferences = (
        re.compile(r"\bI\s+prefer\s+(.+)", re.IGNORECASE | re.DOTALL),
        re.compile(r"我(?:更)?(?:喜欢|偏好)\s*[:：]?\s*(.+)", re.DOTALL),
    )

    def __init__(self, *, default_scope: str = "project_local") -> None:
        self.default_scope: MemoryScope = default_scope  # type: ignore[assignment]

    @staticmethod
    def _scope_for(text: str, default_scope: MemoryScope) -> MemoryScope:
        if re.search(
            r"\b(?:all projects|globally|across projects)\b|所有项目|全局",
            text,
            re.IGNORECASE,
        ):
            return "user"
        return default_scope

    @staticmethod
    def _kind_for(text: str) -> MemoryKind:
        if re.search(
            r"\b(?:prefer|preference|always|never)\b|喜欢|偏好|始终|不要",
            text,
            re.IGNORECASE,
        ):
            return "preference"
        return "fact"

    def extract(
        self,
        messages: list[dict[str, Any]],
        *,
        max_candidates: int,
    ) -> list[MemoryCandidate]:
        completed = _latest_completed_turn(messages)
        if not completed:
            return []
        user_text, _ = completed
        candidates: list[MemoryCandidate] = []
        for pattern in self._explicit_patterns:
            match = pattern.search(user_text)
            if not match:
                continue
            content = match.group(1).strip().rstrip()
            if content:
                kind = self._kind_for(content)
                prefix = "user.preference" if kind == "preference" else "project.fact"
                candidates.append(
                    MemoryCandidate(
                        scope=self._scope_for(user_text, self.default_scope),
                        kind=kind,
                        canonical_key=_key(prefix, content),
                        content=content,
                        confidence=0.98,
                        explicit=True,
                        evidence_type="explicit_user_directive",
                        source_uri="conversation:user",
                    )
                )
            break
        if not candidates:
            for pattern in self._implicit_preferences:
                match = pattern.search(user_text)
                if not match:
                    continue
                content = match.group(0).strip()
                candidates.append(
                    MemoryCandidate(
                        scope=self._scope_for(user_text, self.default_scope),
                        kind="preference",
                        canonical_key=_key("user.preference", content),
                        content=content,
                        confidence=0.78,
                        explicit=False,
                        evidence_type="user_preference_statement",
                        source_uri="conversation:user",
                    )
                )
                break
        successful_results = {
            message.get("toolUseId"): message
            for message in messages
            if message.get("role") == "tool_result" and not message.get("isError")
        }
        for message in reversed(messages):
            if message.get("role") != "assistant_tool_call":
                continue
            tool_use_id = message.get("toolUseId")
            if tool_use_id not in successful_results or message.get("toolName") != "run_command":
                continue
            input_value = message.get("input")
            if not isinstance(input_value, dict):
                continue
            command = str(input_value.get("command") or "").strip()
            args = input_value.get("args") or []
            rendered = " ".join([command, *[str(value) for value in args]]).strip()
            if not re.search(
                r"(?:^|\s)(?:pytest|unittest|test|tests)(?:\s|$)|npm\s+(?:run\s+)?test",
                rendered,
                re.IGNORECASE,
            ):
                continue
            content = f"Verified test command: {rendered}"
            candidates.append(
                MemoryCandidate(
                    scope=self.default_scope,
                    kind="procedure",
                    canonical_key="project.test.command",
                    content=content,
                    confidence=0.9,
                    explicit=False,
                    evidence_type="verified_tool_success",
                    source_uri=f"tool:run_command:{tool_use_id}",
                )
            )
            break
        return candidates[: max(1, int(max_candidates))]
