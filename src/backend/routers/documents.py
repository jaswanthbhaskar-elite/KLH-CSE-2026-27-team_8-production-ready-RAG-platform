"""
Document management endpoints (admin only in this prototype).

POST   /documents/upload        upload a PDF or TXT file
GET    /documents               list documents
GET    /documents/{document_id} document metadata and chunk information
DELETE /documents/{document_id} delete a document and its chunks

This file only handles HTTP and authorization. The real work lives in:
- ingestion/filenames.py   sanitize the uploaded filename
- ingestion/extraction.py  PDF/TXT -> text
- ingestion/chunking.py    text -> chunks
- database.py              SQLite storage
- governance/*             identity, access control and audit logging
"""

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Path, Query, UploadFile
from fastapi.responses import JSONResponse

import database
from document_schemas import (
    ChunkInfo,
    DeleteResponse,
    DocumentDetailResponse,
    DocumentErrorResponse,
    DocumentListResponse,
    DocumentMetadata,
    UploadResponse,
)
from governance.access_control import check_access
from governance.audit import log_document_event
from governance.identity import check_document_admin
from ingestion import config
from ingestion.chunking import chunk_pages
from ingestion.extraction import ExtractionError, extract_text
from ingestion.filenames import clean_original_name, get_extension, sanitize_filename

logger = logging.getLogger("knowledge_platform")

router = APIRouter(prefix="/documents", tags=["Documents"])

PREVIEW_LENGTH = 200

ERROR_RESPONSES = {
    401: {"model": DocumentErrorResponse, "description": "Unknown user."},
    403: {"model": DocumentErrorResponse, "description": "Known user, but not an admin."},
    404: {"model": DocumentErrorResponse, "description": "Document does not exist."},
    413: {"model": DocumentErrorResponse, "description": "File larger than the upload limit."},
    415: {"model": DocumentErrorResponse, "description": "File type not supported."},
    422: {"model": DocumentErrorResponse, "description": "Invalid input or unreadable file."},
    500: {"model": DocumentErrorResponse, "description": "Unexpected server or database error."},
}

USER_ID_QUERY = Query(
    min_length=1, max_length=64, description="Development user ID. Only admin001 is allowed."
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _error(http_status: int, status: str, error_code: str, reason: str, trace_id: UUID):
    """Build the structured error response used by every /documents endpoint."""
    return JSONResponse(
        status_code=http_status,
        content={
            "status": status,
            "error_code": error_code,
            "reason": reason,
            "trace_id": str(trace_id),
        },
    )


def _format_size(size_bytes: int) -> str:
    """Show a size as MB when large, otherwise as bytes."""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):g} MB"
    return f"{size_bytes} bytes"


def _authorize_admin(action: str, trace_id: UUID, user_id: str, **audit_fields):
    """
    Check that the user is the admin.

    Returns (role, None) when allowed, or (None, error_response) after
    writing a "deny" audit event.
    """
    auth = check_document_admin(user_id)
    if auth["allowed"]:
        return auth["role"], None

    unknown_user = auth["stage"] == "identity"
    http_status = 401 if unknown_user else 403
    log_document_event(
        trace_id=trace_id,
        action=action,
        decision="deny",
        stage=auth["stage"],
        user_id=user_id,
        role=auth["role"],
        http_status=http_status,
        reason=auth["reason"],
        **audit_fields,
    )
    error_code = "unknown_user" if unknown_user else "not_admin"
    return None, _error(http_status, "refused", error_code, auth["reason"], trace_id)


def _storage_error(action: str, trace_id: UUID, user_id: str, role: str, error: Exception, **audit_fields):
    """Log a database failure and return a generic 500. Internals stay in the server log."""
    logger.error("trace_id=%s database error during %s: %s", trace_id, action, type(error).__name__)
    log_document_event(
        trace_id=trace_id,
        action=action,
        decision="error",
        stage="storage",
        user_id=user_id,
        role=role,
        http_status=500,
        reason=f"Database error: {type(error).__name__}",
        **audit_fields,
    )
    return _error(
        500, "error", "storage_error",
        "A storage error occurred. Please try again later.", trace_id,
    )


def _to_metadata(row: dict) -> DocumentMetadata:
    """Convert a database row to the public metadata model (hides internal columns)."""
    return DocumentMetadata(
        document_id=row["document_id"],
        filename=row["filename"],
        file_type=row["file_type"],
        knowledge_scope=row["knowledge_scope"],
        uploaded_by=row["uploaded_by"],
        uploaded_at=row["uploaded_at"],
        page_count=row["page_count"],
        chunk_count=row["chunk_count"],
        word_count=row["word_count"],
        file_size_bytes=row["file_size_bytes"],
        status=row["status"],
    )


