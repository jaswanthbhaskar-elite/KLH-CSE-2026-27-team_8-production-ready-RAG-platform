# Document Ingestion

How the platform accepts PDF and TXT files, extracts text, splits it into chunks and stores
everything in SQLite. This is the data foundation for the later RAG tasks.

**Not part of this task:** embeddings, FAISS, BM25, LLM answers, frontend, OCR.

## 1. Pipeline at a glance

```text
POST /documents/upload  (multipart: file, user_id, knowledge_scope)
        |
        v
  governance      identity -> admin role -> scope (existing check_access)
        |
        v
  ingestion/filenames.py   sanitize the filename, read the extension
        |
        v
  size check              read at most 10 MB + 1 byte
        |
        v
  ingestion/extraction.py  PDF (pypdf) or TXT (UTF-8)  ->  pages of text
        |
        v
  ingestion/chunking.py    pages  ->  ~500-word chunks, 50-word overlap
        |
        v
  database.py              one transaction: 1 document row + N chunk rows
        |
        v
  governance/audit.py      one audit line, no document text
```

## 2. Folder layout

```text
backend/
  main.py                      app, startup, error handlers, /query, /, /health
  schemas.py                   /query request and response models
  document_schemas.py          /documents response models
  database.py                  SQLite connection, tables and queries
  governance/
    access_control.py          scope RBAC (reused by upload)
    purpose_control.py         purpose limitation (/query)
    identity.py                user -> role table and the "is admin" check
    audit.py                   audit events (request_decision, document_event)
  ingestion/
    config.py                  limits and defaults in one place
    filenames.py               filename sanitizing
    extraction.py              PDF and TXT text extraction
    chunking.py                text -> chunks
  routers/
    documents.py               the four /documents endpoints
  tests/                       pytest suite (temporary databases only)
  data/knowledge.db            created at startup (ignored by Git)
  logs/audit.log               created at startup (ignored by Git)
```

`routers/documents.py` only handles HTTP and authorization. The work is done by the modules
above, each of which can be read and tested on its own.

## 3. Endpoints

See `API.md` for full request and response examples. Summary:

| Method | Path | Success |
|---|---|---|
| POST | `/documents/upload` | 201 with document ID, filename, page count, chunk count, status |
| GET | `/documents` | 200 with all document metadata |
| GET | `/documents/{document_id}` | 200 with metadata and chunk information |
| DELETE | `/documents/{document_id}` | 200 with the number of chunks deleted |

All four are admin only (`admin001`).

## 4. Design decisions

| Decision | Reason |
|---|---|
| **One scope per document** | Retrieval can later filter with a simple rule: a document is visible only if its scope is in the user's permitted scopes. |
| **Scope checked with `check_access(role, [scope])`** | Reuses the existing governance logic instead of copying it. |
| **File processed in memory, never saved to disk** | No filesystem path exists to attack. The filename cannot become a path. |
| **Chunks never cross a page boundary** | Every chunk has an exact page number, which citations will need. Cost: a sentence split across two PDF pages is not joined. |
| **Chunk ID = `<document_id>_chunk_<index>`** | Stable (same inputs give the same ID), unique, and readable. |
| **Chunking is word based (500 words, 50 overlap)** | Simple, explainable, no tokenizer needed. Embedding models count tokens, so the size may be tuned later in `ingestion/config.py`. |
| **Document and chunks saved in one transaction** | A failure never leaves a document without chunks. |
| **`status` column** | Currently always `ready`. A later task can add `indexed` once embeddings exist. |
| **Real HTTP status codes for `/documents`** | 401, 403, 404, 413, 415, 422, 500 are easier for a frontend and for monitoring than always returning 200. (`/query` keeps its original behaviour.) |
| **Authorization before file inspection** | A non-admin cannot learn anything about how files are parsed. |
| **Content-Type header ignored** | Clients can lie. The extension and the actual content decide. |
| **Duplicate uploads allowed** | The SHA-256 of each file is stored (`content_sha256`), so duplicate detection can be added later. |

## 5. Text extraction rules

**TXT**

- Decoded as UTF-8. A leading byte-order mark is accepted and removed.
- Invalid UTF-8 returns `invalid_encoding` with a message asking for a UTF-8 file.
- A NUL character means binary data renamed to `.txt`: `binary_content`.
- Empty or whitespace-only files are rejected.

**PDF** (text layer only, via `pypdf`)

- Must contain the `%PDF-` header near the start.
- Page numbers start at 1 and match the real PDF page, even when blank pages are skipped.
- Pages without text are skipped. If **no** page has text (a scanned PDF), the file is rejected
  with `no_text` and a message that **OCR is not supported**. The system does not try to read images.
- Password-protected PDFs are rejected (`encrypted_pdf`). Limit: 500 pages.
- Any parser failure becomes `malformed_pdf`. The real exception is never sent to the client.

## 6. Database

SQLite file: `backend/data/knowledge.db` (override with the `KNOWLEDGE_DB_PATH` environment variable).
Tables are created automatically at application start (`CREATE TABLE IF NOT EXISTS`).

