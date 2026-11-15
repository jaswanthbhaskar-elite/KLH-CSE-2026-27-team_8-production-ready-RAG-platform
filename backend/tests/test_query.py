"""Automated tests for the AI Knowledge Platform backend."""

import json
import logging
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import main
from governance.access_control import check_access
from governance.audit import audit_logger, log_decision
from governance.purpose_control import check_purpose

client = TestClient(main.app)

RESPONSE_KEYS = {"status", "answer", "citations", "refusal_reason", "trace_id"}
RETRIEVAL_MESSAGE = "Knowledge retrieval has not been configured yet."


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------

def make_payload(**overrides):
    """A valid student request. Override any field to change it."""
    payload = {
        "question": "What are the exam rules?",
        "user_id": "student001",
        "purpose": "academic",
        "knowledge_scope": ["course_materials"],
    }
    payload.update(overrides)
    return payload


def assert_structured(body: dict) -> UUID:
    """Check the response shape and return the parsed trace ID."""
    assert set(body.keys()) == RESPONSE_KEYS
    trace_id = UUID(body["trace_id"])  # raises ValueError if not a UUID
    assert trace_id.version == 4
    return trace_id


def assert_refused(response, reason_contains: str, status_code: int = 200):
    assert response.status_code == status_code
    body = response.json()
    assert_structured(body)
    assert body["status"] == "refused"
    assert body["answer"] is None
    assert body["citations"] == []
    assert reason_contains in body["refusal_reason"]
    return body


class AuditCapture(logging.Handler):
    """Collects audit log lines so tests can inspect them."""

    def __init__(self):
        super().__init__()
        self.raw = []

    def emit(self, record):
        self.raw.append(record.getMessage())

    @property
    def events(self):
        return [json.loads(line) for line in self.raw]


@pytest.fixture
def audit_capture():
    handler = AuditCapture()
    audit_logger.addHandler(handler)
    yield handler
    audit_logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# Existing endpoints still work
# ---------------------------------------------------------------------------

def test_home_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {
        "message": "AI Knowledge Platform is running!",
        "status": "operational",
    }


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_swagger_docs_and_openapi_are_available():
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "post" in schema["paths"]["/query"]
    assert "get" in schema["paths"]["/"]
    assert "get" in schema["paths"]["/health"]


# ---------------------------------------------------------------------------
# Authorized users (all three roles)
# ---------------------------------------------------------------------------

ALL_SCOPES = [
    "academic_regulations",
    "course_materials",
    "research_documents",
    "administrative_documents",
]

VALID_REQUESTS = [
    pytest.param("student001", "academic", ["course_materials"], id="student-academic"),
    pytest.param(
        "student001", "general", ["academic_regulations", "course_materials"],
        id="student-general-two-scopes",
    ),
    pytest.param("researcher001", "research", ["research_documents"], id="researcher-research"),
    pytest.param("researcher001", "academic", ["course_materials"], id="researcher-academic"),
    pytest.param(
        "researcher001", "general", ["research_documents", "course_materials"],
        id="researcher-general-two-scopes",
    ),
    pytest.param("admin001", "research", ALL_SCOPES, id="admin-all-scopes"),
    pytest.param("admin001", "academic", ["administrative_documents"], id="admin-admin-docs"),
    pytest.param(
        "student001", "academic", ["course_materials", "course_materials"],
        id="duplicate-scopes-are-fine",
    ),
]


@pytest.mark.parametrize("user_id, purpose, scope", VALID_REQUESTS)
def test_authorized_requests_reach_retrieval_stage(user_id, purpose, scope):
    response = client.post(
        "/query",
        json=make_payload(user_id=user_id, purpose=purpose, knowledge_scope=scope),
    )
    assert response.status_code == 200
    body = response.json()
    assert_structured(body)
    assert body["status"] == "needs_review"
    assert body["refusal_reason"] == RETRIEVAL_MESSAGE
    assert body["answer"] is None
    assert body["citations"] == []


def test_user_id_whitespace_is_stripped():
    response = client.post("/query", json=make_payload(user_id="  student001  "))
    assert response.status_code == 200
    assert response.json()["status"] == "needs_review"


# ---------------------------------------------------------------------------
# Unauthorized (unknown) users
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("user_id", ["hacker999", "STUDENT001", "admin", "student002"])
def test_unknown_users_are_refused(user_id):
    response = client.post("/query", json=make_payload(user_id=user_id))
    assert_refused(response, "User identity is not recognized.")


def test_identity_is_checked_before_scope():
    response = client.post(
        "/query",
        json=make_payload(user_id="hacker999", knowledge_scope=["administrative_documents"]),
    )
    assert_refused(response, "User identity is not recognized.")


# ---------------------------------------------------------------------------
# Purpose restrictions
# ---------------------------------------------------------------------------

def test_student_cannot_use_research_purpose():
    response = client.post("/query", json=make_payload(purpose="research"))
    assert_refused(response, "Purpose 'research' is not permitted for role 'student'.")


