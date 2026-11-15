"""
Runs before the tests are collected.

Sends audit logs to a temporary file (not backend/logs/audit.log) and
silences console audit output, so test runs stay clean.
"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault(
    "AUDIT_LOG_FILE",
    str(Path(tempfile.gettempdir()) / "knowledge_platform_test_audit.log"),
)
os.environ.setdefault("AUDIT_LOG_CONSOLE", "0")