**`documents`**

| Column | Notes |
|---|---|
| `document_id` | Primary key (UUID text) |
| `filename` | Sanitized name (what the API returns) |
| `original_filename` | Name as uploaded, with control characters removed and cut to 255 characters. **Stored for reference only. Never returned, logged or used as a path** |
| `file_type` | `pdf` or `txt` |
| `knowledge_scope` | One scope |
| `uploaded_by`, `uploaded_at` | Admin ID and UTC timestamp |
| `page_count` | Integer for PDFs, NULL for TXT |
| `chunk_count`, `word_count`, `file_size_bytes` | Extracted text metadata |
| `content_sha256` | Hash of the file, for future duplicate detection |
| `status` | `ready` |

**`chunks`**

| Column | Notes |
|---|---|
| `chunk_id` | Primary key |
| `document_id` | Foreign key to `documents`, `ON DELETE CASCADE` |
| `chunk_index` | Order within the document (unique per document) |
| `text` | The chunk text |
| `page_number` | PDF page, NULL for TXT |
| `word_count` | Words in this chunk |

All SQL uses `?` or named parameters. User input is never pasted into SQL text.
Deleting a document removes its chunks both explicitly and through the foreign-key cascade.

## 7. Security decisions

| Risk | Control | Status |
|---|---|---|
| Path traversal (`../../x`) | The filename is only ever stored as text. Nothing is written to disk. The sanitizer also removes folder parts and unsafe characters. | Implemented and tested |
| Running uploaded content | Files are only decoded as text or parsed for text. Nothing is imported, executed or opened by another program. | Implemented and tested |
| Oversized uploads | At most 10 MB + 1 byte is read inside the handler | Partly: see limitation 1 |
| Wrong file type | Extension allow-list, then content checks (PDF header, UTF-8, no NUL) | Implemented and tested |
| SQL injection | Parameterized queries; document IDs must be valid UUIDs | Implemented and tested |
| Leaking internals | Generic 500 messages. PDF and database errors are logged by exception type only | Implemented and tested |
| Logging sensitive text | Audit events carry metadata only. Tests upload a unique secret string and prove it never appears in the log | Implemented and tested |
| Log forging | JSON-encoded audit lines, sanitized filenames | Implemented and tested |
| Unauthorized management | Admin check before any file handling, on all four endpoints | **Prototype**: see limitation 2 |

## 8. Known limitations (honest list)

1. **Upload size is enforced after the body is received.** FastAPI parses the multipart body
   (spooling it to a temporary file) before the endpoint runs. The 10 MB check protects memory in
   our code, but a very large request is still received first. A real deployment needs a request-size
   limit at the reverse proxy.
2. **Authentication is a placeholder.** `user_id` is trusted as sent, so anyone who sends
   `admin001` is the admin. Document management is only as secure as that placeholder.
3. **No OCR.** Scanned PDFs are rejected on purpose.
4. **Encrypted PDFs are rejected**, not decrypted.
5. **Text structure is flattened.** Chunks are whitespace-joined words, so paragraph breaks, tables
   and columns are not preserved. PDF text extraction quality depends on the PDF.
6. **Chunks never span pages**, so a sentence cut by a page break is split into two chunks.
7. **Only `.pdf` and `.txt`.** No DOCX or Markdown.
8. **PDF parsing runs inside the API process.** A hostile PDF could use CPU or memory for a while
   (limited to 500 pages and 10 MB, not sandboxed).
9. **No duplicate detection**, and no document update or re-index endpoint (delete and re-upload).
10. **No pagination** on `GET /documents`.
11. **One scope per document.** Sharing a document across scopes means uploading it twice.
12. **SQLite single file.** Fine for a prototype. Concurrent heavy writes are not a goal.
13. **Not tested on Python 3.14.** The tests were run on Python 3.13. All dependencies are
    pure Python or ship wheels, but run `pytest` on your 3.14 environment to confirm.
14. **`/query` does not read the stored documents yet.** That is the retrieval task.

## 9. How the tests keep the real database safe

- `tests/conftest.py` has an automatic fixture that sets `KNOWLEDGE_DB_PATH` to a fresh file in
  pytest's temporary folder for **every** test.
- It also sets the variable at import time as a safety net.
- `test_tests_use_a_temporary_database_not_the_real_one` checks that the path in use is not
  `backend/data/knowledge.db`.
- Test PDFs are generated by a tiny helper in `tests/helpers.py`, so no binary test files are
  needed and no extra packages are installed.

## 10. Where to extend next

| Next task | Where it plugs in |
|---|---|
| Embeddings and FAISS | Read rows from `chunks`, then set `documents.status` to `indexed` |
| BM25 | Same `chunks.text` column |
| Scope-filtered retrieval | `documents.knowledge_scope` joined to `chunks.document_id` and filtered with the user's permitted scopes **inside the retrieval query** |
| Citations | `documents.filename` plus `chunks.page_number` |
