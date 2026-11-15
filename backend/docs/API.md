# AI Knowledge Platform: API Reference

**Version:** 1.1.0 | **Base URL (development):** `http://127.0.0.1:8000`
**Interactive docs:** `/docs` (Swagger UI), `/redoc`, `/openapi.json`

> **Prototype status.** User identity is a hardcoded development mapping.
> Do not treat this API as production-secure. See `SECURITY_REVIEW.md`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service status |
| GET | `/health` | Health check |
| POST | `/query` | Governed knowledge query |

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

404 and 405 errors use FastAPI's default `{"detail": ...}` shape.

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

| Role | Dev user ID | Purposes | Scopes |
|---|---|---|---|
| student | `student001` | academic, general | academic_regulations, course_materials |
| researcher | `researcher001` | academic, research, general | research_documents, course_materials |
| admin | `admin001` | academic, research, general | all four scopes |

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

## Audit log

Written to the console and to `backend/logs/audit.log` (rotating, about 1 MB x 4 files).
One JSON object per line:

| Field | Description |
|---|---|
| `timestamp` | UTC, ISO 8601 |
| `trace_id` | Same value as the API response |
| `user_id`, `role`, `purpose` | As submitted or resolved (`role` is null if identity failed) |
| `requested_scope` | Scopes requested (at most 20 logged, each truncated to 100 characters) |
| `decision` | `allow`, `deny`, `invalid` or `error` |
| `stage` | `validation`, `identity`, `purpose`, `scope`, `retrieval` or `server` |
| `reason` | Refusal or decision reason |
| `response_status` | `needs_review` or `refused` |
| `question_length` | Length only. **The question text is never logged** |

Search by trace ID in PowerShell:

```powershell
Select-String -Path logs\audit.log -Pattern "PASTE-TRACE-ID-HERE"
```