import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import database
from governance.access_control import check_access
from governance.audit import log_decision, setup_audit_logging
from governance.identity import USER_ROLES
from governance.purpose_control import check_purpose
from routers.documents import router as documents_router
from schemas import HealthResponse, HomeResponse, QueryRequest, QueryResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("knowledge_platform")
setup_audit_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once when the server starts: create the SQLite tables if needed."""
    database.init_db()
    logger.info("Database ready at %s", database.get_db_path())
    yield


# ---------------------------------------------------------------------------
# Swagger / OpenAPI documentation text
# ---------------------------------------------------------------------------

API_DESCRIPTION = """
Governed AI-powered knowledge management system (**prototype**).

## Authorization flow for `POST /query`

Every query passes these checks **in order**. The first failure stops the request.

1. **Request validation** (Pydantic). Bad input returns HTTP 422.
2. **Trace ID**. A unique UUID is created for the request.
3. **Identity**. `user_id` is mapped to a role (student, researcher, admin).
4. **Purpose limitation**. The role must be allowed to use the stated purpose.
5. **Scope access (RBAC)**. At least one scope is required, and *every*
   requested scope must be permitted for the role.
6. **Retrieval**. Not implemented yet, so the result is `needs_review`.

Every decision is written to the audit log with its trace ID.

## Document ingestion (`/documents`)

Only the **admin** identity may upload, list, view or delete documents.
Uploads accept PDF and TXT files (max 10 MB), extract the text, split it into
chunks and store everything in SQLite. Scanned PDFs are rejected (no OCR).
Errors use the shape `{status, error_code, reason, trace_id}`.

## Roles

| Role | Purposes | Knowledge scopes |
|---|---|---|
| student | academic, general | academic_regulations, course_materials |
| researcher | academic, research, general | research_documents, course_materials |
| admin | academic, research, general | all four scopes |

## Prototype warning

Authentication is a **placeholder**: the user ID is trusted as sent.
Do not treat this API as production-secure.
"""

TAGS_METADATA = [
    {"name": "System", "description": "Service status and health checks."},
    {
        "name": "Knowledge Query",
        "description": "Governed question endpoint with identity, purpose and scope checks.",
    },
    {
        "name": "Documents",
        "description": "Admin-only document upload, listing, inspection and deletion.",
    },
]

EXAMPLE_TRACE_ID = "3f2b8c1e-5d4a-4e7b-9a61-0c2d8e4f7a10"

QUERY_200_EXAMPLES = {
    "needs_review": {
        "summary": "Authorized, retrieval not implemented yet",
        "value": {
            "status": "needs_review",
            "answer": None,
            "citations": [],
            "refusal_reason": "Knowledge retrieval has not been configured yet.",
            "trace_id": EXAMPLE_TRACE_ID,
        },
    },
    "refused_scope": {
        "summary": "Refused: scope not permitted for the role",
        "value": {
            "status": "refused",
            "answer": None,
            "citations": [],
            "refusal_reason": (
                "Access denied for role 'student' to scope(s): research_documents."
            ),
            "trace_id": EXAMPLE_TRACE_ID,
        },
    },
    "refused_purpose": {
        "summary": "Refused: purpose not permitted for the role",
        "value": {
            "status": "refused",
            "answer": None,
            "citations": [],
            "refusal_reason": "Purpose 'research' is not permitted for role 'student'.",
            "trace_id": EXAMPLE_TRACE_ID,
        },
    },
    "refused_identity": {
        "summary": "Refused: unknown user",
        "value": {
            "status": "refused",
            "answer": None,
            "citations": [],
            "refusal_reason": "User identity is not recognized.",
            "trace_id": EXAMPLE_TRACE_ID,
        },
    },
}

app = FastAPI(
    title="AI Knowledge Platform",
    description=API_DESCRIPTION,
    version="1.2.0",
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
)

app.include_router(documents_router)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def refuse(
    trace_id: UUID,
    request: QueryRequest,
    role: str | None,
    stage: str,
    reason: str,
) -> QueryResponse:
    """Audit-log a denial and build the structured refusal response."""
    log_decision(
        trace_id=trace_id,
        decision="deny",
        stage=stage,
        user_id=request.user_id,
        role=role,
        purpose=request.purpose,
        requested_scope=request.knowledge_scope,
        reason=reason,
        response_status="refused",
        question_length=len(request.question),
    )
    return QueryResponse(
        status="refused",
        refusal_reason=reason,
        trace_id=trace_id,
    )


def error_content(
    request: Request,
    trace_id: UUID,
    reason: str,
    documents_status: str,
    documents_code: str,
) -> dict:
    """
    Build the JSON body for validation and server errors.

    /documents errors use {status, error_code, reason, trace_id}.
    Every other path keeps the original /query shape.
    """
    if request.url.path.startswith("/documents"):
        return {
            "status": documents_status,
            "error_code": documents_code,
            "reason": reason,
            "trace_id": str(trace_id),
        }
    return {
        "status": "refused",
        "answer": None,
        "citations": [],
        "refusal_reason": reason,
        "trace_id": str(trace_id),
    }


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Return Pydantic validation errors in the platform's structured format."""
    trace_id = uuid4()
    problems = []
    fields = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"] if part != "body")
        fields.append(location or "body")
        problems.append(f"{location}: {error['msg']}" if location else error["msg"])

    reason = "Invalid request. " + "; ".join(problems)

    # Log field names only. The rejected input itself is never logged.
    log_decision(
        trace_id=trace_id,
        decision="invalid",
        stage="validation",
        reason="Validation failed for: " + ", ".join(fields),
        response_status="refused",
    )

    return JSONResponse(
        status_code=422,
        content=error_content(request, trace_id, reason, "rejected", "invalid_request"),
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    """Catch-all so unexpected failures never leak internals to the client."""
    trace_id = uuid4()
    logger.exception("trace_id=%s unhandled error", trace_id)
    log_decision(
        trace_id=trace_id,
        decision="error",
        stage="server",
        reason=f"Unhandled exception: {type(exc).__name__}",
        response_status="refused",
    )

    return JSONResponse(
        status_code=500,
        content=error_content(
            request,
            trace_id,
            "An internal error occurred. Please try again later.",
            "error",
            "internal_error",
        ),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/",
    response_model=HomeResponse,
    tags=["System"],
    summary="Service status",
    description="Returns a short message confirming that the API is running.",
)
def home():
    return {
        "message": "AI Knowledge Platform is running!",
        "status": "operational",
    }


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check",
    description="Lightweight liveness check for monitoring and scripts.",
)
def health():
    return {"status": "healthy"}


