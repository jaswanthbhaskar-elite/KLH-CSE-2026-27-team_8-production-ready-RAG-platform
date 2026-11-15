"""Unit tests for the ingestion modules and the database layer (no HTTP)."""

import sqlite3

import pytest

import database
from helpers import make_encrypted_pdf, make_pdf, words
from ingestion import config
from ingestion.chunking import Chunk, chunk_pages, make_chunk_id
from ingestion.extraction import ExtractionError, PageText, extract_pdf, extract_text, extract_txt
from ingestion.filenames import clean_original_name, get_extension, sanitize_filename


# ---------------------------------------------------------------------------
# Filename sanitizing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("report.pdf", "report.pdf"),
        ("My Notes 2024.txt", "My Notes 2024.txt"),
        ("../../evil.txt", "evil.txt"),
        ("..\\..\\evil.txt", "evil.txt"),
        ("/etc/passwd.txt", "passwd.txt"),
        ("C:\\Windows\\system32\\evil.txt", "evil.txt"),
        ("....//evil.txt", "evil.txt"),
        (".hidden.txt", "hidden.txt"),
        ("name with <script>.txt", "name with _script.txt"),
        ("semi;colon&amp|pipe.pdf", "semi_colon_amp_pipe.pdf"),
        ("o'brien; DROP TABLE documents;--.txt", "o_brien_ DROP TABLE documents_--.txt"),
        ("line\nbreak\r.txt", "line_break.txt"),
        ("nul\x00byte.pdf", "nul_byte.pdf"),
        ("trailing dots...", "trailing dots"),
        (None, "document"),
        ("", "document"),
        ("...", "document"),
        (".pdf", "pdf"),
        ("   ", "document"),
        ("___.txt", "document.txt"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_never_contains_path_characters():
    nasty = ["../a.txt", "..\\a.txt", "a/b/c.txt", "a\\b\\c.txt", "%2e%2e%2fa.txt", "a:b.txt"]
    for raw in nasty:
        cleaned = sanitize_filename(raw)
        assert "/" not in cleaned
        assert "\\" not in cleaned
        assert ".." not in cleaned
        assert ":" not in cleaned


def test_sanitize_filename_truncates_but_keeps_extension():
    cleaned = sanitize_filename("a" * 500 + ".pdf")
    assert len(cleaned) == config.MAX_FILENAME_LENGTH
    assert cleaned.endswith(".pdf")


def test_get_extension_is_lowercase():
    assert get_extension("Report.PDF") == ".pdf"
    assert get_extension("noextension") == ""
    assert get_extension("archive.tar.gz") == ".gz"


def test_clean_original_name_strips_control_characters():
    assert clean_original_name("a\nb\x00c.txt") == "abc.txt"
    assert clean_original_name(None) == ""
    assert len(clean_original_name("x" * 1000)) == 255


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def page(text, number=None):
    return PageText(page_number=number, text=text)


def test_default_chunk_settings_are_500_and_50():
    assert config.CHUNK_SIZE_WORDS == 500
    assert config.CHUNK_OVERLAP_WORDS == 50


def test_short_text_gives_one_chunk():
    chunks = chunk_pages("doc1", [page(words(10))])
    assert len(chunks) == 1
    assert chunks[0].word_count == 10
    assert chunks[0].chunk_index == 0


def test_default_chunking_of_1200_words_gives_three_overlapping_chunks():
    chunks = chunk_pages("doc1", [page(words(1200))])
    # step = 500 - 50 = 450 -> starts at 0, 450, 900
    assert [c.word_count for c in chunks] == [500, 500, 300]
    first, second = chunks[0].text.split(), chunks[1].text.split()
    assert first[-50:] == second[:50]  # exactly 50 shared words


def test_custom_size_and_overlap():
    chunks = chunk_pages("d", [page(words(9))], chunk_size=5, overlap=2)
    assert [c.text.split()[0] for c in chunks] == ["word0", "word3", "word6"]
    assert [c.word_count for c in chunks] == [5, 5, 3]


def test_every_word_appears_in_some_chunk():
    chunks = chunk_pages("d", [page(words(1234))], chunk_size=100, overlap=10)
    seen = set()
    for chunk in chunks:
        seen.update(chunk.text.split())
    assert seen == set(words(1234).split())


def test_exact_multiple_does_not_create_an_extra_chunk():
    chunks = chunk_pages("d", [page(words(500))])
    assert len(chunks) == 1


def test_no_empty_chunks_and_blank_pages_are_skipped():
    pages = [page("   \n\t  ", 1), page(words(3), 2), page("", 3)]
    chunks = chunk_pages("d", pages)
    assert len(chunks) == 1
    assert chunks[0].page_number == 2
    assert all(c.text.strip() and c.word_count > 0 for c in chunks)


def test_empty_input_gives_no_chunks():
    assert chunk_pages("d", []) == []


def test_chunks_never_cross_pages_and_keep_page_numbers():
    pages = [page(words(700, "a"), 1), page(words(20, "b"), 2), page(words(600, "c"), 3)]
    chunks = chunk_pages("d", pages)
    for chunk in chunks:
        prefixes = {w[0] for w in chunk.text.split()}
        assert len(prefixes) == 1  # all words come from one page
    assert [c.page_number for c in chunks] == [1, 1, 2, 3, 3]


def test_chunk_index_is_continuous_across_pages():
    pages = [page(words(700, "a"), 1), page(words(700, "b"), 2)]
    chunks = chunk_pages("d", pages)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_txt_chunks_have_no_page_number():
    chunks = chunk_pages("d", [page(words(10), None)])
    assert chunks[0].page_number is None


def test_chunk_ids_are_stable_unique_and_carry_document_id():
    first = chunk_pages("doc-abc", [page(words(1200))])
    second = chunk_pages("doc-abc", [page(words(1200))])
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]  # stable
    ids = [c.chunk_id for c in first]
    assert len(ids) == len(set(ids))                                    # unique
    assert all(c.document_id == "doc-abc" for c in first)
    assert ids[0] == make_chunk_id("doc-abc", 0) == "doc-abc_chunk_0000"
    other = chunk_pages("doc-xyz", [page(words(1200))])
    assert not set(ids) & {c.chunk_id for c in other}                   # unique across docs


