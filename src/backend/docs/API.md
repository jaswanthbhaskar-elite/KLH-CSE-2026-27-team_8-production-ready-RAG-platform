# AI Knowledge Platform: API Reference

**Version:** 1.2.0 | **Base URL (development):** `http://127.0.0.1:8000`
**Interactive docs:** `/docs` (Swagger UI), `/redoc`, `/openapi.json`

> **Prototype status.** User identity is a hardcoded development mapping.
> Do not treat this API as production-secure. See `SECURITY_REVIEW.md`.

## Endpoints

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/` | anyone | Service status |
| GET | `/health` | anyone | Health check |
| POST | `/query` | known users | Governed knowledge query |
| POST | `/documents/upload` | admin only | Upload a PDF or TXT document |
| GET | `/documents` | admin only | List documents |
| GET | `/documents/{document_id}` | admin only | Document metadata and chunk information |
| DELETE | `/documents/{document_id}` | admin only | Delete a document and its chunks |

Document ingestion is described in more detail in `DOCUMENT_INGESTION.md`.

---

## GET `/`

Confirms that the API is running.

**Response 200**

```json
{ "message": "AI Knowledge Platform is running!", "status": "operational" }
```

## GET `/health`

**Response 200**

```json
{ "status": "healthy" }
```

---

## POST `/query`

### Request body

| Field | Type | Rules | Description |
|---|---|---|---|
| `question` | string | 3 to 1000 characters, whitespace trimmed | The user's question |
| `user_id` | string | 1 to 64 characters, whitespace trimmed | Development user ID |
| `purpose` | string | `academic`, `research` or `general` | Declared purpose |
| `knowledge_scope` | list of strings | 1 to 10 items, each 1 to 64 characters | Knowledge areas requested |

An omitted `knowledge_scope` defaults to an empty list, which is **refused**.

### Response body (all outcomes use this shape)

| Field | Type | Description |
|---|---|---|
| `status` | `answered` \| `refused` \| `needs_review` | Outcome |
| `answer` | string or null | Generated answer (not implemented yet) |
| `citations` | list | `{document_name, page_number, excerpt}` items (not implemented yet) |
| `refusal_reason` | string or null | Why the request was refused or needs review |
| `trace_id` | UUID | Unique per request. Matches the audit log |

### Status values

| `status` | Meaning |
|---|---|
| `refused` | Blocked by validation, identity, purpose or scope rules |
| `needs_review` | Authorized, but retrieval is not implemented yet |
| `answered` | Reserved for the future RAG pipeline |

### HTTP status codes

| Code | When |
|---|---|
| 200 | Request processed. Check `status`. Authorization refusals also use 200 |
| 422 | Request body failed validation (structured body, `status: "refused"`) |
| 500 | Unexpected server error (generic message, `trace_id` for support) |

### Authorization flow

```text
Request
  1. Pydantic validation ........ fail -> 422, structured refusal
  2. Generate trace_id (UUID4)
  3. Identity: user_id -> role .. unknown -> refused (stage: identity)
  4. Purpose: role may use it? .. no      -> refused (stage: purpose)
  5. Scope (RBAC):
       at least one scope? ...... no      -> refused (stage: scope)
       every scope permitted? ... no      -> refused (stage: scope)
  6. Retrieval (not implemented) ........ -> needs_review
