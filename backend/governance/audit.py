"""
Audit logging for authorization decisions.

Every request that reaches the platform produces exactly one audit event,
written as a single JSON line. JSON lines are easy to read, search with
PowerShell, and load into SQLite later.

Rules this module follows:
- Only the standard library is used (zero cost).
- The question text and document contents are NEVER logged. Only the
  question's length is recorded.
- User-controlled values are truncated and JSON-encoded, so a malicious
  user_id such as "x\\nFAKE LOG LINE" cannot forge extra log lines.

Environment variables (optional):
- AUDIT_LOG_FILE:    path of the log file (default: backend/logs/audit.log)
- AUDIT_LOG_CONSOLE: set to "0" to turn off console output (used by tests)
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from uuid import UUID

DEFAULT_LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "audit.log"
MAX_VALUE_LENGTH = 100
MAX_REASON_LENGTH = 300
MAX_SCOPE_ITEMS = 20

audit_logger = logging.getLogger("knowledge_platform.audit")
_configured = False


def setup_audit_logging() -> None:
    """Attach console and file handlers once. Safe to call repeatedly."""
    global _configured
    if _configured:
        return
    _configured = True

    audit_logger.setLevel(logging.INFO)
    # Do not also send audit lines to the root logger (avoids duplicates).
    audit_logger.propagate = False

    # The message is already a complete JSON line, so no extra formatting.
    formatter = logging.Formatter("%(message)s")

    if os.environ.get("AUDIT_LOG_CONSOLE", "1") != "0":
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        audit_logger.addHandler(console_handler)

    log_file = Path(os.environ.get("AUDIT_LOG_FILE", str(DEFAULT_LOG_FILE)))
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=1_000_000,  # about 1 MB per file
            backupCount=3,       # keep 3 older files
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        audit_logger.addHandler(file_handler)
    except OSError as error:
        # Never crash the API because the log file is unavailable.
        logging.getLogger("knowledge_platform").warning(
            "Audit log file unavailable (%s). Console logging only.", error
        )


def _clean(value: Any, limit: int = MAX_VALUE_LENGTH) -> str | None:
    """Convert to text and cut off very long, user-controlled values."""
    if value is None:
        return None
    text = str(value)
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def log_decision(
    *,
    trace_id: UUID,
    decision: str,
    stage: str,
    user_id: str | None = None,
    role: str | None = None,
    purpose: str | None = None,
    requested_scope: list[str] | None = None,
    reason: str | None = None,
    response_status: str | None = None,
    question_length: int | None = None,
) -> None:
    """
    Write one audit event.

    decision: "allow", "deny", "invalid" (failed validation) or "error"
    stage:    where the decision was made: "validation", "identity",
              "purpose", "scope", "retrieval" or "server"
    """
    scope_items = [_clean(item) for item in (requested_scope or [])[:MAX_SCOPE_ITEMS]]

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "request_decision",
        "trace_id": str(trace_id),
        "user_id": _clean(user_id),
        "role": _clean(role),
        "purpose": _clean(purpose),
        "requested_scope": scope_items,
        "decision": decision,
        "stage": stage,
        "reason": _clean(reason, MAX_REASON_LENGTH),
        "response_status": response_status,
        "question_length": question_length,
    }
    audit_logger.info(json.dumps(event, ensure_ascii=True))