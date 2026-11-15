"""
End-to-end tests for the document ingestion API.

Every test uses its own temporary SQLite database (see conftest.py), so the
real backend/data/knowledge.db is never touched.
"""

import os
import sqlite3
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import database
import main
from helpers import make_encrypted_pdf, make_pdf, words
from ingestion import config

ERROR_KEYS = {"status", "error_code", "reason", "trace_id"}
ALL_SCOPES = [
    "academic_regulations",
    "course_materials",
    "research_documents",
    "administrative_documents",
]


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(isolated_database):
    """A client that also runs the app's startup code (creates the tables)."""
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def safe_client(isolated_database):
    """A client that returns HTTP 500 responses instead of re-raising errors."""
    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        yield test_client


def upload(client, filename, content, user_id="admin001", scope="course_materials",
           content_type="application/octet-stream"):
    return client.post(
        "/documents/upload",
        data={"user_id": user_id, "knowledge_scope": scope},
        files={"file": (filename, content, content_type)},
    )


def upload_txt(client, text="Hello knowledge base. This is a small test document.",
               filename="notes.txt", **kwargs):
    return upload(client, filename, text.encode("utf-8"), content_type="text/plain", **kwargs)


def assert_error(response, http_status, status, error_code):
    assert response.status_code == http_status, response.text
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["status"] == status
    assert body["error_code"] == error_code
    assert UUID(body["trace_id"]).version == 4
    return body


def count_rows(db_path, table):
    with sqlite3.connect(db_path) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def events_for(audit_log, trace_id):
    return [e for e in audit_log.events if e["trace_id"] == trace_id]


# ---------------------------------------------------------------------------
# Valid uploads
# ---------------------------------------------------------------------------

def test_valid_txt_upload(client):
    response = upload_txt(client, text=words(30))
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "status", "document_id", "filename", "file_type", "knowledge_scope",
        "page_count", "chunk_count", "trace_id",
    }
    assert body["status"] == "ready"
    assert body["filename"] == "notes.txt"
    assert body["file_type"] == "txt"
    assert body["knowledge_scope"] == "course_materials"
    assert body["page_count"] is None  # TXT has no pages
    assert body["chunk_count"] == 1
    assert UUID(body["document_id"]).version == 4
    assert UUID(body["trace_id"]).version == 4


def test_valid_pdf_upload(client):
    pdf = make_pdf(["First page about regulations.", "Second page about exams."])
    response = upload(client, "handbook.pdf", pdf, content_type="application/pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["file_type"] == "pdf"
    assert body["filename"] == "handbook.pdf"
    assert body["page_count"] == 2
    assert body["chunk_count"] == 2  # one chunk per page
    assert body["status"] == "ready"


def test_long_txt_is_split_into_overlapping_chunks(client):
    body = upload_txt(client, text=words(1200)).json()
    assert body["chunk_count"] == 3  # 500 + 500 + 300 words, 50 overlap


def test_pdf_chunks_keep_page_numbers(client):
    pdf = make_pdf([words(600, "a"), words(20, "b")])
    document_id = upload(client, "two.pdf", pdf).json()["document_id"]
    detail = client.get(f"/documents/{document_id}", params={"user_id": "admin001"}).json()
    assert [c["page_number"] for c in detail["chunks"]] == [1, 1, 2]


def test_pdf_with_some_blank_pages_keeps_original_numbering(client):
    pdf = make_pdf(["", "Only the second page has text", ""])
    body = upload(client, "mixed.pdf", pdf).json()
    assert body["page_count"] == 3
    detail = client.get(
        f"/documents/{body['document_id']}", params={"user_id": "admin001"}
    ).json()
    assert [c["page_number"] for c in detail["chunks"]] == [2]


def test_uppercase_extension_is_accepted(client):
    response = upload(client, "REPORT.PDF", make_pdf(["Some text"]))
    assert response.status_code == 201
    assert response.json()["file_type"] == "pdf"


def test_content_type_header_is_not_trusted(client):
    # A text file labelled as an image is still handled by its extension and content.
    response = upload(client, "notes.txt", b"plain text body", content_type="image/png")
    assert response.status_code == 201
    assert response.json()["file_type"] == "txt"


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_admin_may_upload_to_every_scope(client, scope):
    response = upload_txt(client, scope=scope)
    assert response.status_code == 201
    assert response.json()["knowledge_scope"] == scope


def test_same_file_uploaded_twice_creates_two_documents(client):
    first = upload_txt(client).json()["document_id"]
    second = upload_txt(client).json()["document_id"]
    assert first != second
    listing = client.get("/documents", params={"user_id": "admin001"}).json()
    assert listing["total"] == 2


def test_uploaded_code_is_stored_as_text_and_never_executed(client):
    code = "import os\nos.environ['UPLOAD_WAS_EXECUTED'] = 'yes'\n"
    document_id = upload(client, "script.txt", code.encode()).json()["document_id"]
    assert "UPLOAD_WAS_EXECUTED" not in os.environ
    detail = client.get(f"/documents/{document_id}", params={"user_id": "admin001"}).json()
    assert "import os" in detail["chunks"][0]["text_preview"]


# ---------------------------------------------------------------------------
# Rejected files
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename",
    ["slides.docx", "program.exe", "image.PNG", "noextension", "archive.pdf.exe",
     "script.txt.sh", "data.csv", "page.html"],
)
def test_unsupported_file_types_are_rejected(client, filename, isolated_database):
    response = upload(client, filename, b"some content")
    assert_error(response, 415, "rejected", "unsupported_file_type")
    assert ".pdf" in response.json()["reason"] and ".txt" in response.json()["reason"]
    assert count_rows(isolated_database, "documents") == 0


