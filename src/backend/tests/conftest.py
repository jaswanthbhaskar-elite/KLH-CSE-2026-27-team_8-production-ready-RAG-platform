"""
Runs before the tests are collected.

- Sends audit logs to a temporary file (not backend/logs/audit.log) and
  silences console audit output, so test runs stay clean.
- Gives EVERY test its own empty SQLite database in a temporary folder, so
  tests can never read or change the real backend/data/knowledge.db.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault(
    "AUDIT_LOG_FILE",
    str(Path(tempfile.gettempdir()) / "knowledge_platform_test_audit.log"),
)
os.environ.setdefault("AUDIT_LOG_CONSOLE", "0")

# Safety net: even code that runs at import time (before any fixture) must
# not touch the real database.
os.environ["KNOWLEDGE_DB_PATH"] = str(
    Path(tempfile.gettempdir()) / "knowledge_platform_test_import.db"
)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Point KNOWLEDGE_DB_PATH at a fresh temporary file for each test."""
    db_path = tmp_path / "test_knowledge.db"
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(db_path))
    return db_path


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
def audit_log():
    """Capture audit events written during a test (used by test_documents.py)."""
    from governance.audit import audit_logger

    handler = AuditCapture()
    audit_logger.addHandler(handler)
    yield handler
    audit_logger.removeHandler(handler)
