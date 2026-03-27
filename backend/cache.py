"""JSON file cache for per-ticket AI summaries."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_FILE = CACHE_DIR / "analysis_cache.json"


def _cache_key(issue_id: str, latest_message_time: str) -> str:
    raw = f"{issue_id}:{latest_message_time}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def get_ticket_summary(issue_id: str, latest_message_time: str) -> str | None:
    """Return a cached ticket summary string, or None if not cached."""
    key = "ts:" + _cache_key(issue_id, latest_message_time)
    entry = _load().get(key)
    return entry.get("summary") if isinstance(entry, dict) else None


def set_ticket_summary(issue_id: str, latest_message_time: str, summary: str) -> None:
    """Persist a ticket summary to the file cache."""
    cache = _load()
    key = "ts:" + _cache_key(issue_id, latest_message_time)
    cache[key] = {
        "summary": summary,
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(cache)
