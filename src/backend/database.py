"""
SQLite storage for documents and chunks.

- Uses only the standard-library sqlite3 module.
- Every query is parameterized (values are passed separately with "?"),
  so user input is never pasted into SQL text.
- Tables are created automatically (CREATE TABLE IF NOT EXISTS).

The database path comes from the KNOWLEDGE_DB_PATH environment variable and
defaults to backend/data/knowledge.db. Tests set the variable to a temporary
file so they never touch the real database.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ingestion.chunking import Chunk

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "knowledge.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id       TEXT PRIMARY KEY,
    filename          TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_type         TEXT NOT NULL CHECK (file_type IN ('pdf', 'txt')),
    knowledge_scope   TEXT NOT NULL,
    uploaded_by       TEXT NOT NULL,
    uploaded_at       TEXT NOT NULL,
    page_count        INTEGER,
    chunk_count       INTEGER NOT NULL,
    word_count        INTEGER NOT NULL,
    file_size_bytes   INTEGER NOT NULL,
    content_sha256    TEXT NOT NULL,
    status            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL
                 REFERENCES documents(document_id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    page_number  INTEGER,
    word_count   INTEGER NOT NULL,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks (document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_documents_scope ON documents (knowledge_scope);
"""


def get_db_path() -> Path:
    """Read the database path each time, so tests can change it."""
    return Path(os.environ.get("KNOWLEDGE_DB_PATH", str(DEFAULT_DB_PATH)))


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """
    Open a connection, make sure the tables exist, and close it afterwards.

    Everything done inside one `with` block is ONE transaction: it is
    committed if the block finishes and rolled back if an error is raised.
    """
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        # SQLite ignores foreign keys unless this is switched on per connection.
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)  # cheap: does nothing if tables exist
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    """Create the database file and tables. Called when the app starts."""
    with get_connection():
        pass


def _row_to_document(row: sqlite3.Row) -> dict:
    """Convert a documents row to a plain dict."""
    return dict(row)


def insert_document(document: dict, chunks: list[Chunk]) -> None:
    """
    Store one document and all of its chunks in a single transaction.

    Either everything is saved or nothing is, so there are never half-stored
    documents. `document` must contain every column of the documents table.
    """
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO documents (
                document_id, filename, original_filename, file_type,
                knowledge_scope, uploaded_by, uploaded_at, page_count,
                chunk_count, word_count, file_size_bytes, content_sha256, status
            ) VALUES (
                :document_id, :filename, :original_filename, :file_type,
                :knowledge_scope, :uploaded_by, :uploaded_at, :page_count,
                :chunk_count, :word_count, :file_size_bytes, :content_sha256, :status
            )
            """,
            document,
        )
        connection.executemany(
            """
            INSERT INTO chunks (
                chunk_id, document_id, chunk_index, text, page_number, word_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (c.chunk_id, c.document_id, c.chunk_index, c.text, c.page_number, c.word_count)
                for c in chunks
            ],
        )


def list_documents() -> list[dict]:
    """Return metadata for all documents, newest first."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM documents ORDER BY uploaded_at DESC, document_id"
        ).fetchall()
    return [_row_to_document(row) for row in rows]


def get_document(document_id: str) -> dict | None:
    """Return one document's metadata, or None if it does not exist."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()
    return _row_to_document(row) if row else None


def get_chunks(document_id: str) -> list[dict]:
    """Return all chunks of a document in reading order."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (document_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_document(document_id: str) -> int | None:
    """
    Delete a document and its chunks in one transaction.

    Returns the number of chunks deleted, or None if the document does not exist.
    """
    with get_connection() as connection:
        exists = connection.execute(
            "SELECT 1 FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        if exists is None:
            return None

        deleted_chunks = connection.execute(
            "DELETE FROM chunks WHERE document_id = ?", (document_id,)
        ).rowcount
        connection.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
    return deleted_chunks
