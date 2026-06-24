from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable

from app.models import KNOWLEDGE_EMBEDDING_DIMENSIONS

TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [normalize_token(token) for token in TOKEN_RE.findall(text.lower())]


def normalize_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def embed_text(text: str, dimensions: int = KNOWLEDGE_EMBEDDING_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    tokens = tokenize(text)
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign

    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = list(left)
    right_values = list(right)
    numerator = sum(a * b for a, b in zip(left_values, right_values, strict=False))
    left_magnitude = math.sqrt(sum(value * value for value in left_values))
    right_magnitude = math.sqrt(sum(value * value for value in right_values))
    if left_magnitude == 0 or right_magnitude == 0:
        return 0.0
    return numerator / (left_magnitude * right_magnitude)
