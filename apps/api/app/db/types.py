from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator, UserDefinedType


class PgVectorType(UserDefinedType[object]):
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_kw: object) -> str:
        return f"vector({self.dimensions})"


class EmbeddingVector(TypeDecorator[list[float]]):
    """Store embeddings as pgvector on Postgres and text in local SQLite tests."""

    impl = Text
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        super().__init__()
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect: Any) -> object:
        if dialect.name == "postgresql":
            return PgVectorType(self.dimensions)
        return dialect.type_descriptor(Text())

    def process_bind_param(
        self, value: Iterable[float] | str | None, dialect: Any
    ) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return vector_literal(value)

    def process_result_value(self, value: object, dialect: Any) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, list):
            return [float(item) for item in value]
        if isinstance(value, tuple):
            return [float(item) for item in value]
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = json.loads(stripped.replace(" ", ","))
            return [float(item) for item in parsed]
        return [float(item) for item in value]  # type: ignore[arg-type]

    def copy(self, **_kw: object) -> "EmbeddingVector":
        return EmbeddingVector(self.dimensions)


def vector_literal(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"
