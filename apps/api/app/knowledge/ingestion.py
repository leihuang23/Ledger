from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.knowledge.embeddings import embed_text, tokenize
from app.models import KnowledgeDocument, KnowledgeDocumentChunk

DOCS_DIR = Path(__file__).resolve().parent / "docs"
SOURCE_ROOT = DOCS_DIR.parent.parent.parent
BUILTIN_SOURCE_PATH_PREFIX = "app/knowledge/docs/"
KNOWLEDGE_INGESTED_AT = datetime(2026, 6, 9, 12, 0, 0)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class MarkdownChunk:
    chunk_index: int
    heading_path: str
    content: str
    token_count: int


@dataclass(frozen=True)
class BuiltinDocument:
    path: Path
    source_id: str
    source_path: str
    checksum: str
    markdown: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class KnowledgeIngestionResult:
    document_count: int
    chunk_count: int
    source_ids: list[str]


@dataclass(frozen=True)
class MarkdownSection:
    heading_path: str
    content: str


def chunk_markdown(
    markdown: str, *, max_chars: int = 950
) -> tuple[dict[str, Any], list[MarkdownChunk]]:
    metadata, body = parse_front_matter(markdown)
    sections = markdown_sections(body, fallback_title=str(metadata.get("title") or "Untitled"))
    chunks: list[MarkdownChunk] = []

    for section in sections:
        for piece in split_section(section.content, max_chars=max_chars):
            content = piece.strip()
            if not content:
                continue
            chunks.append(
                MarkdownChunk(
                    chunk_index=len(chunks),
                    heading_path=section.heading_path,
                    content=content,
                    token_count=len(tokenize(content)),
                )
            )

    return metadata, chunks


def parse_front_matter(markdown: str) -> tuple[dict[str, Any], str]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, markdown

    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            metadata_lines = lines[1:index]
            body = "\n".join(lines[index + 1 :]).strip()
            return parse_metadata_lines(metadata_lines), body

    return {}, markdown


def parse_metadata_lines(lines: list[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, raw_value = line.split(":", maxsplit=1)
        value = raw_value.strip()
        if key.strip() == "tags":
            metadata["tags"] = [tag.strip() for tag in value.split(",") if tag.strip()]
        else:
            metadata[key.strip()] = value
    return metadata


def markdown_sections(body: str, *, fallback_title: str) -> list[MarkdownSection]:
    sections: list[MarkdownSection] = []
    heading_stack: list[str] = []
    current_lines: list[str] = []
    current_heading_path = fallback_title

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append(
                MarkdownSection(heading_path=current_heading_path, content=content)
            )

    for line in body.splitlines():
        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            del heading_stack[level - 1 :]
            heading_stack.append(title)
            current_heading_path = " > ".join(heading_stack)
            current_lines = [line]
            continue

        current_lines.append(line)

    flush()
    return sections


def split_section(content: str, *, max_chars: int) -> list[str]:
    if len(content) <= max_chars:
        return [content]

    pieces: list[str] = []
    current: list[str] = []
    current_length = 0
    for paragraph in re.split(r"\n{2,}", content):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        paragraph_length = len(paragraph)
        if current and current_length + paragraph_length + 2 > max_chars:
            pieces.append("\n\n".join(current))
            current = []
            current_length = 0
        if paragraph_length > max_chars:
            pieces.extend(split_long_paragraph(paragraph, max_chars=max_chars))
            continue
        current.append(paragraph)
        current_length += paragraph_length + 2

    if current:
        pieces.append("\n\n".join(current))
    return pieces


def split_long_paragraph(paragraph: str, *, max_chars: int) -> list[str]:
    words = paragraph.split()
    pieces: list[str] = []
    current: list[str] = []
    current_length = 0
    for word in words:
        if current and current_length + len(word) + 1 > max_chars:
            pieces.append(" ".join(current))
            current = []
            current_length = 0
        current.append(word)
        current_length += len(word) + 1
    if current:
        pieces.append(" ".join(current))
    return pieces


def builtin_document_paths() -> list[Path]:
    return sorted(DOCS_DIR.glob("*.md"))


def builtin_documents() -> list[BuiltinDocument]:
    documents: list[BuiltinDocument] = []
    seen_source_ids: set[str] = set()
    for path in builtin_document_paths():
        markdown = path.read_text(encoding="utf-8")
        metadata, _body = parse_front_matter(markdown)
        source_id = required_metadata(metadata, "source_id", path)
        if source_id in seen_source_ids:
            raise ValueError(f"Duplicate built-in knowledge source_id: {source_id}")
        seen_source_ids.add(source_id)
        documents.append(
            BuiltinDocument(
                path=path,
                source_id=source_id,
                source_path=str(path.relative_to(SOURCE_ROOT)),
                checksum=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                markdown=markdown,
                metadata=metadata,
            )
        )
    return documents


def ingest_builtin_knowledge_documents(
    session: Session, *, force: bool = True, commit: bool = True
) -> KnowledgeIngestionResult:
    documents = builtin_documents()
    if not force and builtin_documents_are_current(session, documents):
        return current_ingestion_result(session)

    prune_removed_builtin_documents(session, documents)
    for document in documents:
        ingest_builtin_document(session, document)

    session.flush()
    if commit:
        session.commit()

    return current_ingestion_result(session)


def current_ingestion_result(session: Session) -> KnowledgeIngestionResult:
    return KnowledgeIngestionResult(
        document_count=session.scalar(select(func.count()).select_from(KnowledgeDocument))
        or 0,
        chunk_count=session.scalar(select(func.count()).select_from(KnowledgeDocumentChunk))
        or 0,
        source_ids=[
            source_id
            for (source_id,) in session.execute(
                select(KnowledgeDocument.id).order_by(KnowledgeDocument.id)
            )
        ],
    )


def builtin_documents_are_current(
    session: Session, documents: list[BuiltinDocument]
) -> bool:
    expected = {
        document.source_id: (document.checksum, document.source_path)
        for document in documents
    }
    existing = {
        row.id: (row.checksum, row.source_path)
        for row in session.execute(
            select(
                KnowledgeDocument.id,
                KnowledgeDocument.checksum,
                KnowledgeDocument.source_path,
            ).where(KnowledgeDocument.source_path.like(f"{BUILTIN_SOURCE_PATH_PREFIX}%"))
        )
    }
    if existing != expected:
        return False

    chunk_document_ids = {
        document_id
        for (document_id,) in session.execute(
            select(KnowledgeDocumentChunk.document_id)
            .where(KnowledgeDocumentChunk.document_id.in_(expected))
            .distinct()
        )
    }
    return chunk_document_ids == set(expected)


def prune_removed_builtin_documents(
    session: Session, documents: list[BuiltinDocument]
) -> None:
    current_ids = {document.source_id for document in documents}
    stale_ids = [
        source_id
        for (source_id,) in session.execute(
            select(KnowledgeDocument.id).where(
                KnowledgeDocument.source_path.like(f"{BUILTIN_SOURCE_PATH_PREFIX}%"),
                ~KnowledgeDocument.id.in_(current_ids),
            )
        )
    ]
    if not stale_ids:
        return

    session.execute(
        delete(KnowledgeDocumentChunk).where(
            KnowledgeDocumentChunk.document_id.in_(stale_ids)
        )
    )
    session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(stale_ids)))


