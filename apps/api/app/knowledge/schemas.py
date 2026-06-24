from __future__ import annotations

from pydantic import BaseModel, Field


class KnowledgeIngestionResponse(BaseModel):
    document_count: int
    chunk_count: int
    source_ids: list[str]


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=400)
    limit: int = Field(default=6, ge=1, le=20)


class KnowledgeCitation(BaseModel):
    source_id: str
    chunk_id: str
    title: str
    document_type: str
    heading_path: str
    source_path: str
    source_uri: str | None = None
    chunk_index: int
    tags: list[str] = Field(default_factory=list)


class KnowledgeSearchResult(BaseModel):
    source_id: str
    title: str
    snippet: str
    score: float
    citation: KnowledgeCitation


class KnowledgeSearchResponse(BaseModel):
    query: str
    results: list[KnowledgeSearchResult]
