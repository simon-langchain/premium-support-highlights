"""Pylon REST API client for the Premium Support Highlights dashboard.

Focused on account-level data: listing accounts, searching issues per account,
and fetching messages/custom-fields for metrics computation.

Rate limits (read-only): search 20/min, get issue 60/min, get messages 20/min.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx

PYLON_BASE_URL = "https://api.usepylon.com"
PYLON_API_TOKEN = os.getenv("PYLON_API_TOKEN", "")

# Optional: disable in-memory cache (set PYLON_TOOLS_CACHE=0 to disable)
_CACHE_ENABLED = os.getenv("PYLON_TOOLS_CACHE", "1").strip().lower() not in ("0", "false", "no")
_CACHE_TTL_SECONDS = 120
_cache: dict[tuple[str, str], tuple[float, str]] = {}

# Shared HTTP client (reused for connection pooling)
_http_client: httpx.Client | None = None
_http_client_lock = threading.Lock()

_MAX_RETRIES = 2
_RETRY_BACKOFF = 1.0
_CACHE_MAX_ENTRIES = 256


def _get_client() -> httpx.Client:
    """Return a shared HTTP client, creating it on first use."""
    global _http_client
    if _http_client is None:
        with _http_client_lock:
            if _http_client is None:
                if not PYLON_API_TOKEN:
                    raise RuntimeError(
                        "PYLON_API_TOKEN is not set. "
                        "Add it to the .env file in the project root."
                    )
                _http_client = httpx.Client(
                    base_url=PYLON_BASE_URL,
                    headers={
                        "Authorization": f"Bearer {PYLON_API_TOKEN}",
                        "Accept": "application/vnd.api+json",
                        "Content-Type": "application/json",
                    },
                    timeout=30,
                )
    return _http_client


def _get(path: str, params: dict | None = None) -> dict:
    """Make a GET request to the Pylon API. Uses shared client; retries on 429."""
    client = _get_client()
    for attempt in range(_MAX_RETRIES + 1):
        resp = client.get(path, params=params)
        if resp.status_code != 429 or attempt == _MAX_RETRIES:
            resp.raise_for_status()
            return resp.json()
        time.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError("unreachable")


def _post(path: str, body: dict) -> dict:
    """Make a POST request to the Pylon API. Uses shared client; retries on 429."""
    client = _get_client()
    for attempt in range(_MAX_RETRIES + 1):
        resp = client.post(path, json=body)
        if resp.status_code != 429 or attempt == _MAX_RETRIES:
            resp.raise_for_status()
            return resp.json()
        time.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError("unreachable")


def _cached_get(cache_key: str, path: str) -> dict:
    """GET with optional short-TTL in-memory cache."""
    if not _CACHE_ENABLED:
        return _get(path)
    key = ("get", cache_key)
    now = time.monotonic()
    if key in _cache:
        ts, raw = _cache[key]
        if now - ts < _CACHE_TTL_SECONDS:
            return json.loads(raw)
    data = _get(path)
    _cache[key] = (now, json.dumps(data, default=str))
    # Evict stale entries to prevent unbounded growth
    if len(_cache) > _CACHE_MAX_ENTRIES:
        stale = [k for k, (ts, _) in _cache.items() if now - ts >= _CACHE_TTL_SECONDS]
        for k in stale:
            del _cache[k]
    return data


def get_me() -> dict:
    """Get the currently authenticated Pylon user's details."""
    return _cached_get("me", "/me")


def get_team_members() -> list[dict]:
    """Return all Pylon team members. Cached for 2 minutes.

    Uses GET /users. Each member has at minimum an `email` field.
    """
    data = _cached_get("users", "/users")
    return data.get("data", []) if isinstance(data, dict) else []


def get_accounts() -> list[dict]:
    """List all accounts from the Pylon API, paginating through all results.

    Returns the data list from GET /accounts. Each account has: id, name,
    and other metadata fields.
    """
    all_accounts: list[dict] = []
    cursor: str | None = None
    max_pages = 50
    for _ in range(max_pages):
        params = {"cursor": cursor} if cursor else None
        data = _get("/accounts", params=params)
        all_accounts.extend(data.get("data", []))
        pagination = data.get("pagination", {})
        if not pagination.get("has_next_page"):
            break
        cursor = pagination.get("cursor")
        if not cursor:
            break
    return all_accounts


_ACCOUNTS_TTL_SECONDS = 3600  # 1 hour
_CACHE_DIR = Path(__file__).parent / ".cache"
_CURRENT_CUSTOMERS_CACHE_FILE = _CACHE_DIR / "current_customers.json"

_RELATIONSHIP_STATUS_SLUG = "account.salesforce.Relationship_Status__c"
_SUPPORT_TIER_SLUG = "account.salesforce.Support_Tier__c"


