from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


class EmbeddingProvider(Protocol):
    name: str
    dimensions: int
    is_external: bool

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


class LocalHashEmbeddingProvider:
    """Deterministic, dependency-free local embedding fallback.

    This provider keeps all text on-device. It is intentionally lightweight;
    callers can supply a stronger provider through the same interface.
    """

    name = "local-hash-v1"
    is_external = False

    def __init__(self, *, dimensions: int = 256) -> None:
        self.dimensions = max(32, min(int(dimensions), 2_048))

    @staticmethod
    def _tokens(text: str) -> list[str]:
        normalized = text.casefold()
        words = re.findall(r"[\w.-]{2,}", normalized)
        chinese = [
            normalized[index : index + 2]
            for index in range(max(0, len(normalized) - 1))
            if "\u4e00" <= normalized[index] <= "\u9fff"
            and "\u4e00" <= normalized[index + 1] <= "\u9fff"
        ]
        return [*words, *chinese]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in self._tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(str(text or "")) for text in texts]