# ---------------------------------------------------------------------------
# POST /documents/upload
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=201,
    summary="Upload a PDF or TXT document",
    description=(
        "Admin only. Checks identity, role and knowledge scope, then validates the "
        "file type and size, extracts the text, splits it into chunks and stores "
        "everything in SQLite. The file itself is processed in memory and never "
        "written to disk. The uploaded filename is sanitized and never used as a path. "
        "Scanned PDFs are rejected because OCR is not supported."
    ),
    responses=ERROR_RESPONSES,
)
def upload_document(
    file: UploadFile = File(description="A .pdf or .txt file, at most 10 MB."),
    user_id: str = Form(min_length=1, max_length=64, description="Development user ID (admin001)."),
    knowledge_scope: str = Form(
        min_length=1,
        max_length=64,
        description="The one knowledge scope this document belongs to, e.g. course_materials.",
    ),
):
    trace_id = uuid4()
    user_id = user_id.strip()
    knowledge_scope = knowledge_scope.strip()
    filename = sanitize_filename(file.filename)  # safe to store and log
    base = {"user_id": user_id, "filename": filename, "knowledge_scope": knowledge_scope}

    def reject(http_status, status, decision, stage, error_code, reason, **extra):
        """Audit-log a failed upload and build its error response."""
        log_document_event(
            trace_id=trace_id, action="upload", decision=decision, stage=stage,
            http_status=http_status, reason=f"{error_code}: {reason}",
            role=role, **{**base, **extra},
        )
        return _error(http_status, status, error_code, reason, trace_id)

    role = None

    # 1. Identity and admin role
    role, denied = _authorize_admin(
        "upload", trace_id, user_id, filename=filename, knowledge_scope=knowledge_scope
    )
    if denied:
        return denied

    # 2. Knowledge scope, using the existing RBAC logic
    if not knowledge_scope:
        return reject(422, "rejected", "reject", "scope", "invalid_scope",
                      "knowledge_scope must not be blank.")
    access = check_access(role, [knowledge_scope])
    if not access["allowed"]:
        return reject(422, "rejected", "reject", "scope", "invalid_scope", access["reason"])

    # 3. File type, by extension of the SANITIZED name (content type is not trusted)
    file_type = config.ALLOWED_EXTENSIONS.get(get_extension(filename))
    if file_type is None:
        return reject(415, "rejected", "reject", "file", "unsupported_file_type",
                      "Unsupported file type. Only .pdf and .txt files are accepted.")

    # 4. Size: read at most limit + 1 bytes, so a huge file is never fully loaded
    limit = config.MAX_UPLOAD_BYTES
    data = file.file.read(limit + 1)
    if len(data) > limit:
        return reject(413, "rejected", "reject", "file", "file_too_large",
                      f"The file exceeds the maximum upload size of {_format_size(limit)}.",
                      file_type=file_type)
    file_size = len(data)

    # 5. Extract text
    try:
        extraction = extract_text(file_type, data)
    except ExtractionError as error:
        return reject(422, "rejected", "reject", "extraction", error.code, error.message,
                      file_type=file_type, file_size_bytes=file_size)

    # 6. Chunk
    document_id = str(uuid4())
    chunks = chunk_pages(document_id, extraction.pages)
    if not chunks:  # defensive: extraction already guarantees text
        return reject(422, "rejected", "reject", "extraction", "no_text",
                      "The file contains no text.",
                      file_type=file_type, file_size_bytes=file_size)

    # 7. Store document and chunks in one transaction
    document = {
        "document_id": document_id,
        "filename": filename,
        "original_filename": clean_original_name(file.filename),
        "file_type": file_type,
        "knowledge_scope": knowledge_scope,
        "uploaded_by": user_id,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "page_count": extraction.page_count,
        "chunk_count": len(chunks),
        "word_count": sum(len(page.text.split()) for page in extraction.pages),
        "file_size_bytes": file_size,
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "status": config.STATUS_READY,
    }
    try:
        database.insert_document(document, chunks)
    except sqlite3.Error as error:
        return _storage_error("upload", trace_id, user_id, role, error,
                              filename=filename, file_type=file_type,
                              knowledge_scope=knowledge_scope, file_size_bytes=file_size)

    log_document_event(
        trace_id=trace_id, action="upload", decision="allow", stage="complete",
        role=role, document_id=document_id, file_type=file_type,
        file_size_bytes=file_size, page_count=extraction.page_count,
        chunk_count=len(chunks), http_status=201, reason="Document stored.", **base,
    )
    return UploadResponse(
        status=config.STATUS_READY,
        document_id=document_id,
        filename=filename,
        file_type=file_type,
        knowledge_scope=knowledge_scope,
        page_count=extraction.page_count,
        chunk_count=len(chunks),
        trace_id=str(trace_id),
    )


