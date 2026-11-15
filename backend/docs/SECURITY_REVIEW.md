# Security Review: AI Knowledge Platform Backend (prototype)

**Scope:** `main.py`, `schemas.py`, `governance/*`, `tests/*`, project files.
**Method:** manual code review. No penetration test, no automated scanner, no third-party audit.

> **This backend is a governed-architecture prototype. It is NOT production-secure.**
> The controls below demonstrate design intent. Several are placeholders.

## 1. Implemented controls vs. placeholders

| Area | Implemented (works today) | Placeholder / missing |
|---|---|---|
| Authentication | None | `user_id` is trusted as sent; no password, token or session |
| Role assignment | Roles are resolved on the server, not sent by the client | Roles are hardcoded in `main.py` |
| RBAC | Allowlist per role; unknown roles and unknown scopes denied; empty scope denied; one bad scope refuses the whole request | Permissions are hardcoded; scopes are not linked to real documents yet |
| Purpose limitation | Allowlist per role; checked before scope | Purpose is self-declared and cannot be verified |
| Input validation | Pydantic types, length limits, enum for `purpose`, list-size and item-length limits, whitespace trimming | See section 2.4 |
| Error handling | Structured 422; generic 500 that hides internals | 404 and 405 use a different default format |
| Traceability | UUID4 trace ID per request, in the response and in the audit log | No persistent, tamper-evident store |
| Audit logging | Structured JSON, rotating file, no question text, truncated and escaped values | See section 2.5 |
| Tests | Automated pytest suite for the decision logic | No security-specific or load tests |

## 2. Weaknesses

### 2.1 Authentication (Critical)
- Anyone can send `"user_id": "admin001"` and become admin. Identity is a claim, not proof.
- User IDs are predictable and listed in the source code.
- Users and roles are in source code: no revocation, no management, changes need a redeploy.
- No rate limiting, so unlimited guessing is possible.

### 2.2 Authorization (High for the future RAG stage)
- Checks apply to scope *names in the request*, not to documents. When retrieval is added,
  scope filtering must be enforced **at retrieval time** (filter chunks by scope metadata
  before anything reaches the model), not only at the front door.
- Purpose limitation is declarative: a client can choose any purpose its role allows.
  Researcher and admin currently have identical purposes.
- Permissions live in two separate dictionaries with free-text scope names, so typos or drift are easy.
- Refusal messages reveal policy details to unauthenticated callers (role names, scope names)
  and allow user enumeration ("identity not recognized" differs from "access denied").
- Refusals return HTTP 200. This is a documented design choice, but monitoring tools cannot tell
  allowed from denied by status code.

### 2.3 Transport and exposure (Medium)
- Plain HTTP in development; no TLS.
- No CORS configuration yet. The React frontend will need one. Allow only the exact Vite origin
  (for example `http://localhost:5173`), never `*`.
- Swagger (`/docs`) is open to everyone.

### 2.4 Input validation (Medium)
- No server-level request body size limit.
- `user_id` and scope names accept any characters (length-limited only).
- Unknown extra JSON fields are silently ignored (Pydantic default).
- The question is not screened for prompt-injection text. This matters once an LLM is involved.

### 2.5 Logging (Medium)
- The log file is a plain file. Anyone with disk access can edit or delete it. No tamper evidence.
- Rotation keeps about 4 files, so old events are eventually overwritten. That is not an audit retention policy.
- Logs contain user IDs (personal data). No retention or access policy is defined.
- Validation failures do not record who sent them.
- No client IP is logged.
- Timestamps use the server clock.

### 2.5.1 Other
- Dependencies are unpinned, which gives some supply-chain and reproducibility risk.
- `pyvenv.cfg` exposes the local username and path. Never commit `venv/`.

## 3. Recommended improvements (all zero-cost)

| Priority | Improvement | Notes |
|---|---|---|
| 1 | Real authentication | Store users in SQLite. Hash passwords with `hashlib.scrypt` (standard library). Issue random session tokens with `secrets.token_urlsafe` and store only their hash. Take the role from the server-side user record, never from the request. |
| 2 | Move roles and permissions into SQLite | One policy source, editable without redeploying, with a role-change audit trail. |
| 3 | Enforce scopes at retrieval | Tag every document chunk with its scope. Filter before prompting the model. Add tests that prove a student's query can never retrieve admin chunks. |
| 4 | Persistent audit table in SQLite | Insert-only by convention. Optionally store a hash of the previous row to detect edits. Define retention. |
| 5 | Uniform external refusal messages | Keep detailed reasons in the audit log, and return fewer policy details to callers. |
| 6 | Simple in-memory rate limiting | Per-IP and per-user counters in plain Python. |
| 7 | CORS with exact origin | `CORSMiddleware` (already included in FastAPI) when the frontend starts. |
| 8 | Stricter schemas | `extra="forbid"`, a pattern for `user_id`, and a request-size limit. |
| 9 | Decide on HTTP 401/403 for refusals | Better for monitoring, but update the tests and docs together. |
| 10 | Pin dependency versions | Run `pip freeze` once the stack is stable. |
| 11 | Add prompt-injection tests | Needed once an LLM answers questions. |

## 4. What we do NOT claim
- No claim of production readiness, compliance or privacy certification.
- No claim that logs are tamper-proof.
- No claim that purpose limitation is enforced beyond a role allowlist.
- The tests prove the current decision logic behaves as designed. They do not prove the system is secure.