def _get_custom_field(account: dict, slug: str) -> str:
    fields = account.get("custom_fields") or {}
    if isinstance(fields, dict):
        return str((fields.get(slug) or {}).get("value", "")).strip()
    return ""


def get_current_customers(force_refresh: bool = False) -> list[dict]:
    """Fetch all accounts where Relationship_Status__c = 'Current Customer'.

    Cached to disk for 1 hour. Single source of truth — tiers and per-tier
    account lists are derived from this without additional API calls.
    """
    if not force_refresh and _CURRENT_CUSTOMERS_CACHE_FILE.exists():
        try:
            raw = json.loads(_CURRENT_CUSTOMERS_CACHE_FILE.read_text())
            if time.time() - raw.get("cached_at", 0) < _ACCOUNTS_TTL_SECONDS:
                return raw.get("accounts", [])
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    accounts: list[dict] = []
    body: dict = {
        "filter": {
            "field": _RELATIONSHIP_STATUS_SLUG,
            "operator": "equals",
            "value": "Current Customer",
        },
        "limit": 1000,
    }
    data = _post("/accounts/search", body)
    accounts = data.get("data", [])
    cursor = (data.get("pagination") or {}).get("cursor")
    while cursor and (data.get("pagination") or {}).get("has_next_page"):
        body = {**body, "cursor": cursor}
        data = _post("/accounts/search", body)
        accounts.extend(data.get("data", []))
        cursor = (data.get("pagination") or {}).get("cursor")

    _CURRENT_CUSTOMERS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CURRENT_CUSTOMERS_CACHE_FILE.write_text(
        json.dumps({"cached_at": time.time(), "accounts": accounts}, default=str)
    )
    return accounts


def get_available_tiers(force_refresh: bool = False) -> list[str]:
    """Return sorted Support Tier values across all current customers."""
    customers = get_current_customers(force_refresh=force_refresh)
    return sorted({_get_custom_field(a, _SUPPORT_TIER_SLUG) for a in customers
                   if _get_custom_field(a, _SUPPORT_TIER_SLUG)})


def get_accounts_by_tier(tier: str = "Premium", force_refresh: bool = False) -> list[dict]:
    """Return current customers filtered to the given Support Tier."""
    customers = get_current_customers(force_refresh=force_refresh)
    return [a for a in customers if _get_custom_field(a, _SUPPORT_TIER_SLUG).lower() == tier.lower()]


def get_premium_accounts(force_refresh: bool = False) -> list[dict]:
    """Backward-compatible wrapper for get_accounts_by_tier('Premium')."""
    return get_accounts_by_tier("Premium", force_refresh=force_refresh)


