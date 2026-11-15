"""
Filename sanitizing.

The uploaded filename is attacker-controlled text. This project never uses
it as a filesystem path (uploads are processed in memory and only text is
stored in SQLite). We still clean it so that it is safe to store, log and
show in a future frontend.
"""

import os
import re

from ingestion import config

DEFAULT_STEM = "document"

# Anything outside this small safe set becomes an underscore.
_UNSAFE_CHARACTERS = re.compile(r"[^A-Za-z0-9._ -]")
_REPEATED_UNDERSCORES = re.compile(r"_{2,}")
_REPEATED_SPACES = re.compile(r" {2,}")


def sanitize_filename(raw_name: str | None) -> str:
    """
    Turn an untrusted filename into a safe display name.

    - Drops any folder part, for both "/" and "\\" separators
      ("../../evil.txt" -> "evil.txt").
    - Replaces every character outside A-Z a-z 0-9 . _ - and space with "_".
    - Removes leading dots (no hidden files) and trailing dots/spaces.
    - Cuts the name to MAX_FILENAME_LENGTH characters, keeping the extension.
    - Never returns an empty string.
    """
    if not raw_name:
        return DEFAULT_STEM

    # Treat both Windows and Unix separators as folder separators.
    name = raw_name.replace("\\", "/").split("/")[-1]

    name = _UNSAFE_CHARACTERS.sub("_", name)
    name = _REPEATED_UNDERSCORES.sub("_", name)
    name = _REPEATED_SPACES.sub(" ", name)
    name = name.lstrip(". ").rstrip(". ")

    stem, extension = os.path.splitext(name)
    stem = stem.strip(" _")
    if not stem:
        stem = DEFAULT_STEM

    max_stem_length = max(1, config.MAX_FILENAME_LENGTH - len(extension))
    return stem[:max_stem_length] + extension


def get_extension(sanitized_name: str) -> str:
    """Return the lower-case extension including the dot ('' if none)."""
    return os.path.splitext(sanitized_name)[1].lower()


def clean_original_name(raw_name: str | None, limit: int = 255) -> str:
    """
    Keep the name as uploaded, but strip control characters and cut it short.

    Stored for forensic reference only. It is never used as a path, never
    logged and never returned by the API (the API shows the sanitized name).
    """
    if not raw_name:
        return ""
    printable = "".join(ch for ch in raw_name if ch.isprintable())
    return printable[:limit]
