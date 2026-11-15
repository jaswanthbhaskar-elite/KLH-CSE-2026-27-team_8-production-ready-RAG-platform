"""
Text extraction from uploaded files.

Supported: PDF (text layer only, via pypdf) and TXT (UTF-8).
NOT supported: OCR. A scanned PDF is an image of text, so it has no text
layer and is rejected with a clear message instead of being "read" by guesswork.

This module only works on bytes in memory. It never touches the filesystem.
"""

import io
import logging
from dataclasses import dataclass

from pypdf import PdfReader

from ingestion import config

logger = logging.getLogger("knowledge_platform.ingestion")


class ExtractionError(Exception):
    """
    Raised when a file cannot be turned into text.

    `code` is a short machine-readable reason and `message` is safe to show
    to API clients (it never contains internal exception details).
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PageText:
    """Text of one page. page_number is None for formats without pages (TXT)."""

    page_number: int | None
    text: str


@dataclass(frozen=True)
class ExtractionResult:
    """Extracted pages plus the total page count (None for TXT)."""

    pages: list[PageText]
    page_count: int | None


def extract_text(file_type: str, data: bytes) -> ExtractionResult:
    """Extract text from file bytes. file_type is 'pdf' or 'txt'."""
    if not data:
        raise ExtractionError("empty_file", "The uploaded file is empty.")

    if file_type == "txt":
        return extract_txt(data)
    if file_type == "pdf":
        return extract_pdf(data)

    raise ExtractionError("unsupported_type", "Unsupported file type.")


def extract_txt(data: bytes) -> ExtractionResult:
    """Decode a UTF-8 text file (a leading BOM is accepted and removed)."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExtractionError(
            "invalid_encoding",
            "The text file is not valid UTF-8. Please re-save it as UTF-8 and upload again.",
        ) from None

    # A NUL character means this is binary data renamed to .txt.
    if "\x00" in text:
        raise ExtractionError(
            "binary_content", "The file contains binary data and is not a text file."
        )

    text = text.strip()
    if not text:
        raise ExtractionError("no_text", "The file contains no text.")

    return ExtractionResult(pages=[PageText(page_number=None, text=text)], page_count=None)


def extract_pdf(data: bytes) -> ExtractionResult:
    """
    Extract the text layer of a PDF, one PageText per page that has text.

    Page numbers start at 1 and match the real page in the PDF, even when
    some pages (for example scanned images) have no text and are skipped.
    """
    # Every real PDF starts with "%PDF-" near the beginning.
    if b"%PDF-" not in data[:1024]:
        raise ExtractionError(
            "malformed_pdf", "The file is not a valid PDF (missing PDF header)."
        )

    pages: list[PageText] = []
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)

        if reader.is_encrypted:
            raise ExtractionError(
                "encrypted_pdf",
                "The PDF is password-protected. Remove the password and upload again.",
            )

        total_pages = len(reader.pages)
        if total_pages > config.MAX_PDF_PAGES:
            raise ExtractionError(
                "too_many_pages",
                f"The PDF has too many pages (limit is {config.MAX_PDF_PAGES}).",
            )

        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").replace("\x00", "").strip()
            if text:
                pages.append(PageText(page_number=page_number, text=text))

    except ExtractionError:
        raise
    except Exception as error:  # noqa: BLE001 - pypdf can raise many error types
        # Broad catch on purpose: a hostile or damaged PDF must never crash the
        # API. Only the exception TYPE is logged, never the file contents.
        logger.warning("PDF parsing failed: %s", type(error).__name__)
        raise ExtractionError(
            "malformed_pdf", "The PDF is damaged or could not be read."
        ) from None

    if not pages:
        raise ExtractionError(
            "no_text",
            "The PDF has no extractable text. It may be a scanned document. "
            "OCR is not supported, so please upload a PDF with selectable text.",
        )

    return ExtractionResult(pages=pages, page_count=total_pages)