def search_issues_for_account(
    account_id: str,
    states: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Search issues for a specific account, paginating through all results.

    Args:
        account_id: The account UUID to filter on.
        states: Filter by issue states (e.g. ["new", "waiting_on_you"]).
        created_after: ISO 8601 timestamp — only return issues created after this.
        created_before: ISO 8601 timestamp — only return issues created before this.
        limit: Max results per page (default 500, capped at 1000).

    Returns:
        Flat list of all matching issue dicts across all pages.
    """
    subfilters: list[dict] = [
        {"field": "account_id", "operator": "equals", "value": account_id}
    ]
    if states:
        subfilters.append({"field": "state", "operator": "in", "values": states})
    if created_after:
        subfilters.append({"field": "created_at", "operator": "time_is_after", "value": created_after})
    if created_before:
        subfilters.append({"field": "created_at", "operator": "time_is_before", "value": created_before})

    filter_obj = (
        {"operator": "and", "subfilters": subfilters}
        if len(subfilters) > 1
        else subfilters[0]
    )
    body: dict = {"filter": filter_obj, "limit": min(limit, 1000)}

    all_issues: list[dict] = []
    max_pages = 20
    cursor: str | None = None

    for _ in range(max_pages):
        if cursor:
            body["cursor"] = cursor
        data = _post("/issues/search", body)
        all_issues.extend(data.get("data", []))
        pagination = data.get("pagination", {})
        if not pagination.get("has_next_page"):
            break
        cursor = pagination.get("cursor")
        if not cursor:
            break

    return all_issues


def get_issue_messages(issue_id: str) -> list[dict]:
    """Get the message history for a Pylon issue.

    Args:
        issue_id: The issue UUID.

    Returns:
        List of message dicts.
    """
    data = _get(f"/issues/{issue_id}/messages")
    return data.get("data", [])


_field_labels_cache: dict[str, dict[str, str]] = {}
_field_labels_ts: float = 0
_FIELD_LABELS_TTL = 3600  # 1 hour


def get_issue_field_labels() -> dict[str, dict[str, str]]:
    """Fetch option labels for all issue custom fields, cached for 1 hour.

    Returns:
        dict mapping field_slug -> {option_slug -> label}
        e.g. {"category": {"lc_infrastructure": "LangChain - Infrastructure"}}
    """
    global _field_labels_cache, _field_labels_ts
    now = time.monotonic()
    if _field_labels_cache and now - _field_labels_ts < _FIELD_LABELS_TTL:
        return _field_labels_cache

    data = _get("/custom-fields", params={"object_type": "issue"})
    fields = data.get("data", []) if isinstance(data, dict) else []
    result: dict[str, dict[str, str]] = {}
    for field in fields:
        slug = field.get("slug", "")
        options = (field.get("select_metadata") or {}).get("options") or []
        if options:
            result[slug] = {
                opt["slug"]: opt["label"]
                for opt in options
                if opt.get("slug") and opt.get("label")
            }

    _field_labels_cache = result
    _field_labels_ts = now
    return result


_csat_survey_id: str | None = None
_csat_survey_id_ts: float = 0


def _get_csat_survey_id() -> str | None:
    """Find the CSAT survey ID from /surveys, cached for 1 hour."""
    global _csat_survey_id, _csat_survey_id_ts
    now = time.monotonic()
    if _csat_survey_id and now - _csat_survey_id_ts < 3600:
        return _csat_survey_id
    try:
        data = _get("/surveys")
        for survey in data.get("data", []):
            if survey.get("type") == "csat":
                _csat_survey_id = survey["id"]
                _csat_survey_id_ts = now
                return _csat_survey_id
    except Exception:
        pass
    return None


def get_csat_responses_for_account(account_id: str) -> list[dict]:
    """Fetch all CSAT survey responses for a specific account."""
    survey_id = _get_csat_survey_id()
    if not survey_id:
        return []

    all_responses: list[dict] = []
    cursor: str | None = None
    for _ in range(20):
        params: dict = {}
        if cursor:
            params["cursor"] = cursor
        data = _get(f"/surveys/{survey_id}/responses", params=params or None)
        all_responses.extend(data.get("data", []))
        pagination = data.get("pagination", {})
        if not pagination.get("has_next_page"):
            break
        cursor = pagination.get("cursor")
        if not cursor:
            break

    return [r for r in all_responses if r.get("account_id") == account_id]


def make_date_range(period: str) -> tuple[str, str]:
    """Return (created_after, created_before) ISO strings for the given period.

    period: "7d" | "1m" | "6m" | "1y"
    """
    now = datetime.now(timezone.utc)
    days = {"7d": 7, "1m": 30, "6m": 183, "1y": 365}.get(period, 183)
    start = now - timedelta(days=days)
    return (
        start.isoformat().replace("+00:00", "Z"),
        now.isoformat().replace("+00:00", "Z"),
    )


def get_account(account_id: str) -> dict | None:
    """Look up a single account by ID from the current customers cache."""
    for account in get_current_customers():
        if account.get("id") == account_id:
            return account
    try:
        return _get(f"/accounts/{account_id}")
    except Exception:
        return None


def get_slack_channel_id(account: dict) -> str | None:
    """Extract the primary Slack channel ID from an account's channels array.

    Prefers is_primary=True channels; falls back to first Slack channel found.
    Returns None if no Slack channel is configured.
    """
    ch = _get_primary_slack_channel(account)
    return ch.get("channel_id") if ch else None


def get_slack_channel_info(account: dict) -> dict | None:
    """Return {channel_id, channel_name} for the primary Slack channel, or None."""
    ch = _get_primary_slack_channel(account)
    if not ch:
        return None
    return {
        "channel_id": ch.get("channel_id"),
        "channel_name": ch.get("name") or ch.get("channel_name") or ch.get("channel_id"),
    }


def get_all_slack_channels(account: dict) -> list[dict]:
    """Return all Slack channels linked to the account as [{id, name}], primary first."""
    channels = account.get("channels") or []
    slack_channels = [c for c in channels if c.get("source") == "slack" and c.get("channel_id")]
    result = []
    seen: set[str] = set()
    # Primary first, then the rest
    ordered = sorted(slack_channels, key=lambda c: 0 if c.get("is_primary") else 1)
    for c in ordered:
        cid = c["channel_id"]
        if cid not in seen:
            seen.add(cid)
            name = c.get("name") or c.get("channel_name") or cid
            result.append({"id": cid, "name": name})
    return result


def _get_primary_slack_channel(account: dict) -> dict | None:
    channels = account.get("channels") or []
    slack_channels = [c for c in channels if c.get("source") == "slack" and c.get("channel_id")]
    if not slack_channels:
        return None
    primary = next((c for c in slack_channels if c.get("is_primary")), None)
    return primary or slack_channels[0]


if __name__ == "__main__":
    print("Testing Pylon client...")
    me = get_me()
    print(f"Authenticated as: {me.get('data', me)}")

    print("\n--- get_accounts ---")
    accounts = get_accounts()
    print(f"Found {len(accounts)} accounts")
    if accounts:
        print(f"First account: {accounts[0].get('name', '?')}")

    print("\nPylon client OK!")
