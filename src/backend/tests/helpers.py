"""Shared test helpers (no third-party packages needed)."""


def make_pdf(pages: list[str]) -> bytes:
    """
    Build a small valid PDF with one text string per page.

    An empty string makes a page with no text at all. That imitates a
    scanned PDF, whose pages are pictures with no text layer.
    """
    page_count = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(page_count))

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    for i, text in enumerate(pages):
        content_object_number = 5 + 2 * i
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_object_number} 0 R "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ).encode()
        )
        if text:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream = f"BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET".encode("latin-1")
        else:
            stream = b""
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"
        )

    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_position = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode()
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_position}\n%%EOF\n"
    ).encode()
    return bytes(output)


def words(count: int, prefix: str = "word") -> str:
    """Return `count` distinct words: 'word0 word1 word2 ...'."""
    return " ".join(f"{prefix}{i}" for i in range(count))


def make_encrypted_pdf(text: str = "secret text") -> bytes:
    """
    Build a password-protected PDF using pypdf itself.

    Raises an exception if this pypdf install cannot write encrypted files;
    callers should skip the test in that case.
    """
    import io

    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(make_pdf([text])))
    writer = PdfWriter(clone_from=reader)
    writer.encrypt("password", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
