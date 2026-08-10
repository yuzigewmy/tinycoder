"""Followup suggestion generator - mirrors qwen-code suggestionGenerator.ts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

_PROMPT_PATH = Path(__file__).parent / "followup_prompt.txt"
SUGGESTION_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

MIN_ASSISTANT_TURNS = 2

ALLOWED_SINGLE_WORDS = frozenset({
    "yes", "yeah", "yep", "yea", "yup", "sure", "ok", "okay",
    "push", "commit", "deploy", "stop", "continue", "check",
    "exit", "quit", "no",
})

KNOWN_ABBREVIATIONS = frozenset({
    "Mr", "Mrs", "Dr", "Ms", "Prof", "Sr", "Jr", "St", "vs", "etc",
})

SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s+[A-Z]")


def _has_sentence_boundary(suggestion: str) -> bool:
    for m in SENTENCE_BOUNDARY_RE.finditer(suggestion):
        i = m.start()
        before = suggestion[:i]
        word_match = re.search(r"(\w+)$", before)
        if not word_match:
            return True
        word = word_match.group(1)
        if word in KNOWN_ABBREVIATIONS:
            continue
        if (word == "g" and before.rstrip().lower().endswith("e.g")) or \
           (word == "e" and before.rstrip().lower().endswith("i.e")):
            continue
        return True
    return False


def get_filter_reason(suggestion: str) -> Optional[str]:
    """Mirrors qwen-code getFilterReason."""
    lower = suggestion.lower()
    word_count = len(suggestion.strip().split())

    if re.search(r"[\u0000-\u001f\u007f-\u009f]", suggestion):
        return "control_chars"

    if lower == "done":
        return "done"
    if lower in ("nothing found", "nothing found."):
        return "meta_text"
    if lower.startswith("nothing to suggest") or lower.startswith("no suggestion"):
        return "meta_text"
    if re.search(r"\bsilence is\b|\bstay(s|ing)? silent\b", lower):
        return "meta_text"
    if re.match(r"^\W*silence\W*$", lower):
        return "meta_text"

    if re.match(r"^\(.*\)$|^\[.*\]$", suggestion):
        return "meta_wrapped"

    if lower.startswith(("api error:", "prompt is too long", "request timed out",
                         "invalid api key", "image was too large")):
        return "error_message"

    if re.match(r"^\w+:\s", suggestion):
        return "prefixed_label"

    has_cjk = bool(re.search(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]", suggestion))
    if not has_cjk:
        if word_count < 2:
            if not suggestion.startswith("/") and lower not in ALLOWED_SINGLE_WORDS:
                return "too_few_words"
        if word_count > 12:
            return "too_many_words"
    else:
        if len(suggestion) < 2:
            return "too_few_words"
        if len(suggestion) > 30:
            return "too_many_words"

    if len(suggestion) >= 100:
        return "too_long"
    if _has_sentence_boundary(suggestion):
        return "multiple_sentences"
    if re.search(r"[\n*]|\*\*", suggestion):
        return "has_formatting"

    if re.search(
        r"\bthanks\b|\bthank you\b|\blooks good\b|\bsounds good\b|"
        r"\bthat works\b|\bthat worked\b|\bthat\\'s all\b|"
        r"\bnice\b|\bgreat\b|\bperfect\b|"
        r"\bmakes sense\b|\bawesome\b|\bexcellent\b",
        lower,
    ):
        return "evaluative"

    if re.match(
        r"^(let me|i\\'ll|i\\'ve|i\\'m|i can|i would|i think|i notice|"
        r"here\\'s|here is|here are|that\\'s|this is|this will|"
        r"you can|you should|you could|sure,|of course|certainly)",
        suggestion,
        re.IGNORECASE,
    ):
        return "ai_voice"

    return None


def should_filter(suggestion: str) -> bool:
    return get_filter_reason(suggestion) is not None


def count_assistant_turns(messages: list[dict[str, Any]]) -> int:
    return sum(1 for m in messages if m.get("role") in ("assistant", "assistant_progress"))


async def generate_suggestion(
    model: Any,
    messages: list[dict[str, Any]],
) -> Optional[str]:
    """Generate a followup suggestion via a lightweight model call."""
    if count_assistant_turns(messages) < MIN_ASSISTANT_TURNS:
        return None

    try:
        query_messages = list(messages[-8:])
        query_messages.append({"role": "user", "content": SUGGESTION_PROMPT})

        raw = await model.next(query_messages, max_tokens=30)
        if not raw or raw.get("type") != "assistant":
            return None

        suggestion = str(raw.get("content") or "").strip()
        if not suggestion:
            return None

        if (suggestion.startswith('"') and suggestion.endswith('"')) or \
           (suggestion.startswith("\\'") and suggestion.endswith("\\'")):
            suggestion = suggestion[1:-1]

        if get_filter_reason(suggestion):
            return None

        return suggestion
    except Exception:
        return None
