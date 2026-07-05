from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.cache import Cache
from app.db.types import vector_literal
from app.knowledge.embeddings import (
    EmbeddingProvider,
    cosine_similarity,
    embed_text,
    get_embedding_provider,
    tokenize,
)
from app.models import KnowledgeDocument, KnowledgeDocumentChunk


@dataclass(frozen=True)
class KnowledgeSearchResult:
    source_id: str
    title: str
    snippet: str
    score: float
    citation: dict[str, Any]


DASHBOARD_CACHE_TTL_SECONDS = 60
KNOWLEDGE_CACHE_TTL_SECONDS = 300


def _search_knowledge_cache_key(query: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", query.strip()).lower()
    return f"knowledge:search:{normalized}:{limit}"


def search_knowledge(
    session: Session,
    query: str,
    *,
    limit: int = 6,
    provider: EmbeddingProvider | None = None,
) -> list[KnowledgeSearchResult]:
    query = query.strip()
    if not query:
        return []

    if provider is None:
        provider = get_embedding_provider()

    cache = Cache()
    cache_key = _search_knowledge_cache_key(query, limit)
    cached = cache.get(cache_key)
    if cached is not None:
        return [
            KnowledgeSearchResult(
                source_id=str(item["source_id"]),
                title=str(item["title"]),
                snippet=str(item["snippet"]),
                score=float(item["score"]),
                citation=dict(item["citation"]),
            )
            for item in cached["results"]
        ]

    query_embedding = provider.embed([query])[0]
    if session.get_bind().dialect.name == "postgresql":
        results = search_postgres(session, query, query_embedding, limit=limit)
    else:
        results = search_in_memory(session, query, query_embedding, limit=limit)

    cache.set(
        cache_key,
        {
            "results": [
                {
                    "source_id": result.source_id,
                    "title": result.title,
                    "snippet": result.snippet,
                    "score": result.score,
                    "citation": result.citation,
                }
                for result in results
            ]
        },
        KNOWLEDGE_CACHE_TTL_SECONDS,
    )
    return results


def search_postgres(
    session: Session, query: str, query_embedding: list[float], *, limit: int
) -> list[KnowledgeSearchResult]:
    rows = session.execute(
        text(
            """
            SELECT
                d.id AS source_id,
                d.title AS title,
                c.content AS content,
                c.citation_metadata AS citation,
                1 - (c.embedding <=> CAST(:embedding AS vector)) AS score
            FROM knowledge_document_chunks c
            JOIN knowledge_documents d ON d.id = c.document_id
            ORDER BY c.embedding <=> CAST(:embedding AS vector), c.id
            LIMIT :limit
            """
        ),
        {"embedding": vector_literal(query_embedding), "limit": limit},
    ).mappings()

    return [
        KnowledgeSearchResult(
            source_id=str(row["source_id"]),
            title=str(row["title"]),
            snippet=build_snippet(str(row["content"]), query),
            score=round(float(row["score"] or 0), 6),
            citation=dict(row["citation"] or {}),
        )
        for row in rows
    ]


def search_in_memory(
    session: Session, query: str, query_embedding: list[float], *, limit: int
) -> list[KnowledgeSearchResult]:
    rows = session.execute(
        select(KnowledgeDocument, KnowledgeDocumentChunk)
        .join(
            KnowledgeDocumentChunk,
            KnowledgeDocumentChunk.document_id == KnowledgeDocument.id,
        )
        .order_by(KnowledgeDocumentChunk.id)
    ).all()
    query_tokens = set(tokenize(query))

    ranked: list[KnowledgeSearchResult] = []
    for document, chunk in rows:
        semantic_score = cosine_similarity(query_embedding, chunk.embedding)
        lexical_score = lexical_overlap(query_tokens, chunk.content, document.title)
        ranked.append(
            KnowledgeSearchResult(
                source_id=document.id,
                title=document.title,
                snippet=build_snippet(chunk.content, query),
                score=round(semantic_score + lexical_score, 6),
                citation=chunk.citation_metadata,
            )
        )

    ranked.sort(key=lambda result: (-result.score, result.source_id, result.citation["chunk_id"]))
    return ranked[:limit]


def lexical_overlap(query_tokens: set[str], content: str, title: str) -> float:
    if not query_tokens:
        return 0.0
    haystack_tokens = set(tokenize(f"{title} {content}"))
    overlap = len(query_tokens & haystack_tokens)
    return overlap / len(query_tokens)


def build_snippet(content: str, query: str, *, max_chars: int = 280) -> str:
    compact = re.sub(r"\s+", " ", content).strip()
    if len(compact) <= max_chars:
        return compact

    query_terms = [re.escape(token) for token in tokenize(query) if len(token) > 2]
    if query_terms:
        match = re.search("|".join(query_terms), compact, flags=re.IGNORECASE)
        if match:
            start = max(match.start() - max_chars // 3, 0)
            end = min(start + max_chars, len(compact))
            snippet = compact[start:end].strip()
            return f"{'...' if start > 0 else ''}{snippet}{'...' if end < len(compact) else ''}"

    return compact[: max_chars - 3].rstrip() + "..."
