from __future__ import annotations

import re


class MemoryPolicyError(ValueError):
    pass


_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S{12,}")),
    ("provider_key", re.compile(r"\b(?:sk-(?:ant|proj)-|gh[pousr]_)[A-Za-z0-9_-]{16,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|client[_-]?secret)"
            r"\s*[:=]\s*[\"']?[^\s\"']{12,}"
        ),
    ),
    (
        "credential_url",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
    ),
)


def detect_secret(content: str) -> str | None:
    text = str(content or "")
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return name
    return None


def validate_memory_content(content: str, *, max_chars: int = 8_000) -> str:
    text = str(content or "").strip()
    if not text:
        raise MemoryPolicyError("memory content is empty")
    if len(text) > max_chars:
        raise MemoryPolicyError(f"memory content exceeds {max_chars} characters")
    secret_type = detect_secret(text)
    if secret_type:
        raise MemoryPolicyError(f"memory contains forbidden secret material: {secret_type}")
    return text