@pytest.mark.parametrize(
    "size, overlap",
    [(0, 0), (-5, 0), (10, -1), (10, 10), (10, 11)],
)
def test_invalid_chunk_settings_raise(size, overlap):
    with pytest.raises(ValueError):
        chunk_pages("d", [page("a b c")], chunk_size=size, overlap=overlap)


def test_chunk_is_immutable():
    chunk = chunk_pages("d", [page("a b c")])[0]
    with pytest.raises(Exception):
        chunk.text = "changed"  # frozen dataclass


# ---------------------------------------------------------------------------
# Text extraction: TXT
# ---------------------------------------------------------------------------

def test_txt_extraction_basic():
    result = extract_txt("Hello world\nsecond line".encode("utf-8"))
    assert result.page_count is None
    assert len(result.pages) == 1
    assert result.pages[0].page_number is None
    assert "second line" in result.pages[0].text


def test_txt_extraction_accepts_utf8_with_bom_and_unicode():
    result = extract_txt("\ufeffnaïve café – हिंदी".encode("utf-8"))
    assert result.pages[0].text == "naïve café – हिंदी"


def test_txt_invalid_utf8_gives_clear_error():
    with pytest.raises(ExtractionError) as info:
        extract_txt("café".encode("latin-1"))
    assert info.value.code == "invalid_encoding"
    assert "UTF-8" in info.value.message


def test_txt_binary_content_rejected():
    with pytest.raises(ExtractionError) as info:
        extract_txt(b"text\x00more text")
    assert info.value.code == "binary_content"


@pytest.mark.parametrize("data", [b"", b"   \n\t  \r\n"])
def test_txt_empty_or_whitespace_rejected(data):
    with pytest.raises(ExtractionError) as info:
        extract_text("txt", data)
    assert info.value.code in {"empty_file", "no_text"}


def test_extract_text_rejects_unknown_type():
    with pytest.raises(ExtractionError) as info:
        extract_text("docx", b"data")
    assert info.value.code == "unsupported_type"


# ---------------------------------------------------------------------------
# Text extraction: PDF
# ---------------------------------------------------------------------------

def test_pdf_extraction_keeps_page_numbers():
    result = extract_pdf(make_pdf(["First page text", "Second page text"]))
    assert result.page_count == 2
    assert [p.page_number for p in result.pages] == [1, 2]
    assert "First page" in result.pages[0].text
    assert "Second page" in result.pages[1].text


def test_pdf_blank_pages_are_skipped_but_numbering_is_kept():
    result = extract_pdf(make_pdf(["", "Real text here", ""]))
    assert result.page_count == 3
    assert [p.page_number for p in result.pages] == [2]


def test_scanned_style_pdf_is_rejected_and_mentions_ocr():
    with pytest.raises(ExtractionError) as info:
        extract_pdf(make_pdf(["", ""]))
    assert info.value.code == "no_text"
    assert "OCR is not supported" in info.value.message


@pytest.mark.parametrize(
    "data",
    [
        b"this is not a pdf at all",
        b"%PDF-1.4\nthis is garbage after a valid header",
        make_pdf(["Some text"])[:120],  # truncated file
        b"%PDF-1.7\n" + bytes(range(256)) * 20,
    ],
)
def test_malformed_pdfs_raise_extraction_error(data):
    with pytest.raises(ExtractionError) as info:
        extract_pdf(data)
    assert info.value.code in {"malformed_pdf", "no_text"}


def test_pdf_error_messages_do_not_leak_internals():
    with pytest.raises(ExtractionError) as info:
        extract_pdf(b"%PDF-1.4\ngarbage")
    message = info.value.message
    assert "Traceback" not in message
    assert "pypdf" not in message