def test_oversized_upload_is_rejected(client, isolated_database):
    too_big = b"a" * (config.MAX_UPLOAD_BYTES + 1)  # 10 MiB + 1 byte
    response = upload(client, "huge.txt", too_big)
    body = assert_error(response, 413, "rejected", "file_too_large")
    assert "10 MB" in body["reason"]
    assert count_rows(isolated_database, "documents") == 0


def test_upload_limit_boundary(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 100)
    assert upload(client, "exact.txt", b"a" * 100).status_code == 201
    assert_error(upload(client, "over.txt", b"a" * 101), 413, "rejected", "file_too_large")


def test_empty_file_is_rejected(client, isolated_database):
    assert_error(upload(client, "empty.txt", b""), 422, "rejected", "empty_file")
    assert_error(upload(client, "empty.pdf", b""), 422, "rejected", "empty_file")
    assert count_rows(isolated_database, "documents") == 0


@pytest.mark.parametrize("content", [b"   \n\t\r\n   ", b"\n\n\n"])
def test_whitespace_only_txt_is_rejected(client, content):
    assert_error(upload(client, "blank.txt", content), 422, "rejected", "no_text")


def test_scanned_style_pdf_is_rejected_without_pretending_to_ocr(client, isolated_database):
    response = upload(client, "scan.pdf", make_pdf(["", "", ""]))
    body = assert_error(response, 422, "rejected", "no_text")
    assert "OCR is not supported" in body["reason"]
    assert count_rows(isolated_database, "documents") == 0


@pytest.mark.parametrize(
    "content",
    [
        b"%PDF-1.4\nthis is not a real pdf body",
        b"just plain text with a pdf extension",
        make_pdf(["Some text here"])[:100],
        b"%PDF-1.7\n" + os.urandom(512),
    ],
    ids=["bad-body", "no-header", "truncated", "random-bytes"],
)
def test_malformed_pdf_is_rejected_safely(client, content, isolated_database):
    response = upload(client, "broken.pdf", content)
    assert response.status_code == 422
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["error_code"] in {"malformed_pdf", "no_text"}
    assert "Traceback" not in response.text
    assert "pypdf" not in response.text
    assert count_rows(isolated_database, "documents") == 0


def test_password_protected_pdf_is_rejected(client, isolated_database):
    try:
        data = make_encrypted_pdf()
    except Exception:  # this pypdf install cannot write encrypted files
        pytest.skip("cannot build an encrypted PDF here")
    body = assert_error(upload(client, "locked.pdf", data), 422, "rejected", "encrypted_pdf")
    assert "password" in body["reason"]
    assert count_rows(isolated_database, "documents") == 0


