"""JSON file cache for per-ticket AI summaries."""

import functools
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

SUMMARY_MAX_AGE_SECONDS = 86400  # 24 hours

CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_FILE = CACHE_DIR / "analysis_cache.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)  # create once at import time


def _cache_key(issue_id: str, latest_message_time: str, model: str = "") -> str:
    raw = f"{issue_id}:{latest_message_time}:{model}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# Writers do load → modify → save on one shared file from many threads (ticket
# summaries run in parallel), so they're serialised by a lock...
_write_lock = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _write_lock:
            return fn(*args, **kwargs)
    return wrapper


def _save(cache: dict) -> None:
    # ...and written atomically: a reader never sees a half-written file (which _load
    # would treat as empty, making the next save wipe every entry).
    tmp = CACHE_FILE.with_name(f"{CACHE_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(cache, indent=2))
    os.replace(tmp, CACHE_FILE)


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


def get_ticket_summary(issue_id: str, latest_message_time: str, model: str = "") -> str | None:
    """Return a cached ticket summary string, or None if not cached or stale.

    Looks up by model-specific key first. If not found, falls back to the
    legacy key (without model) so existing cache entries from before the
    model-aware cache key migration are still served.
    """
    return _lookup_ticket_summary(_load(), issue_id, latest_message_time, model)


def get_ticket_summaries(tickets: list[tuple[str, str]], model: str = "", cache: dict | None = None) -> dict[str, str]:
    """Bulk get_ticket_summary for [(issue_id, latest_message_time)] → {issue_id: summary},
    loading the cache file once instead of once per ticket."""
    cache = _load() if cache is None else cache
    out = {}
    for issue_id, latest in tickets:
        if (summary := _lookup_ticket_summary(cache, issue_id, latest, model)) is not None:
            out[issue_id] = summary
    return out


def _lookup_ticket_summary(cache: dict, issue_id: str, latest_message_time: str, model: str) -> str | None:
    key = "ts:" + _cache_key(issue_id, latest_message_time, model)
    entry = cache.get(key)
    if not isinstance(entry, dict) or _is_stale(entry, SUMMARY_MAX_AGE_SECONDS):
        # Fall back to legacy key (no model in hash)
        if model:
            legacy_key = "ts:" + _cache_key(issue_id, latest_message_time, "")
            entry = cache.get(legacy_key)
        if not isinstance(entry, dict) or _is_stale(entry, SUMMARY_MAX_AGE_SECONDS):
            return None
    return entry.get("summary")


@_locked
def set_ticket_summary(issue_id: str, latest_message_time: str, summary: str, model: str = "") -> None:
    """Persist a ticket summary to the file cache."""
    cache = _load()
    key = "ts:" + _cache_key(issue_id, latest_message_time, model)
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


@_locked
def set_account_summary(account_id: str, period: str, summary: str) -> None:
    """Persist an AI account summary to the file cache."""
    cache = _load()
    key = f"as:{account_id}:{period}"
    cache[key] = {
        "summary": summary,
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(cache)


PAYLOAD_MAX_AGE_SECONDS = 8 * 3600  # 8 hours — matches session expiry


def get_payload_cache(account_id: str, period: str) -> dict | None:
    """Return the cached computed payload for an account, or None if missing or stale."""
    key = f"payload:{account_id}:{period}"
    entry = _load().get(key)
    if not isinstance(entry, dict):
        return None
    if _is_stale(entry, PAYLOAD_MAX_AGE_SECONDS):
        return None
    return entry.get("payload")


@_locked
def set_payload_cache(account_id: str, period: str, payload: dict) -> None:
    """Persist the computed account payload to disk so it survives restarts."""
    cache = _load()
    key = f"payload:{account_id}:{period}"
    cache[key] = {
        "payload": payload,
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(cache)


def get_qbr_slide(account_id: str, year_month: str) -> dict | None:
    """Return cached QBR slide info {url, pres_id, created_at, month_label}, or None."""
    key = f"qbr:{account_id}:{year_month}"
    entry = _load().get(key)
    return entry if isinstance(entry, dict) else None


@_locked
def delete_qbr_slide(account_id: str, year_month: str) -> bool:
    """Remove a QBR slide record. Returns True if the key existed."""
    cache = _load()
    key = f"qbr:{account_id}:{year_month}"
    if key not in cache:
        return False
    del cache[key]
    _save(cache)
    return True


@_locked
def set_qbr_slide(
    account_id: str,
    year_month: str,
    url: str,
    pres_id: str,
    month_label: str,
) -> None:
    """Persist a QBR slide record permanently (no expiry)."""
    cache = _load()
    key = f"qbr:{account_id}:{year_month}"
    cache[key] = {
        "url": url,
        "pres_id": pres_id,
        "month_label": month_label,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(cache)

