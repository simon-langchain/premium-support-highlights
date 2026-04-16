"""JSON file cache for per-ticket AI summaries."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

SUMMARY_MAX_AGE_SECONDS = 86400  # 24 hours

CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_FILE = CACHE_DIR / "analysis_cache.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)  # create once at import time


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
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _is_stale(entry: dict, max_age_seconds: int) -> bool:
    """Return True if the cache entry is older than max_age_seconds."""
    cached_at = entry.get("cached_at")
    if not cached_at:
        return True
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached_at)).total_seconds()
        return age > max_age_seconds
    except (ValueError, TypeError):
        return True


def get_ticket_summary(issue_id: str, latest_message_time: str) -> str | None:
    """Return a cached ticket summary string, or None if not cached or stale."""
    key = "ts:" + _cache_key(issue_id, latest_message_time)
    entry = _load().get(key)
    if not isinstance(entry, dict):
        return None
    if _is_stale(entry, SUMMARY_MAX_AGE_SECONDS):
        return None
    return entry.get("summary")


def set_ticket_summary(issue_id: str, latest_message_time: str, summary: str) -> None:
    """Persist a ticket summary to the file cache."""
    cache = _load()
    key = "ts:" + _cache_key(issue_id, latest_message_time)
    cache[key] = {
        "summary": summary,
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(cache)


def get_account_summary(account_id: str, period: str) -> str | None:
    """Return the cached AI account summary, or None if not cached or stale."""
    key = f"as:{account_id}:{period}"
    entry = _load().get(key)
    if not isinstance(entry, dict):
        return None
    if _is_stale(entry, SUMMARY_MAX_AGE_SECONDS):
        return None
    return entry.get("summary")


def set_account_summary(account_id: str, period: str, summary: str) -> None:
    """Persist an AI account summary to the file cache."""
    cache = _load()
    key = f"as:{account_id}:{period}"
    cache[key] = {
        "summary": summary,
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(cache)
