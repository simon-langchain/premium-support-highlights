"""Audit log for user-triggered actions.

Appends entries to a persistent JSONL file. Each entry has a UTC timestamp,
action name, and structured details dict.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_DIR = Path(__file__).parent / ".cache"
AUDIT_FILE = AUDIT_DIR / "audit.jsonl"


def log(action: str, details: dict[str, Any] | None = None) -> None:
    """Append a single audit entry."""
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action,
        "details": details or {},
    }
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def get_recent(limit: int = 100) -> list[dict[str, Any]]:
    """Return the most recent audit entries (newest first)."""
    if not AUDIT_FILE.exists():
        return []
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    entries = []
    for line in reversed(lines[-limit:]):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
