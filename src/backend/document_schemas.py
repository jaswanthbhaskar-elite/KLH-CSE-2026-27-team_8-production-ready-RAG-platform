"""Pydantic response models for the /documents endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    document_id: str = Field(description="Unique document ID (UUID).")
    filename: str = Field(description="Sanitized filename (safe to display).")
    file_type: Literal["pdf", "txt"]
    knowledge_scope: str = Field(description="The one knowledge scope this document belongs to.")
    uploaded_by: str
    uploaded_at: str = Field(description="UTC upload time, ISO 8601.")
    page_count: int | None = Field(
        default=None, description="Number of pages for PDFs. null for TXT."
    )
    chunk_count: int
    word_count: int = Field(description="Words in the extracted text (overlap not double counted).")
    file_size_bytes: int
    status: str = Field(description="'ready' = extracted, chunked and stored.")


class ChunkInfo(BaseModel):
    chunk_id: str
    chunk_index: int
    page_number: int | None = Field(
        default=None, description="Source page for PDFs. null for TXT."
    )
    word_count: int
    text_preview: str = Field(description="First 200 characters of the chunk.")


class UploadResponse(BaseModel):
    status: str = Field(description="Document status. 'ready' after a successful upload.")
    document_id: str
    filename: str = Field(description="Sanitized filename. May differ from the uploaded name.")
    file_type: Literal["pdf", "txt"]
    knowledge_scope: str
    page_count: int | None = None
    chunk_count: int
    trace_id: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentMetadata]
    total: int
    trace_id: str


class DocumentDetailResponse(BaseModel):
    document: DocumentMetadata
    chunks: list[ChunkInfo]
    trace_id: str


class DeleteResponse(BaseModel):
    status: Literal["deleted"]
    document_id: str
    chunks_deleted: int
    trace_id: str


class DocumentErrorResponse(BaseModel):
    """Shape of every error from /documents endpoints."""

    status: Literal["refused", "rejected", "not_found", "error"] = Field(
        description="refused = not authorized; rejected = bad input or file; "
        "not_found = no such document; error = server problem."
    )
    error_code: str = Field(description="Short machine-readable reason, e.g. 'file_too_large'.")
    reason: str = Field(description="Human-readable explanation. Never contains internals.")
    trace_id: str
