"""Settings for the document ingestion pipeline (all in one place)."""

# Largest upload accepted, in bytes (10 MiB).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Only these extensions are accepted. The value is the stored file type.
ALLOWED_EXTENSIONS = {".pdf": "pdf", ".txt": "txt"}

# Refuse PDFs with more pages than this (protects memory and CPU).
MAX_PDF_PAGES = 500

# Chunking defaults: about 500 words per chunk, 50 words shared with the
# previous chunk so a sentence cut at a boundary still appears whole once.
CHUNK_SIZE_WORDS = 500
CHUNK_OVERLAP_WORDS = 50

# Longest sanitized filename kept (including the extension).
MAX_FILENAME_LENGTH = 100

# Document status written when extraction, chunking and storage all succeeded.
# Later tasks may add "indexed" once embeddings exist.
STATUS_READY = "ready"