Every step ends in exactly one audit log line carrying the trace_id.
```

### Role matrix (development)

| Role | Dev user ID | Purposes | Scopes | May manage documents |
|---|---|---|---|---|
| student | `student001` | academic, general | academic_regulations, course_materials | no |
| researcher | `researcher001` | academic, research, general | research_documents, course_materials | no |
| admin | `admin001` | academic, research, general | all four scopes | **yes** |

If any requested scope is not permitted, the **whole request** is refused.

### Examples

**1. Authorized (needs_review)**

```powershell
$body = @{ question="What are the exam rules?"; user_id="student001"; purpose="academic"; knowledge_scope=@("course_materials") } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/query -ContentType "application/json" -Body $body | ConvertTo-Json
```

```json
{
  "status": "needs_review",
  "answer": null,
  "citations": [],
  "refusal_reason": "Knowledge retrieval has not been configured yet.",
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

**2. Refused: scope not permitted** (student asks for `research_documents`)

```json
{
  "status": "refused",
  "answer": null,
  "citations": [],
  "refusal_reason": "Access denied for role 'student' to scope(s): research_documents.",
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

**3. Refused: purpose** (student uses `research`)

```json
{
  "status": "refused",
  "answer": null,
  "citations": [],
  "refusal_reason": "Purpose 'research' is not permitted for role 'student'.",
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

**4. Refused: unknown user**

```json
{
  "status": "refused",
  "answer": null,
  "citations": [],
  "refusal_reason": "User identity is not recognized.",
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

**5. Invalid input (HTTP 422)**, for example `purpose` = `"hacking"`

```json
{
  "status": "refused",
  "answer": null,
  "citations": [],
  "refusal_reason": "Invalid request. purpose: Input should be 'academic', 'research' or 'general'",
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

**6. Server error (HTTP 500)**

```json
{
  "status": "refused",
  "answer": null,
  "citations": [],
  "refusal_reason": "An internal error occurred. Please try again later.",
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

---

## Document endpoints (`/documents`)

All four endpoints are **admin only** (`user_id=admin001`). The admin check runs
before the uploaded file is examined.

### Error response shape (all `/documents` errors)

`/documents` errors use a different shape from `/query`, with real HTTP status codes:

```json
{
  "status": "rejected",
  "error_code": "file_too_large",
  "reason": "The file exceeds the maximum upload size of 10 MB.",
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

| Field | Description |
|---|---|
| `status` | `refused` (not authorized), `rejected` (bad input or file), `not_found`, `error` (server) |
| `error_code` | Stable machine-readable reason (table below) |
| `reason` | Human-readable text. Never contains internal exception details |
| `trace_id` | Unique per request. Matches the audit log |

| HTTP | `status` | `error_code` | Cause |
|---|---|---|---|
| 401 | refused | `unknown_user` | `user_id` is not a known user |
| 403 | refused | `not_admin` | Known user, but not the admin |
| 404 | not_found | `document_not_found` | No document with that ID |
| 413 | rejected | `file_too_large` | File is larger than 10 MB |
| 415 | rejected | `unsupported_file_type` | Extension is not `.pdf` or `.txt` |
| 422 | rejected | `invalid_scope` | Scope is blank, unknown or not permitted |
| 422 | rejected | `invalid_request` | Missing or malformed field (including a bad document ID) |
| 422 | rejected | `empty_file` | The file has zero bytes |
| 422 | rejected | `no_text` | No extractable text (whitespace-only TXT, or scanned PDF) |
| 422 | rejected | `invalid_encoding` | TXT is not valid UTF-8 |
| 422 | rejected | `binary_content` | A `.txt` file that contains binary data |
| 422 | rejected | `malformed_pdf` | The PDF is damaged or unreadable |
| 422 | rejected | `encrypted_pdf` | The PDF is password-protected |
| 422 | rejected | `too_many_pages` | PDF has more than 500 pages |
| 500 | error | `storage_error` | SQLite failure (generic message) |
| 500 | error | `internal_error` | Any other unexpected failure (generic message) |

### POST `/documents/upload`

Content type: `multipart/form-data`.

| Field | Type | Rules | Description |
|---|---|---|---|
| `file` | file | `.pdf` or `.txt`, at most 10 MB | The document |
| `user_id` | string | 1 to 64 characters | Must be `admin001` |
| `knowledge_scope` | string | 1 to 64 characters | **One** scope: `academic_regulations`, `course_materials`, `research_documents` or `administrative_documents` |

**Response 201**

| Field | Type | Description |
|---|---|---|
| `status` | string | `ready` = extracted, chunked and stored |
| `document_id` | string | New document ID (UUID) |
| `filename` | string | **Sanitized** filename (may differ from the uploaded name) |
| `file_type` | `pdf` \| `txt` | Detected from the extension |
| `knowledge_scope` | string | The scope the document was stored under |
| `page_count` | integer or null | Pages in a PDF. `null` for TXT |
| `chunk_count` | integer | Number of chunks stored |
| `trace_id` | string | Matches the audit log |

**Example (PowerShell, real `curl.exe`)**

```powershell
curl.exe -s -F "user_id=admin001" -F "knowledge_scope=course_materials" -F "file=@C:\path\to\handbook.pdf" http://127.0.0.1:8000/documents/upload
```

```json
{
  "status": "ready",
  "document_id": "99e7e1c9-0994-488d-a5ea-a72c908c2506",
  "filename": "handbook.pdf",
  "file_type": "pdf",
  "knowledge_scope": "course_materials",
  "page_count": 12,
  "chunk_count": 14,
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

**Example refusal (student tries to upload), HTTP 403**

```json
{
  "status": "refused",
  "error_code": "not_admin",
  "reason": "Role 'student' is not permitted to manage documents.",
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

**Example scanned PDF, HTTP 422**

```json
{
  "status": "rejected",
  "error_code": "no_text",
  "reason": "The PDF has no extractable text. It may be a scanned document. OCR is not supported, so please upload a PDF with selectable text.",
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

### GET `/documents`

Query parameter: `user_id` (required, must be `admin001`).

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/documents?user_id=admin001" | ConvertTo-Json -Depth 5
```

**Response 200** (newest first, no chunk text)

```json
{
  "documents": [
    {
      "document_id": "99e7e1c9-0994-488d-a5ea-a72c908c2506",
      "filename": "handbook.pdf",
      "file_type": "pdf",
      "knowledge_scope": "course_materials",
      "uploaded_by": "admin001",
      "uploaded_at": "2026-11-15T11:29:06.488660+00:00",
      "page_count": 12,
      "chunk_count": 14,
      "word_count": 5230,
      "file_size_bytes": 184233,
      "status": "ready"
    }
  ],
  "total": 1,
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

### GET `/documents/{document_id}`

Path parameter: `document_id` (UUID). Query parameter: `user_id` (required).

**Response 200**

```json
{
  "document": { "document_id": "99e7e1c9-0994-488d-a5ea-a72c908c2506", "filename": "handbook.pdf", "...": "same fields as the list" },
  "chunks": [
    {
      "chunk_id": "99e7e1c9-0994-488d-a5ea-a72c908c2506_chunk_0000",
      "chunk_index": 0,
      "page_number": 1,
      "word_count": 312,
      "text_preview": "First 200 characters of the chunk..."
    }
  ],
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

A malformed ID (not a UUID) returns 422 `invalid_request`. A valid but unknown ID returns 404.

### DELETE `/documents/{document_id}`

Path parameter: `document_id` (UUID). Query parameter: `user_id` (required).

```powershell
Invoke-RestMethod -Method Delete "http://127.0.0.1:8000/documents/<DOCUMENT-ID>?user_id=admin001" | ConvertTo-Json
```

**Response 200**

```json
{
  "status": "deleted",
  "document_id": "99e7e1c9-0994-488d-a5ea-a72c908c2506",
  "chunks_deleted": 14,
  "trace_id": "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"
}
```

Deletion is permanent. A second delete of the same ID returns 404.

---

## Authorization flow for `/documents`

```text
Request
  1. FastAPI/Pydantic validation .. fail -> 422 invalid_request
  2. Generate trace_id (UUID4)
  3. Identity: user_id known? ..... no   -> 401 unknown_user
  4. Role: is it the admin? ....... no   -> 403 not_admin
  -- upload only, from here --
  5. Scope: check_access(admin, [scope])  -> 422 invalid_scope
  6. File type by extension ............. -> 415 unsupported_file_type
  7. Size (read at most limit + 1 byte) . -> 413 file_too_large
  8. Extract text ....................... -> 422 (see table)
  9. Chunk and store in one transaction . -> 201, or 500 storage_error
Every step ends in exactly one audit log line carrying the trace_id.
```

---

## Audit log

Written to the console and to `backend/logs/audit.log` (rotating, about 1 MB x 4 files).
One JSON object per line. There are two event types.

### `request_decision` (POST `/query`, validation errors, server errors)

| Field | Description |
|---|---|
| `timestamp` | UTC, ISO 8601 |
| `event` | `request_decision` |
| `trace_id` | Same value as the API response |
| `user_id`, `role`, `purpose` | As submitted or resolved (`role` is null if identity failed) |
| `requested_scope` | Scopes requested (at most 20 logged, each truncated to 100 characters) |
| `decision` | `allow`, `deny`, `invalid` or `error` |
| `stage` | `validation`, `identity`, `purpose`, `scope`, `retrieval` or `server` |
| `reason` | Refusal or decision reason |
| `response_status` | `needs_review` or `refused` |
| `question_length` | Length only. **The question text is never logged** |

### `document_event` (all `/documents` endpoints)

| Field | Description |
|---|---|
| `timestamp`, `event`, `trace_id` | As above. `event` is `document_event` |
| `action` | `upload`, `list`, `view` or `delete` |
| `decision` | `allow` (succeeded), `deny` (not authorized), `reject` (bad file or input) or `error` |
| `stage` | `identity`, `role`, `scope`, `file`, `extraction`, `lookup`, `storage` or `complete` |
| `user_id`, `role` | As submitted or resolved |
| `document_id` | Set when a document was created, viewed or deleted |
| `filename` | The **sanitized** filename |
| `file_type`, `file_size_bytes`, `knowledge_scope`, `page_count`, `chunk_count` | Metadata only |
| `http_status`, `reason` | Outcome. `reason` starts with the `error_code` for rejections |

**Document text is never logged.** Only metadata and counts are written.

Search by trace ID in PowerShell:

```powershell
Select-String -Path logs\audit.log -Pattern "PASTE-TRACE-ID-HERE"
```