def test_invalid_utf8_txt_gives_clear_error(client):
    response = upload(client, "latin1.txt", "café résumé".encode("latin-1"))
    body = assert_error(response, 422, "rejected", "invalid_encoding")
    assert "UTF-8" in body["reason"]


def test_binary_file_renamed_to_txt_is_rejected(client):
    assert_error(upload(client, "fake.txt", b"abc\x00\x01\x02def"), 422, "rejected", "binary_content")


# ---------------------------------------------------------------------------
# Authorization and scope
# ---------------------------------------------------------------------------

def test_unknown_user_cannot_upload(client, isolated_database):
    response = upload_txt(client, user_id="hacker999")
    assert_error(response, 401, "refused", "unknown_user")
    assert count_rows(isolated_database, "documents") == 0


@pytest.mark.parametrize("user_id", ["student001", "researcher001"])
def test_non_admin_cannot_upload(client, user_id, isolated_database):
    response = upload_txt(client, user_id=user_id)
    body = assert_error(response, 403, "refused", "not_admin")
    assert user_id not in body["reason"]
    assert count_rows(isolated_database, "documents") == 0


def test_admin_identity_is_exact(client):
    for user_id in ["ADMIN001", "admin", "admin001 ; x", "admin0011"]:
        assert upload_txt(client, user_id=user_id).status_code == 401


def test_authorization_is_checked_before_the_file_is_examined(client):
    # A student uploading a broken, unsupported file must see the auth error.
    response = upload(client, "virus.exe", b"MZ\x90", user_id="student001")
    assert_error(response, 403, "refused", "not_admin")


def test_invalid_knowledge_scope_is_rejected(client, isolated_database):
    response = upload_txt(client, scope="made_up_scope")
    body = assert_error(response, 422, "rejected", "invalid_scope")
    assert "Access denied" in body["reason"]
    assert "made_up_scope" in body["reason"]
    assert count_rows(isolated_database, "documents") == 0


def test_blank_knowledge_scope_is_rejected(client):
    assert_error(upload_txt(client, scope="   "), 422, "rejected", "invalid_scope")


def test_scope_uses_existing_access_control_logic(client, monkeypatch):
    from routers import documents

    calls = []

    def fake_check_access(role, scopes):
        calls.append((role, scopes))
        return {"allowed": False, "reason": "blocked by fake policy"}

    monkeypatch.setattr(documents, "check_access", fake_check_access)
    response = upload_txt(client)
    assert_error(response, 422, "rejected", "invalid_scope")
    assert response.json()["reason"] == "blocked by fake policy"
    assert calls == [("admin", ["course_materials"])]


# ---------------------------------------------------------------------------
# Request validation (Pydantic / FastAPI) uses the /documents error shape
# ---------------------------------------------------------------------------

def test_missing_file_returns_structured_422(client):
    response = client.post(
        "/documents/upload", data={"user_id": "admin001", "knowledge_scope": "course_materials"}
    )
    body = assert_error(response, 422, "rejected", "invalid_request")
    assert "file" in body["reason"]


@pytest.mark.parametrize("missing", ["user_id", "knowledge_scope"])
def test_missing_form_fields_return_structured_422(client, missing):
    data = {"user_id": "admin001", "knowledge_scope": "course_materials"}
    del data[missing]
    response = client.post(
        "/documents/upload", data=data, files={"file": ("a.txt", b"hello there", "text/plain")}
    )
    body = assert_error(response, 422, "rejected", "invalid_request")
    assert missing in body["reason"]


def test_overlong_user_id_is_rejected(client):
    assert_error(upload_txt(client, user_id="u" * 65), 422, "rejected", "invalid_request")


def test_validation_error_shapes_are_path_specific(client):
    # /query keeps its original shape ...
    query = client.post("/query", json={"question": "hi"})
    assert query.status_code == 422
    assert set(query.json()) == {"status", "answer", "citations", "refusal_reason", "trace_id"}
    # ... while /documents uses the document error shape.
    docs = client.get("/documents")
    assert docs.status_code == 422
    assert set(docs.json()) == ERROR_KEYS