# ---------------------------------------------------------------------------
# GET /documents
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List uploaded documents",
    description="Admin only. Returns metadata for every stored document, newest first.",
    responses=ERROR_RESPONSES,
)
def list_documents(user_id: str = USER_ID_QUERY):
    trace_id = uuid4()
    user_id = user_id.strip()

    role, denied = _authorize_admin("list", trace_id, user_id)
    if denied:
        return denied

    try:
        rows = database.list_documents()
    except sqlite3.Error as error:
        return _storage_error("list", trace_id, user_id, role, error)

    log_document_event(
        trace_id=trace_id, action="list", decision="allow", stage="complete",
        user_id=user_id, role=role, http_status=200,
        reason=f"Returned {len(rows)} document(s).",
    )
    return DocumentListResponse(
        documents=[_to_metadata(row) for row in rows],
        total=len(rows),
        trace_id=str(trace_id),
    )


# ---------------------------------------------------------------------------
# GET /documents/{document_id}
# ---------------------------------------------------------------------------

@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    summary="Get document metadata and chunk information",
    description=(
        "Admin only. Returns the document's metadata and a list of its chunks "
        "(ID, index, page number, word count and a short text preview)."
    ),
    responses=ERROR_RESPONSES,
)
def get_document(
    document_id: UUID = Path(description="Document ID (UUID) returned by the upload."),
    user_id: str = USER_ID_QUERY,
):
    trace_id = uuid4()
    user_id = user_id.strip()
    doc_id = str(document_id)

    role, denied = _authorize_admin("view", trace_id, user_id, document_id=doc_id)
    if denied:
        return denied

    try:
        row = database.get_document(doc_id)
        chunk_rows = database.get_chunks(doc_id) if row else []
    except sqlite3.Error as error:
        return _storage_error("view", trace_id, user_id, role, error, document_id=doc_id)

    if row is None:
        log_document_event(
            trace_id=trace_id, action="view", decision="reject", stage="lookup",
            user_id=user_id, role=role, document_id=doc_id, http_status=404,
            reason="document_not_found: Document does not exist.",
        )
        return _error(404, "not_found", "document_not_found", "Document does not exist.", trace_id)

    log_document_event(
        trace_id=trace_id, action="view", decision="allow", stage="complete",
        user_id=user_id, role=role, document_id=doc_id, filename=row["filename"],
        file_type=row["file_type"], knowledge_scope=row["knowledge_scope"],
        page_count=row["page_count"], chunk_count=row["chunk_count"],
        http_status=200, reason="Document details returned.",
    )
    return DocumentDetailResponse(
        document=_to_metadata(row),
        chunks=[
            ChunkInfo(
                chunk_id=chunk["chunk_id"],
                chunk_index=chunk["chunk_index"],
                page_number=chunk["page_number"],
                word_count=chunk["word_count"],
                text_preview=chunk["text"][:PREVIEW_LENGTH],
            )
            for chunk in chunk_rows
        ],
        trace_id=str(trace_id),
    )


# ---------------------------------------------------------------------------
# DELETE /documents/{document_id}
# ---------------------------------------------------------------------------

@router.delete(
    "/{document_id}",
    response_model=DeleteResponse,
    summary="Delete a document and its chunks",
    description="Admin only. Permanently removes the document and all of its chunks.",
    responses=ERROR_RESPONSES,
)
def delete_document(
    document_id: UUID = Path(description="Document ID (UUID) returned by the upload."),
    user_id: str = USER_ID_QUERY,
):
    trace_id = uuid4()
    user_id = user_id.strip()
    doc_id = str(document_id)

    role, denied = _authorize_admin("delete", trace_id, user_id, document_id=doc_id)
    if denied:
        return denied

    try:
        row = database.get_document(doc_id)  # read first so the audit event has the filename
        chunks_deleted = database.delete_document(doc_id) if row else None
    except sqlite3.Error as error:
        return _storage_error("delete", trace_id, user_id, role, error, document_id=doc_id)

    if chunks_deleted is None:
        log_document_event(
            trace_id=trace_id, action="delete", decision="reject", stage="lookup",
            user_id=user_id, role=role, document_id=doc_id, http_status=404,
            reason="document_not_found: Document does not exist.",
        )
        return _error(404, "not_found", "document_not_found", "Document does not exist.", trace_id)

    log_document_event(
        trace_id=trace_id, action="delete", decision="allow", stage="complete",
        user_id=user_id, role=role, document_id=doc_id, filename=row["filename"],
        file_type=row["file_type"], knowledge_scope=row["knowledge_scope"],
        chunk_count=chunks_deleted, http_status=200, reason="Document and chunks deleted.",
    )
    return DeleteResponse(
        status="deleted", document_id=doc_id, chunks_deleted=chunks_deleted,
        trace_id=str(trace_id),
    )