def test_password_protected_pdf_is_rejected():
    try:
        data = make_encrypted_pdf()
    except Exception:  # this pypdf install cannot write encrypted files
        pytest.skip("cannot build an encrypted PDF here")
    with pytest.raises(ExtractionError) as info:
        extract_pdf(data)
    assert info.value.code == "encrypted_pdf"
    assert "password" in info.value.message


def test_pdf_page_limit(monkeypatch):
    monkeypatch.setattr(config, "MAX_PDF_PAGES", 1)
    with pytest.raises(ExtractionError) as info:
        extract_pdf(make_pdf(["one", "two"]))
    assert info.value.code == "too_many_pages"


# ---------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------

def sample_document(document_id="11111111-1111-4111-8111-111111111111", **overrides):
    document = {
        "document_id": document_id,
        "filename": "notes.txt",
        "original_filename": "notes.txt",
        "file_type": "txt",
        "knowledge_scope": "course_materials",
        "uploaded_by": "admin001",
        "uploaded_at": "2026-11-15T10:00:00+00:00",
        "page_count": None,
        "chunk_count": 2,
        "word_count": 6,
        "file_size_bytes": 30,
        "content_sha256": "0" * 64,
        "status": "ready",
    }
    document.update(overrides)
    return document


def sample_chunks(document_id="11111111-1111-4111-8111-111111111111"):
    return [
        Chunk(make_chunk_id(document_id, 0), document_id, 0, "alpha beta gamma", None, 3),
        Chunk(make_chunk_id(document_id, 1), document_id, 1, "delta epsilon zeta", None, 3),
    ]


def test_init_db_creates_tables_and_file(isolated_database):
    assert not isolated_database.exists()
    database.init_db()
    assert isolated_database.exists()
    with sqlite3.connect(isolated_database) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"documents", "chunks"} <= names


def test_database_path_comes_from_environment(isolated_database):
    assert database.get_db_path() == isolated_database
    assert database.get_db_path() != database.DEFAULT_DB_PATH


def test_insert_and_read_back():
    database.insert_document(sample_document(), sample_chunks())
    stored = database.get_document("11111111-1111-4111-8111-111111111111")
    assert stored["filename"] == "notes.txt"
    assert stored["status"] == "ready"
    chunks = database.get_chunks("11111111-1111-4111-8111-111111111111")
    assert [c["chunk_index"] for c in chunks] == [0, 1]
    assert chunks[0]["text"] == "alpha beta gamma"


def test_get_missing_document_returns_none():
    assert database.get_document("does-not-exist") is None
    assert database.get_chunks("does-not-exist") == []


def test_delete_removes_document_and_chunks():
    doc_id = "11111111-1111-4111-8111-111111111111"
    database.insert_document(sample_document(doc_id), sample_chunks(doc_id))
    assert database.delete_document(doc_id) == 2
    assert database.get_document(doc_id) is None
    assert database.get_chunks(doc_id) == []
    assert database.delete_document(doc_id) is None  # already gone


def test_failed_insert_is_rolled_back_completely():
    doc_id = "11111111-1111-4111-8111-111111111111"
    chunks = sample_chunks(doc_id)
    duplicated = [chunks[0], chunks[0]]  # same chunk_id twice -> IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        database.insert_document(sample_document(doc_id), duplicated)
    # The document row must NOT be left behind without its chunks.
    assert database.get_document(doc_id) is None


def test_chunk_requires_an_existing_document():
    orphan = sample_chunks("ghost")
    with pytest.raises(sqlite3.IntegrityError):
        with database.get_connection() as connection:
            connection.executemany(
                "INSERT INTO chunks (chunk_id, document_id, chunk_index, text, page_number, word_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(c.chunk_id, c.document_id, c.chunk_index, c.text, c.page_number, c.word_count)
                 for c in orphan],
            )


def test_sql_injection_text_is_stored_as_plain_data():
    doc_id = "11111111-1111-4111-8111-111111111111"
    nasty = "x'); DROP TABLE documents; --"
    database.insert_document(sample_document(doc_id, filename=nasty), sample_chunks(doc_id))
    assert database.get_document(doc_id)["filename"] == nasty
    assert len(database.list_documents()) == 1  # table still exists
    assert database.get_document(nasty) is None  # lookups are parameterized too


def test_list_documents_newest_first():
    database.insert_document(
        sample_document("a" * 8 + "-1111-4111-8111-111111111111",
                        uploaded_at="2026-01-01T00:00:00+00:00"),
        [],
    )
    database.insert_document(
        sample_document("b" * 8 + "-1111-4111-8111-111111111111",
                        uploaded_at="2026-06-01T00:00:00+00:00"),
        [],
    )
    ids = [d["document_id"][0] for d in database.list_documents()]
    assert ids == ["b", "a"]