# ---------------------------------------------------------------------------
# Filename and path traversal safety
# ---------------------------------------------------------------------------

TRAVERSAL_CASES = [
    ("../../evil.txt", "evil.txt"),
    ("..\\..\\evil.txt", "evil.txt"),
    ("/etc/evil.txt", "evil.txt"),
    ("C:\\Windows\\evil.txt", "evil.txt"),
    ("....//evil.txt", "evil.txt"),
    ("%2e%2e%2fevil.txt", "2e_2e_2fevil.txt"),
    ("<script>alert(1)</script>.txt", None),
    ("o'brien; DROP TABLE documents;--.txt", None),
]


@pytest.mark.parametrize("raw_name, expected", TRAVERSAL_CASES)
def test_dangerous_filenames_are_sanitized_and_never_written_to_disk(
    client, raw_name, expected, tmp_path, isolated_database
):
    response = upload(client, raw_name, b"harmless text content")
    assert response.status_code == 201, response.text
    stored_name = response.json()["filename"]

    if expected is not None:
        assert stored_name == expected
    for bad in ("/", "\\", "..", "<", ">", "'", ";"):
        assert bad not in stored_name
    assert stored_name.endswith(".txt")

    # Nothing but the temporary database exists in the test folder or its parent.
    created = {p.name for p in tmp_path.iterdir()}
    assert created <= {isolated_database.name, isolated_database.name + "-journal"}
    assert not (tmp_path.parent / "evil.txt").exists()
    assert not (tmp_path.parent.parent / "evil.txt").exists()
    assert not os.path.exists(os.path.join(os.getcwd(), "evil.txt"))

    # The database is intact after the SQL-looking filename.
    assert count_rows(isolated_database, "documents") == 1


def test_filename_made_only_of_dots_gets_a_default_name_and_is_rejected_by_type(client):
    # "..." has no extension once sanitized, so the type check rejects it.
    assert_error(upload(client, "...", b"text"), 415, "rejected", "unsupported_file_type")


def test_sanitized_name_is_what_the_api_returns_everywhere(client):
    document_id = upload(client, "../../secret/plan.txt", b"some text here").json()["document_id"]
    listing = client.get("/documents", params={"user_id": "admin001"}).json()
    detail = client.get(f"/documents/{document_id}", params={"user_id": "admin001"}).json()
    assert listing["documents"][0]["filename"] == "plan.txt"
    assert detail["document"]["filename"] == "plan.txt"
    assert "secret" not in client.get("/documents", params={"user_id": "admin001"}).text


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_list_is_empty_at_first(client):
    response = client.get("/documents", params={"user_id": "admin001"})
    assert response.status_code == 200
    body = response.json()
    assert body["documents"] == []
    assert body["total"] == 0
    assert UUID(body["trace_id"]).version == 4