def ingest_markdown_file(session: Session, path: Path) -> None:
    markdown = path.read_text(encoding="utf-8")
    metadata, chunks = chunk_markdown(markdown)
    source_id = required_metadata(metadata, "source_id", path)
    source_path = str(path.relative_to(SOURCE_ROOT))
    checksum = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    ingest_markdown_document(
        session,
        source_id=source_id,
        source_path=source_path,
        checksum=checksum,
        markdown=markdown,
        metadata=metadata,
        chunks=chunks,
    )


def ingest_builtin_document(session: Session, document: BuiltinDocument) -> None:
    _metadata, chunks = chunk_markdown(document.markdown)
    ingest_markdown_document(
        session,
        source_id=document.source_id,
        source_path=document.source_path,
        checksum=document.checksum,
        markdown=document.markdown,
        metadata=document.metadata,
        chunks=chunks,
    )


def ingest_markdown_document(
    session: Session,
    *,
    source_id: str,
    source_path: str,
    checksum: str,
    markdown: str,
    metadata: dict[str, Any],
    chunks: list[MarkdownChunk],
) -> None:
    title = required_metadata(metadata, "title", source_path)

    document = session.get(KnowledgeDocument, source_id)
    if document is None:
        document = KnowledgeDocument(
            id=source_id,
            title=title,
            document_type=str(metadata.get("document_type") or "internal_doc"),
            owner=metadata.get("owner"),
            source_path=source_path,
            source_uri=metadata.get("source_uri"),
            checksum=checksum,
            document_metadata=metadata,
            content=markdown,
            created_at=KNOWLEDGE_INGESTED_AT,
            updated_at=KNOWLEDGE_INGESTED_AT,
        )
        session.add(document)
    else:
        document.title = title
        document.document_type = str(metadata.get("document_type") or "internal_doc")
        document.owner = metadata.get("owner")
        document.source_path = source_path
        document.source_uri = metadata.get("source_uri")
        document.checksum = checksum
        document.document_metadata = metadata
        document.content = markdown
        document.updated_at = KNOWLEDGE_INGESTED_AT

    session.execute(
        delete(KnowledgeDocumentChunk).where(KnowledgeDocumentChunk.document_id == source_id)
    )
    session.flush()

    for chunk in chunks:
        chunk_id = f"{source_id}#chunk-{chunk.chunk_index:03d}"
        citation_metadata = {
            "source_id": source_id,
            "chunk_id": chunk_id,
            "title": title,
            "document_type": document.document_type,
            "heading_path": chunk.heading_path,
            "source_path": source_path,
            "source_uri": document.source_uri,
            "chunk_index": chunk.chunk_index,
            "tags": metadata.get("tags", []),
        }
        session.add(
            KnowledgeDocumentChunk(
                id=chunk_id,
                document_id=source_id,
                chunk_index=chunk.chunk_index,
                heading_path=chunk.heading_path,
                content=chunk.content,
                token_count=chunk.token_count,
                embedding=embed_text(f"{title}\n{chunk.heading_path}\n{chunk.content}"),
                citation_metadata=citation_metadata,
                created_at=KNOWLEDGE_INGESTED_AT,
            )
        )


def required_metadata(metadata: dict[str, Any], key: str, path: Path | str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} is missing required metadata: {key}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest built-in knowledge Markdown docs.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args()

    with SessionLocal() as session:
        result = ingest_builtin_knowledge_documents(session)

    payload = {
        "document_count": result.document_count,
        "chunk_count": result.chunk_count,
        "source_ids": result.source_ids,
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("Ingested built-in knowledge documents")
        print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