@pytest.mark.parametrize("purpose", ["academic", "research", "general"])
def test_researcher_may_use_all_purposes(purpose):
    response = client.post(
        "/query",
        json=make_payload(
            user_id="researcher001",
            purpose=purpose,
            knowledge_scope=["course_materials"],
        ),
    )
    assert response.json()["status"] == "needs_review"


def test_purpose_is_checked_before_scope():
    # Both the purpose and the scope are wrong. The purpose reason must win.
    response = client.post(
        "/query",
        json=make_payload(purpose="research", knowledge_scope=["research_documents"]),
    )
    assert_refused(response, "Purpose")


def test_purpose_control_unit_unknown_role():
    result = check_purpose("visitor", "general")
    assert result["allowed"] is False


# ---------------------------------------------------------------------------
# Forbidden and empty scopes
# ---------------------------------------------------------------------------

FORBIDDEN_SCOPE_CASES = [
    pytest.param("student001", "academic", ["research_documents"], "research_documents",
                 id="student-research-docs"),
    pytest.param("student001", "academic", ["administrative_documents"], "administrative_documents",
                 id="student-admin-docs"),
    pytest.param("researcher001", "research", ["administrative_documents"], "administrative_documents",
                 id="researcher-admin-docs"),
    pytest.param("researcher001", "research", ["academic_regulations"], "academic_regulations",
                 id="researcher-regulations"),
    pytest.param("admin001", "research", ["made_up_scope"], "made_up_scope",
                 id="admin-unknown-scope"),
    pytest.param("student001", "academic", ["course_materials", "research_documents"],
                 "research_documents", id="mixed-valid-and-forbidden"),
]


@pytest.mark.parametrize("user_id, purpose, scope, denied", FORBIDDEN_SCOPE_CASES)
def test_forbidden_scopes_are_refused(user_id, purpose, scope, denied):
    response = client.post(
        "/query",
        json=make_payload(user_id=user_id, purpose=purpose, knowledge_scope=scope),
    )
    assert_refused(response, "Access denied")
    assert denied in response.json()["refusal_reason"]


def test_mixed_request_names_only_the_denied_scope():
    response = client.post(
        "/query",
        json=make_payload(knowledge_scope=["course_materials", "research_documents"]),
    )
    reason = response.json()["refusal_reason"]
    assert "research_documents" in reason
    assert "course_materials" not in reason


def test_empty_scope_list_is_refused():
    response = client.post("/query", json=make_payload(knowledge_scope=[]))
    assert_refused(response, "scope")


def test_omitted_scope_is_refused():
    payload = make_payload()
    del payload["knowledge_scope"]
    response = client.post("/query", json=payload)
    assert_refused(response, "scope")


def test_access_control_unit_unknown_role():
    result = check_access("visitor", ["course_materials"])
    assert result["allowed"] is False


# ---------------------------------------------------------------------------
# Invalid input -> HTTP 422 in the structured format
# ---------------------------------------------------------------------------

INVALID_FIELD_CASES = [
    pytest.param({"purpose": "hacking"}, "purpose", id="invalid-purpose"),
    pytest.param({"purpose": 123}, "purpose", id="purpose-wrong-type"),
    pytest.param({"question": "hi"}, "question", id="question-too-short"),
    pytest.param({"question": "x" * 1001}, "question", id="question-too-long"),
    pytest.param({"question": "     "}, "question", id="question-whitespace-only"),
    pytest.param({"user_id": ""}, "user_id", id="empty-user-id"),
    pytest.param({"user_id": "u" * 65}, "user_id", id="user-id-too-long"),
    pytest.param({"knowledge_scope": "course_materials"}, "knowledge_scope",
                 id="scope-is-a-string"),
    pytest.param({"knowledge_scope": ["a"] * 11}, "knowledge_scope", id="too-many-scopes"),
    pytest.param({"knowledge_scope": ["s" * 65]}, "knowledge_scope", id="scope-name-too-long"),
    pytest.param({"knowledge_scope": [""]}, "knowledge_scope", id="empty-scope-name"),
]


@pytest.mark.parametrize("overrides, field", INVALID_FIELD_CASES)
def test_invalid_input_returns_structured_422(overrides, field):
    response = client.post("/query", json=make_payload(**overrides))
    body = assert_refused(response, "Invalid request.", status_code=422)
    assert field in body["refusal_reason"]


@pytest.mark.parametrize("field", ["question", "user_id", "purpose"])
def test_missing_required_fields_return_422(field):
    payload = make_payload()
    del payload[field]
    response = client.post("/query", json=payload)
    body = assert_refused(response, "Invalid request.", status_code=422)
    assert field in body["refusal_reason"]