@app.post(
    "/query",
    response_model=QueryResponse,
    tags=["Knowledge Query"],
    summary="Submit a governed knowledge query",
    description=(
        "Checks identity, purpose and knowledge-scope access, in that order. "
        "Authorization failures return HTTP 200 with `status: \"refused\"`. "
        "Malformed requests return HTTP 422 in the same response shape. "
        "Knowledge retrieval is not implemented yet, so authorized requests "
        "return `status: \"needs_review\"`. Every response has a unique `trace_id`."
    ),
    response_description="Structured result: answered, refused or needs_review.",
    responses={
        200: {"content": {"application/json": {"examples": QUERY_200_EXAMPLES}}},
        422: {
            "model": QueryResponse,
            "description": "Invalid request body (structured, with a trace ID).",
        },
        500: {
            "model": QueryResponse,
            "description": "Unexpected server error (generic message, with a trace ID).",
        },
    },
)
def process_query(request: QueryRequest):
    # One unique trace ID per request, reused in the audit log and the response
    trace_id = uuid4()

    # 1. Identity -> role
    role = USER_ROLES.get(request.user_id)
    if role is None:
        return refuse(trace_id, request, None, "identity", "User identity is not recognized.")

    # 2. Purpose limitation
    purpose_check = check_purpose(role, request.purpose)
    if not purpose_check["allowed"]:
        return refuse(trace_id, request, role, "purpose", purpose_check["reason"])

    # 3. Knowledge scope access (RBAC)
    access_check = check_access(role, request.knowledge_scope)
    if not access_check["allowed"]:
        return refuse(trace_id, request, role, "scope", access_check["reason"])

    # 4. Retrieval is not implemented yet
    reason = "Knowledge retrieval has not been configured yet."
    log_decision(
        trace_id=trace_id,
        decision="allow",
        stage="retrieval",
        user_id=request.user_id,
        role=role,
        purpose=request.purpose,
        requested_scope=request.knowledge_scope,
        reason="Authorization passed. Retrieval not implemented yet.",
        response_status="needs_review",
        question_length=len(request.question),
    )
    return QueryResponse(
        status="needs_review",
        refusal_reason=reason,
        trace_id=trace_id,
    )