def test_list_returns_metadata_newest_first_without_chunk_text(client):
    first = upload_txt(client, text="first document text", filename="first.txt").json()
    second = upload(client, "second.pdf", make_pdf(["second doc"]), scope="research_documents").json()

    response = client.get("/documents", params={"user_id": "admin001"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [d["document_id"] for d in body["documents"]] == [
        second["document_id"], first["document_id"],
    ]

    newest = body["documents"][0]
    assert set(newest) == {
        "document_id", "filename", "file_type", "knowledge_scope", "uploaded_by",
        "uploaded_at", "page_count", "chunk_count", "word_count", "file_size_bytes", "status",
    }
    assert newest["filename"] == "second.pdf"
    assert newest["file_type"] == "pdf"
    assert newest["knowledge_scope"] == "research_documents"
    assert newest["uploaded_by"] == "admin001"
    assert newest["page_count"] == 1
    assert newest["status"] == "ready"
    assert newest["uploaded_at"]
    assert "first document text" not in response.text


@pytest.mark.parametrize("user_id, http_status", [("student001", 403), ("researcher001", 403), ("nobody", 401)])
def test_only_admin_can_list(client, user_id, http_status):
    upload_txt(client)
    response = client.get("/documents", params={"user_id": user_id})
    assert response.status_code == http_status
    assert set(response.json()) == ERROR_KEYS
    assert "documents" not in response.json()


# ---------------------------------------------------------------------------
# Details
# ---------------------------------------------------------------------------

def test_document_details_include_metadata_and_chunks(client):
    text = words(1200)
    uploaded = upload_txt(client, text=text, filename="long.txt").json()
    response = client.get(
        f"/documents/{uploaded['document_id']}", params={"user_id": "admin001"}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["document"]["document_id"] == uploaded["document_id"]
    assert body["document"]["filename"] == "long.txt"
    assert body["document"]["chunk_count"] == 3
    assert body["document"]["word_count"] == 1200
    assert body["document"]["file_size_bytes"] == len(text.encode())

    chunks = body["chunks"]
    assert [c["chunk_index"] for c in chunks] == [0, 1, 2]
    assert [c["word_count"] for c in chunks] == [500, 500, 300]
    assert all(c["page_number"] is None for c in chunks)
    assert all(
        c["chunk_id"] == f"{uploaded['document_id']}_chunk_{c['chunk_index']:04d}" for c in chunks
    )
    assert chunks[0]["text_preview"].startswith("word0 word1")
    assert all(len(c["text_preview"]) <= 200 for c in chunks)
    assert len({c["chunk_id"] for c in chunks}) == 3


def test_nonexistent_document_returns_404(client):
    response = client.get(f"/documents/{uuid4()}", params={"user_id": "admin001"})
    body = assert_error(response, 404, "not_found", "document_not_found")
    assert body["reason"] == "Document does not exist."


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "123", "1; DROP TABLE documents", "..%2f..%2fetc"])
def test_malformed_document_id_returns_structured_422(client, bad_id):
    for method in (client.get, client.delete):
        response = method(f"/documents/{bad_id}", params={"user_id": "admin001"})
        assert response.status_code in {404, 422}
        if response.status_code == 422:
            assert set(response.json()) == ERROR_KEYS


@pytest.mark.parametrize("user_id, http_status", [("student001", 403), ("researcher001", 403), ("nobody", 401)])
def test_only_admin_can_view_details(client, user_id, http_status):
    document_id = upload_txt(client).json()["document_id"]
    response = client.get(f"/documents/{document_id}", params={"user_id": user_id})
    assert response.status_code == http_status
    assert "chunks" not in response.json()


def test_missing_user_id_query_parameter_returns_422(client):
    document_id = upload_txt(client).json()["document_id"]
    assert_error(client.get(f"/documents/{document_id}"), 422, "rejected", "invalid_request")
    assert_error(client.delete(f"/documents/{document_id}"), 422, "rejected", "invalid_request")


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def test_delete_removes_document_and_its_chunks(client, isolated_database):
    uploaded = upload_txt(client, text=words(1200)).json()
    document_id = uploaded["document_id"]
    assert count_rows(isolated_database, "chunks") == 3

    response = client.delete(f"/documents/{document_id}", params={"user_id": "admin001"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "deleted"
    assert body["document_id"] == document_id
    assert body["chunks_deleted"] == 3
    assert UUID(body["trace_id"]).version == 4

    # Gone from the API ...
    assert client.get(f"/documents/{document_id}", params={"user_id": "admin001"}).status_code == 404
    assert client.get("/documents", params={"user_id": "admin001"}).json()["total"] == 0
    # ... and from the database itself.
    assert count_rows(isolated_database, "documents") == 0
    assert count_rows(isolated_database, "chunks") == 0


def test_deleting_one_document_keeps_the_others_chunks(client, isolated_database):
    keep = upload_txt(client, text=words(1200), filename="keep.txt").json()
    remove = upload_txt(client, text=words(600), filename="remove.txt").json()
    assert count_rows(isolated_database, "chunks") == keep["chunk_count"] + remove["chunk_count"]

    client.delete(f"/documents/{remove['document_id']}", params={"user_id": "admin001"})

    assert count_rows(isolated_database, "chunks") == keep["chunk_count"]
    with sqlite3.connect(isolated_database) as connection:
        owners = {row[0] for row in connection.execute("SELECT DISTINCT document_id FROM chunks")}
    assert owners == {keep["document_id"]}


def test_delete_nonexistent_document_returns_404(client):
    response = client.delete(f"/documents/{uuid4()}", params={"user_id": "admin001"})
    assert_error(response, 404, "not_found", "document_not_found")


def test_deleting_twice_returns_404_the_second_time(client):
    document_id = upload_txt(client).json()["document_id"]
    assert client.delete(f"/documents/{document_id}", params={"user_id": "admin001"}).status_code == 200
    assert client.delete(f"/documents/{document_id}", params={"user_id": "admin001"}).status_code == 404


@pytest.mark.parametrize("user_id", ["student001", "researcher001", "nobody"])
def test_non_admin_cannot_delete_and_document_survives(client, user_id, isolated_database):
    document_id = upload_txt(client).json()["document_id"]
    response = client.delete(f"/documents/{document_id}", params={"user_id": user_id})
    assert response.status_code in {401, 403}
    assert count_rows(isolated_database, "documents") == 1
    assert count_rows(isolated_database, "chunks") == 1


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------

def test_tables_are_created_automatically_at_startup(isolated_database):
    assert not isolated_database.exists()
    with TestClient(main.app):
        pass  # entering the context runs the startup code
    assert isolated_database.exists()
    with sqlite3.connect(isolated_database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"documents", "chunks"} <= tables


def test_uploaded_data_is_really_stored_in_sqlite(client, isolated_database):
    text = "alpha beta gamma delta"
    uploaded = upload_txt(client, text=text, filename="stored.txt").json()

    with sqlite3.connect(isolated_database) as connection:
        connection.row_factory = sqlite3.Row
        document = connection.execute(
            "SELECT * FROM documents WHERE document_id = ?", (uploaded["document_id"],)
        ).fetchone()
        chunk = connection.execute(
            "SELECT * FROM chunks WHERE document_id = ?", (uploaded["document_id"],)
        ).fetchone()

    assert document["filename"] == "stored.txt"
    assert document["original_filename"] == "stored.txt"
    assert document["file_type"] == "txt"
    assert document["knowledge_scope"] == "course_materials"
    assert document["uploaded_by"] == "admin001"
    assert document["uploaded_at"]
    assert document["page_count"] is None
    assert document["chunk_count"] == 1
    assert document["word_count"] == 4
    assert document["file_size_bytes"] == len(text)
    assert len(document["content_sha256"]) == 64
    assert document["status"] == "ready"
    assert chunk["chunk_id"] == f"{uploaded['document_id']}_chunk_0000"
    assert chunk["chunk_index"] == 0
    assert chunk["text"] == text
    assert chunk["page_number"] is None


def test_data_survives_an_application_restart(isolated_database):
    with TestClient(main.app) as first_run:
        document_id = upload_txt(first_run, text="persistent text").json()["document_id"]

    with TestClient(main.app) as second_run:  # a fresh startup, same database file
        listing = second_run.get("/documents", params={"user_id": "admin001"}).json()
        assert [d["document_id"] for d in listing["documents"]] == [document_id]


def test_tests_use_a_temporary_database_not_the_real_one(isolated_database):
    assert database.get_db_path() == isolated_database
    assert database.get_db_path() != database.DEFAULT_DB_PATH
    assert database.DEFAULT_DB_PATH.name == "knowledge.db"
    assert database.DEFAULT_DB_PATH.parent.name == "data"


# ---------------------------------------------------------------------------
# Safe failure handling
# ---------------------------------------------------------------------------

def test_database_error_during_upload_returns_generic_500(client, monkeypatch, isolated_database):
    def broken_insert(document, chunks):
        raise sqlite3.OperationalError("disk I/O error at C:\\secret\\path")

    monkeypatch.setattr(database, "insert_document", broken_insert)
    response = client.post(
        "/documents/upload",
        data={"user_id": "admin001", "knowledge_scope": "course_materials"},
        files={"file": ("a.txt", b"hello there", "text/plain")},
    )
    body = assert_error(response, 500, "error", "storage_error")
    assert "secret" not in response.text
    assert "OperationalError" not in response.text
    assert body["reason"] == "A storage error occurred. Please try again later."


@pytest.mark.parametrize(
    "function_name, call",
    [
        ("list_documents", lambda c: c.get("/documents", params={"user_id": "admin001"})),
        ("get_document", lambda c: c.get(f"/documents/{uuid4()}", params={"user_id": "admin001"})),
        ("get_document", lambda c: c.delete(f"/documents/{uuid4()}", params={"user_id": "admin001"})),
    ],
    ids=["list", "view", "delete"],
)
def test_database_errors_on_read_and_delete_return_generic_500(
    client, monkeypatch, function_name, call
):
    def broken(*args, **kwargs):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(database, function_name, broken)
    response = call(client)
    assert_error(response, 500, "error", "storage_error")
    assert "malformed" not in response.text


def test_unexpected_exception_returns_generic_500_in_document_shape(safe_client, monkeypatch):
    from routers import documents

    def explode(file_type, data):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(documents, "extract_text", explode)
    response = upload_txt(safe_client)
    assert_error(response, 500, "error", "internal_error")
    assert "secret internal detail" not in response.text


def test_failed_storage_leaves_no_partial_document(client, monkeypatch, isolated_database):
    # Make the chunk insert fail halfway: the document row must roll back too.
    from ingestion.chunking import Chunk

    original = database.insert_document

    def insert_with_duplicate_chunk(document, chunks):
        original(document, [chunks[0], Chunk(*chunks[0].__dict__.values())])

    monkeypatch.setattr(database, "insert_document", insert_with_duplicate_chunk)
    response = upload_txt(client)
    assert response.status_code == 500
    assert count_rows(isolated_database, "documents") == 0
    assert count_rows(isolated_database, "chunks") == 0


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------

def test_successful_upload_is_audited_without_content(client, audit_log):
    secret = "ZEBRA-SECRET-CONTENT-12345"
    response = upload_txt(client, text=f"{secret} " + words(20))
    body = response.json()

    events = events_for(audit_log, body["trace_id"])
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "document_event"
    assert event["action"] == "upload"
    assert event["decision"] == "allow"
    assert event["stage"] == "complete"
    assert event["user_id"] == "admin001"
    assert event["role"] == "admin"
    assert event["document_id"] == body["document_id"]
    assert event["filename"] == "notes.txt"
    assert event["file_type"] == "txt"
    assert event["knowledge_scope"] == "course_materials"
    assert event["chunk_count"] == 1
    assert event["http_status"] == 201
    assert event["file_size_bytes"] > 0
    assert event["timestamp"]

    assert not any(secret in line for line in audit_log.raw)


@pytest.mark.parametrize(
    "make_request, http_status, decision, stage",
    [
        (lambda c: upload(c, "evil.exe", b"x"), 415, "reject", "file"),
        (lambda c: upload(c, "broken.pdf", b"%PDF-1.4 junk"), 422, "reject", "extraction"),
        (lambda c: upload(c, "empty.txt", b""), 422, "reject", "extraction"),
        (lambda c: upload_txt(c, scope="made_up_scope"), 422, "reject", "scope"),
        (lambda c: upload_txt(c, user_id="hacker999"), 401, "deny", "identity"),
        (lambda c: upload_txt(c, user_id="student001"), 403, "deny", "role"),
    ],
    ids=["unsupported", "malformed-pdf", "empty", "bad-scope", "unknown-user", "student"],
)
def test_rejected_uploads_are_audited_with_reason(client, audit_log, make_request, http_status,
                                                  decision, stage):
    response = make_request(client)
    assert response.status_code == http_status
    events = events_for(audit_log, response.json()["trace_id"])
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "upload"
    assert event["decision"] == decision
    assert event["stage"] == stage
    assert event["http_status"] == http_status
    assert event["reason"]
    assert event["document_id"] is None


def test_deletion_is_audited(client, audit_log):
    uploaded = upload_txt(client, text=words(600), filename="gone.txt").json()
    response = client.delete(
        f"/documents/{uploaded['document_id']}", params={"user_id": "admin001"}
    )
    events = events_for(audit_log, response.json()["trace_id"])
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "delete"
    assert event["decision"] == "allow"
    assert event["user_id"] == "admin001"
    assert event["role"] == "admin"
    assert event["document_id"] == uploaded["document_id"]
    assert event["filename"] == "gone.txt"
    assert event["chunk_count"] == uploaded["chunk_count"]


def test_denied_and_missing_deletes_are_audited(client, audit_log):
    document_id = upload_txt(client).json()["document_id"]

    denied = client.delete(f"/documents/{document_id}", params={"user_id": "student001"})
    denied_event = events_for(audit_log, denied.json()["trace_id"])[0]
    assert (denied_event["action"], denied_event["decision"]) == ("delete", "deny")
    assert denied_event["document_id"] == document_id

    missing = client.delete(f"/documents/{uuid4()}", params={"user_id": "admin001"})
    missing_event = events_for(audit_log, missing.json()["trace_id"])[0]
    assert (missing_event["action"], missing_event["decision"]) == ("delete", "reject")
    assert missing_event["http_status"] == 404


def test_list_and_view_are_audited(client, audit_log):
    document_id = upload_txt(client).json()["document_id"]
    listing = client.get("/documents", params={"user_id": "admin001"}).json()
    detail = client.get(f"/documents/{document_id}", params={"user_id": "admin001"}).json()

    assert events_for(audit_log, listing["trace_id"])[0]["action"] == "list"
    view_event = events_for(audit_log, detail["trace_id"])[0]
    assert view_event["action"] == "view"
    assert view_event["document_id"] == document_id


def test_audit_log_never_contains_dangerous_filename_characters(client, audit_log):
    upload(client, "../../x\ny.txt", b"some text")
    for line in audit_log.raw:
        assert "\n" not in line
        assert "../" not in line


def test_database_failure_is_audited_as_error(client, audit_log, monkeypatch):
    def broken_insert(document, chunks):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(database, "insert_document", broken_insert)
    response = upload_txt(client)
    event = events_for(audit_log, response.json()["trace_id"])[0]
    assert event["decision"] == "error"
    assert event["stage"] == "storage"
    assert "OperationalError" in event["reason"]


def test_every_document_response_has_a_unique_trace_id(client):
    seen = set()
    for _ in range(5):
        seen.add(upload_txt(client).json()["trace_id"])
        seen.add(client.get("/documents", params={"user_id": "admin001"}).json()["trace_id"])
        seen.add(upload_txt(client, user_id="nobody").json()["trace_id"])
    assert len(seen) == 15


# ---------------------------------------------------------------------------
# Existing behaviour is preserved
# ---------------------------------------------------------------------------

def test_existing_endpoints_still_work(client):
    assert client.get("/").json() == {
        "message": "AI Knowledge Platform is running!",
        "status": "operational",
    }
    assert client.get("/health").json() == {"status": "healthy"}
    query = client.post(
        "/query",
        json={
            "question": "What are the exam rules?",
            "user_id": "student001",
            "purpose": "academic",
            "knowledge_scope": ["course_materials"],
        },
    )
    assert query.status_code == 200
    assert query.json()["status"] == "needs_review"


def test_uploading_does_not_change_query_behaviour(client):
    upload_txt(client)
    body = client.post(
        "/query",
        json={
            "question": "What are the exam rules?",
            "user_id": "student001",
            "purpose": "academic",
            "knowledge_scope": ["course_materials"],
        },
    ).json()
    assert body["status"] == "needs_review"  # retrieval is still a later task
    assert body["answer"] is None


def test_openapi_documents_the_new_endpoints(client):
    schema = client.get("/openapi.json").json()
    assert "post" in schema["paths"]["/documents/upload"]
    assert "get" in schema["paths"]["/documents"]
    assert "get" in schema["paths"]["/documents/{document_id}"]
    assert "delete" in schema["paths"]["/documents/{document_id}"]
    assert "Documents" in schema["paths"]["/documents/upload"]["post"]["tags"]
