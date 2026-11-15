"""
Split extracted text into overlapping chunks of words.

Why chunks? A RAG system retrieves small pieces of text, not whole documents.

How it works (example: size=5, overlap=2, step = 5 - 2 = 3):

    words:    w0 w1 w2 w3 w4 w5 w6 w7 w8
    chunk 0:  w0 w1 w2 w3 w4
    chunk 1:           w3 w4 w5 w6 w7      <- starts 3 words later, shares 2
    chunk 2:                    w6 w7 w8

A chunk never crosses a page boundary, so every chunk has an exact page
number that can be shown as a citation later. The cost is that a sentence
split across two PDF pages is not joined.
"""

from dataclasses import dataclass

from ingestion import config
from ingestion.extraction import PageText


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    chunk_index: int
    text: str
    page_number: int | None
    word_count: int


def make_chunk_id(document_id: str, chunk_index: int) -> str:
    """
    Build a stable, unique chunk ID.

    Stable: the same document ID and index always give the same ID.
    Unique: document IDs are random UUIDs and indexes never repeat in one document.
    """
    return f"{document_id}_chunk_{chunk_index:04d}"


def chunk_pages(
    document_id: str,
    pages: list[PageText],
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    """
    Split pages into chunks of about `chunk_size` words with `overlap` shared words.

    chunk_index counts upward across the whole document (0, 1, 2, ...), so
    the order of chunks matches the reading order of the document.
    Empty chunks are never produced.
    """
    chunk_size = config.CHUNK_SIZE_WORDS if chunk_size is None else chunk_size
    overlap = config.CHUNK_OVERLAP_WORDS if overlap is None else overlap

    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    if overlap < 0:
        raise ValueError("overlap cannot be negative")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    step = chunk_size - overlap
    chunks: list[Chunk] = []

    for page in pages:
        words = page.text.split()  # splits on any whitespace, drops empty strings
        start = 0
        while start < len(words):
            window = words[start : start + chunk_size]
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(document_id, len(chunks)),
                    document_id=document_id,
                    chunk_index=len(chunks),
                    text=" ".join(window),
                    page_number=page.page_number,
                    word_count=len(window),
                )
            )
            if start + chunk_size >= len(words):
                break  # this window already reached the end of the page
            start += step

    return chunks
