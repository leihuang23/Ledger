from __future__ import annotations

import hashlib
import logging
import math
import random
import re
from collections.abc import Iterable
from typing import Protocol

import httpx

from app.models import KNOWLEDGE_EMBEDDING_DIMENSIONS

TOKEN_RE = re.compile(r"[a-z0-9]+")
logger = logging.getLogger(__name__)

OPENAI_EMBEDDING_URL = "https://api.openai.com/v1/embeddings"
OPENAI_EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


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


class EmbeddingProvider(Protocol):
    """Protocol for text embedding implementations."""

    @property
    def dimensions(self) -> int:
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class LocalHashingEmbeddings:
    """Deterministic hashing-based embeddings used by default.

    Requires no external API keys and is fully reproducible across runs.
    """

    @property
    def dimensions(self) -> int:
        return KNOWLEDGE_EMBEDDING_DIMENSIONS

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [embed_text(text) for text in texts]


class OpenAIEmbeddings:
    """OpenAI API-backed embeddings projected to the configured dimension."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        output_dimensions: int = KNOWLEDGE_EMBEDDING_DIMENSIONS,
        timeout_seconds: int = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider = "openai"
        self.model = model
        self._api_key = api_key
        self._output_dimensions = output_dimensions
        self._timeout = timeout_seconds
        self._transport = transport
        self._input_dimensions = OPENAI_EMBEDDING_DIMENSIONS.get(model, 1536)

    @property
    def dimensions(self) -> int:
        return self._output_dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        payload: dict[str, object] = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }
        # OpenAI supports native dimension reduction for v3 models; request the
        # smallest valid dimension and project the rest locally.
        if self.model.startswith("text-embedding-3"):
            min_dimension = 512 if self.model == "text-embedding-3-small" else 256
            payload["dimensions"] = max(min_dimension, self._output_dimensions)

        with httpx.Client(transport=self._transport) as client:
            response = client.post(
                OPENAI_EMBEDDING_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            raw = response.json()

        ordered: list[list[float]] = [[] for _ in texts]
        for item in raw["data"]:
            ordered[item["index"]] = item["embedding"]

        return [_project_to_dimensions(vector, self._output_dimensions) for vector in ordered]


def _project_to_dimensions(vector: list[float], dimensions: int) -> list[float]:
    """Project a high-dimensional vector down to a fixed output dimension.

    Uses a deterministic random projection seeded from the vector so results
    are reproducible without storing a large matrix. The projected vector is
    L2-normalized to remain compatible with cosine-similarity search.
    """
    if len(vector) == dimensions:
        return _normalize(vector)

    rng = random.Random(hashlib.sha256(str(vector[:8]).encode("utf-8")).hexdigest())
    projected: list[float] = [0.0] * dimensions
    for value in vector:
        for dim_index in range(dimensions):
            projected[dim_index] += value * (rng.random() * 2 - 1)

    return _normalize(projected)


def _normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


def get_embedding_provider(
    settings: "app.core.config.Settings" | None = None,
) -> EmbeddingProvider:
    """Return the configured embedding provider.

    Falls back to the local hashing provider when OpenAI is requested but no
    API key is configured, so default behavior is unchanged without credentials.
    """
    from app.core.config import Settings, get_settings

    resolved: Settings = settings or get_settings()
    if resolved.embedding_provider == "openai" and resolved.openai_api_key:
        return OpenAIEmbeddings(
            api_key=resolved.openai_api_key,
            model=resolved.openai_embedding_model,
        )

    if resolved.embedding_provider == "openai" and not resolved.openai_api_key:
        logger.warning(
            "EMBEDDING_PROVIDER=openai requested but OPENAI_API_KEY is empty; "
            "falling back to local hashing embeddings."
        )

    return LocalHashingEmbeddings()
