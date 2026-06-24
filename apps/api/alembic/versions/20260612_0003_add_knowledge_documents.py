"""add knowledge documents

Revision ID: 20260612_0003
Revises: 20260611_0002
Create Date: 2026-06-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260612_0003"
down_revision: str | None = "20260611_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 96


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        upgrade_postgres()
        return

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("document_type", sa.String(length=60), nullable=False),
        sa.Column("owner", sa.String(length=120), nullable=True),
        sa.Column("source_path", sa.String(length=240), nullable=False),
        sa.Column("source_uri", sa.String(length=240), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_path"),
    )
    op.create_index(
        op.f("ix_knowledge_documents_document_type"),
        "knowledge_documents",
        ["document_type"],
        unique=False,
    )
    op.create_table(
        "knowledge_document_chunks",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=96), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.String(length=240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=False),
        sa.Column("citation_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_document_chunks_document_id"),
        "knowledge_document_chunks",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_document_chunks_document_index",
        "knowledge_document_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )


def upgrade_postgres() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE knowledge_documents (
            id VARCHAR(96) PRIMARY KEY,
            title VARCHAR(180) NOT NULL,
            document_type VARCHAR(60) NOT NULL,
            owner VARCHAR(120),
            source_path VARCHAR(240) NOT NULL UNIQUE,
            source_uri VARCHAR(240),
            checksum VARCHAR(64) NOT NULL,
            metadata JSONB NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    op.create_index(
        op.f("ix_knowledge_documents_document_type"),
        "knowledge_documents",
        ["document_type"],
        unique=False,
    )
    op.execute(
        f"""
        CREATE TABLE knowledge_document_chunks (
            id VARCHAR(128) PRIMARY KEY,
            document_id VARCHAR(96) NOT NULL
                REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            heading_path VARCHAR(240) NOT NULL,
            content TEXT NOT NULL,
            token_count INTEGER NOT NULL,
            embedding vector({EMBEDDING_DIMENSIONS}) NOT NULL,
            citation_metadata JSONB NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
        """
    )
    op.create_index(
        op.f("ix_knowledge_document_chunks_document_id"),
        "knowledge_document_chunks",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_document_chunks_document_index",
        "knowledge_document_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )
    op.execute(
        """
        CREATE INDEX ix_knowledge_document_chunks_embedding_hnsw
        ON knowledge_document_chunks
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_knowledge_document_chunks_embedding_hnsw")
    op.drop_index(
        "ix_knowledge_document_chunks_document_index",
        table_name="knowledge_document_chunks",
    )
    op.drop_index(
        op.f("ix_knowledge_document_chunks_document_id"),
        table_name="knowledge_document_chunks",
    )
    op.drop_table("knowledge_document_chunks")
    op.drop_index(
        op.f("ix_knowledge_documents_document_type"),
        table_name="knowledge_documents",
    )
    op.drop_table("knowledge_documents")