def test_malformed_json_returns_structured_422():
    response = client.post(
        "/query",
        content="{this is not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert_refused(response, "Invalid request.", status_code=422)


# ---------------------------------------------------------------------------
# Unexpected errors -> safe HTTP 500
# ---------------------------------------------------------------------------

def test_unexpected_error_returns_safe_structured_500(monkeypatch):
    def explode(role, purpose):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(main, "check_purpose", explode)
    safe_client = TestClient(main.app, raise_server_exceptions=False)

    response = safe_client.post("/query", json=make_payload())

    assert response.status_code == 500
    body = response.json()
    assert_structured(body)
    assert body["status"] == "refused"
    assert "secret internal detail" not in response.text


# ---------------------------------------------------------------------------
# Trace IDs
# ---------------------------------------------------------------------------

def test_trace_ids_are_valid_uuid4_on_every_kind_of_response():
    payloads = [
        make_payload(),                                       # needs_review
        make_payload(user_id="nobody"),                       # refused: identity
        make_payload(purpose="research"),                     # refused: purpose
        make_payload(knowledge_scope=["research_documents"]),  # refused: scope
        make_payload(purpose="hacking"),                      # 422
    ]
    for payload in payloads:
        assert_structured(client.post("/query", json=payload).json())


def test_trace_ids_are_unique_across_requests():
    seen = set()
    for _ in range(25):
        body = client.post("/query", json=make_payload()).json()
        seen.add(body["trace_id"])
    assert len(seen) == 25


def test_identical_requests_get_different_trace_ids():
    first = client.post("/query", json=make_payload()).json()["trace_id"]
    second = client.post("/query", json=make_payload()).json()["trace_id"]
    assert first != second


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def test_audit_log_records_allowed_decision(audit_capture):
    secret_question = "TOPSECRET-question-text about exams"
    response = client.post("/query", json=make_payload(question=secret_question))
    trace_id = response.json()["trace_id"]

    events = [e for e in audit_capture.events if e["trace_id"] == trace_id]
    assert len(events) == 1
    event = events[0]
    assert event["decision"] == "allow"
    assert event["stage"] == "retrieval"
    assert event["user_id"] == "student001"
    assert event["role"] == "student"
    assert event["purpose"] == "academic"
    assert event["requested_scope"] == ["course_materials"]
    assert event["response_status"] == "needs_review"
    assert event["question_length"] == len(secret_question)
    assert event["timestamp"]

    # The question text itself must never be logged.
    assert not any("TOPSECRET" in line for line in audit_capture.raw)


AUDIT_DENIAL_CASES = [
    pytest.param(make_payload(user_id="hacker999"), "identity", None, "not recognized",
                 id="identity"),
    pytest.param(make_payload(purpose="research"), "purpose", "student", "not permitted",
                 id="purpose"),
    pytest.param(make_payload(knowledge_scope=["research_documents"]), "scope", "student",
                 "research_documents", id="scope"),
]


@pytest.mark.parametrize("payload, stage, role, reason_text", AUDIT_DENIAL_CASES)
def test_audit_log_records_denials_with_reason(audit_capture, payload, stage, role, reason_text):
    response = client.post("/query", json=payload)
    trace_id = response.json()["trace_id"]

    events = [e for e in audit_capture.events if e["trace_id"] == trace_id]
    assert len(events) == 1
    event = events[0]
    assert event["decision"] == "deny"
    assert event["stage"] == stage
    assert event["role"] == role
    assert event["user_id"] == payload["user_id"]
    assert event["purpose"] == payload["purpose"]
    assert event["requested_scope"] == payload["knowledge_scope"]
    assert reason_text in event["reason"]
    assert event["response_status"] == "refused"


def test_audit_log_records_validation_failures_without_input(audit_capture):
    response = client.post("/query", json=make_payload(question="hi"))
    trace_id = response.json()["trace_id"]

    events = [e for e in audit_capture.events if e["trace_id"] == trace_id]
    assert len(events) == 1
    assert events[0]["decision"] == "invalid"
    assert events[0]["stage"] == "validation"
    assert "question" in events[0]["reason"]


def test_audit_log_records_unexpected_errors(audit_capture, monkeypatch):
    def explode(role, purpose):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "check_purpose", explode)
    safe_client = TestClient(main.app, raise_server_exceptions=False)
    response = safe_client.post("/query", json=make_payload())

    trace_id = response.json()["trace_id"]
    events = [e for e in audit_capture.events if e["trace_id"] == trace_id]
    assert len(events) == 1
    assert events[0]["decision"] == "error"
    assert "RuntimeError" in events[0]["reason"]


def test_audit_log_cannot_be_forged_with_newlines(audit_capture):
    client.post("/query", json=make_payload(user_id="evil\nFAKE LOG LINE"))
    last_line = audit_capture.raw[-1]
    assert "\n" not in last_line          # still exactly one line
    json.loads(last_line)                 # still valid JSON


def test_audit_log_truncates_very_long_values(audit_capture):
    log_decision(
        trace_id=uuid4(),
        decision="deny",
        stage="identity",
        user_id="x" * 500,
    )
    assert len(audit_capture.events[-1]["user_id"]) < 200