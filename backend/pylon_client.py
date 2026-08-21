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

from cache import PAYLOAD_MAX_AGE_SECONDS

PYLON_BASE_URL = "https://api.usepylon.com"
PYLON_API_TOKEN = os.getenv("PYLON_API_TOKEN", "")

_CACHE_DIR = Path(__file__).parent / ".cache"

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
    """Make a GET request to the Pylon API. Uses shared client; retries on 429
    and on a connection-level timeout (e.g. httpx.ReadTimeout on a slow page —
    without this, one transient timeout would abort a whole paginated fetch
    with no retry, discarding every page already fetched)."""
    client = _get_client()
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = client.get(path, params=params)
        except httpx.TimeoutException:
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(_RETRY_BACKOFF * (attempt + 1))
            continue
        if resp.status_code != 429 or attempt == _MAX_RETRIES:
            resp.raise_for_status()
            return resp.json()
        time.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError("unreachable")


def _post(path: str, body: dict, timeout: float | None = None) -> dict:
    """Make a POST request to the Pylon API. Uses shared client; retries on 429
    and on a connection-level timeout (same rationale as _get).

    `timeout` overrides the client's default 30s for calls known to be slow
    (e.g. large/org-wide /issues/search pages, confirmed to take 10-15s+ per
    1000-row page server-side — well within the account-scoped 30s default,
    but not for org-wide queries with no account_id to narrow the scan).
    """
    client = _get_client()
    kwargs = {"timeout": timeout} if timeout is not None else {}
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = client.post(path, json=body, **kwargs)
        except httpx.TimeoutException:
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(_RETRY_BACKOFF * (attempt + 1))
            continue
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


_SUPPORT_TEAM_NAME = "Support"
_SUPPORT_TEAM_TTL_SECONDS = 3600  # 1 hour
_SUPPORT_TEAM_CACHE_FILE = _CACHE_DIR / "support_team.json"


def get_support_team_member_ids(force_refresh: bool = False) -> dict[str, str]:
    """Return {pylon_user_id: email} for members of the Pylon "Support" team.

    Uses GET /teams (org-level team membership — distinct from the per-issue
    `team` field, which is a routing/region queue, not org membership).
    Disk-cached for 1 hour, mirroring get_current_customers's pattern.
    """
    if not force_refresh and _SUPPORT_TEAM_CACHE_FILE.exists():
        try:
            raw = json.loads(_SUPPORT_TEAM_CACHE_FILE.read_text())
            if time.time() - raw.get("cached_at", 0) < _SUPPORT_TEAM_TTL_SECONDS:
                return raw.get("members", {})
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    members: dict[str, str] = {}
    data = _get("/teams")
    for team in data.get("data", []):
        if str(team.get("name", "")).strip().lower() == _SUPPORT_TEAM_NAME.lower():
            for user in team.get("users") or []:
                uid = user.get("id")
                email = user.get("email")
                if uid and email:
                    members[uid] = email
            break

    _SUPPORT_TEAM_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SUPPORT_TEAM_CACHE_FILE.write_text(
        json.dumps({"cached_at": time.time(), "members": members}, default=str)
    )
    return members


def is_support_team_member(email: str) -> bool:
    """Return True if email belongs to a member of the Pylon "Support" team."""
    if not email:
        return False
    email = email.strip().lower()
    return any(e.strip().lower() == email for e in get_support_team_member_ids().values())


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


_ISSUE_SEARCH_TIMEOUT = 90.0  # confirmed live: org-wide pages can take 10-15s+ each


def _paginated_issue_search(filter_obj: dict, limit: int = 500) -> list[dict]:
    """POST /issues/search with the given filter, paginating through all results.

    Shared by search_issues_for_account and search_all_issues.
    """
    body: dict = {"filter": filter_obj, "limit": min(limit, 1000)}

    all_issues: list[dict] = []
    max_pages = 20
    cursor: str | None = None
    pagination: dict = {}

    for _ in range(max_pages):
        if cursor:
            body["cursor"] = cursor
        data = _post("/issues/search", body, timeout=_ISSUE_SEARCH_TIMEOUT)
        all_issues.extend(data.get("data", []))
        pagination = data.get("pagination", {})
        if not pagination.get("has_next_page"):
            break
        cursor = pagination.get("cursor")
        if not cursor:
            break
    else:
        # Exhausted max_pages while more results were still available —
        # results are silently truncated; surface it so it isn't mistaken
        # for a complete result set.
        if pagination.get("has_next_page"):
            print(f"pylon_client: _paginated_issue_search truncated at {max_pages} pages "
                  f"({len(all_issues)} issues) — more results were available")

    return all_issues


def search_issues_for_account(
    account_id: str,
    states: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    updated_after: str | None = None,
    updated_before: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Search issues for a specific account, paginating through all results.

    Args:
        account_id: The account UUID to filter on.
        states: Filter by issue states (e.g. ["new", "waiting_on_you"]).
        created_after: ISO 8601 timestamp — only return issues created after this.
        created_before: ISO 8601 timestamp — only return issues created before this.
        updated_after: ISO 8601 timestamp — only return issues updated after this.
        updated_before: ISO 8601 timestamp — only return issues updated before this.
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
    if updated_after:
        subfilters.append({"field": "updated_at", "operator": "time_is_after", "value": updated_after})
    if updated_before:
        subfilters.append({"field": "updated_at", "operator": "time_is_before", "value": updated_before})

    filter_obj = (
        {"operator": "and", "subfilters": subfilters}
        if len(subfilters) > 1
        else subfilters[0]
    )
    return _paginated_issue_search(filter_obj, limit)


def search_all_issues(
    states: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    """Search issues org-wide (no account_id filter), paginating through all results.

    Used for the internal Support-team dashboard, which needs ticket data
    across every account rather than one at a time. Confirmed live that
    Pylon's /issues/search accepts a filter with no account_id subfilter
    and returns issues across all accounts.

    Args:
        states: Filter by issue states (e.g. ["waiting_on_you"]).
        created_after: ISO 8601 timestamp — only return issues created after this.
        created_before: ISO 8601 timestamp — only return issues created before this.
        limit: Max results per page (default 1000, capped at 1000).

    Returns:
        Flat list of all matching issue dicts across all pages.
    """
    subfilters: list[dict] = []
    if states:
        subfilters.append({"field": "state", "operator": "in", "values": states})
    if created_after:
        subfilters.append({"field": "created_at", "operator": "time_is_after", "value": created_after})
    if created_before:
        subfilters.append({"field": "created_at", "operator": "time_is_before", "value": created_before})

    if not subfilters:
        raise ValueError("search_all_issues requires at least one filter (states and/or date range)")

    filter_obj = (
        {"operator": "and", "subfilters": subfilters}
        if len(subfilters) > 1
        else subfilters[0]
    )
    return _paginated_issue_search(filter_obj, limit)


def search_issues_by_assignees(
    assignee_ids: list[str],
    updated_after: str | None = None,
    updated_before: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Search issues assigned to any of the given Pylon user IDs, paginating through all results.

    Used by message_activity.py's staged backfill to scope message-sync work
    to Support-team-assigned tickets only, rather than every org-wide issue.
    Confirmed live: assignee_id "in" combines correctly with an updated_at
    range and returns issues matching exactly the given assignees.

    Args:
        assignee_ids: Pylon user IDs to filter on (e.g. non-admin Support-team members).
        updated_after: ISO 8601 timestamp — only return issues updated after this.
        updated_before: ISO 8601 timestamp — only return issues updated before this.
        limit: Max results per page (default 500, capped at 1000).

    Returns:
        Flat list of all matching issue dicts across all pages.
    """
    if not assignee_ids:
        return []

    subfilters: list[dict] = [
        {"field": "assignee_id", "operator": "in", "values": assignee_ids}
    ]
    if updated_after:
        subfilters.append({"field": "updated_at", "operator": "time_is_after", "value": updated_after})
    if updated_before:
        subfilters.append({"field": "updated_at", "operator": "time_is_before", "value": updated_before})

    filter_obj = (
        {"operator": "and", "subfilters": subfilters}
        if len(subfilters) > 1
        else subfilters[0]
    )
    return _paginated_issue_search(filter_obj, limit)


def _trim_issue_for_team_dashboard(issue: dict) -> dict:
    """Project a full Pylon issue down to only the fields the team dashboard needs.

    Drops title/body_html/tags/custom_fields/requester/etc — the team dashboard
    only needs counts and times, never ticket content.
    """
    return {
        "id": issue.get("id"),
        "assignee": issue.get("assignee"),
        "state": issue.get("state"),
        "created_at": issue.get("created_at"),
        "business_hours_first_response_seconds": issue.get("business_hours_first_response_seconds"),
        "business_hours_resolution_seconds": issue.get("business_hours_resolution_seconds"),
        "csat_responses": issue.get("csat_responses"),
        "latest_message_time": issue.get("latest_message_time"),
    }


# Matches the customer-account dashboard's disk-cache duration exactly
# (cache.py's PAYLOAD_MAX_AGE_SECONDS, imported above) — same trade-off:
# freshness is bounded by this TTL by default, with a manual refresh
# (force=true) as the escape hatch, same as the account dashboard's
# "Refresh Data" button.
_TEAM_PERIOD_ISSUES_TTL_SECONDS = PAYLOAD_MAX_AGE_SECONDS
_TEAM_BACKLOG_TTL_SECONDS = PAYLOAD_MAX_AGE_SECONDS


def _team_period_issues_cache_file(period: str) -> Path:
    safe_period = "".join(c for c in period if c.isalnum()) or "unknown"
    return _CACHE_DIR / f"team_period_issues_{safe_period}.json"


_TEAM_BACKLOG_CACHE_FILE = _CACHE_DIR / "team_backlog.json"


# Narrowest to broadest — all periods are windows ending at "now", so a
# broader period's data is always a superset of every narrower one's.
_PERIOD_ORDER = ["7d", "1m", "3m", "6m", "1y"]


def get_team_period_issues(period: str, force_refresh: bool = False) -> list[dict]:
    """Trimmed, org-wide issues created within `period`. Disk-cached (matches
    the account dashboard's payload cache duration — see PAYLOAD_MAX_AGE_SECONDS).

    Isolated from cache.py's shared analysis_cache.json — this can be several
    thousand rows and shouldn't bloat the file that every ticket-summary/
    account-summary write also reads and rewrites wholesale.

    Before hitting Pylon, checks whether a broader period is already cached
    and fresh (e.g. "6m" when "1m" is requested) and filters it locally
    instead — periods are nested windows ending at "now", so a broader
    period's data already contains everything a narrower one needs. Never
    fetches more than what's actually requested; this only reuses data that
    was already fetched for some other period the user (or another viewer)
    already looked at.

    Stale-while-revalidate for broad periods: a live org-wide fetch for 6m/1y
    can take several minutes (measured live: 6m ~260s; 1y truncates past
    10,000 rows and takes even longer) — long enough to exceed the frontend's
    fetch timeout and 500 the whole page. If force_refresh is False and a
    cache file exists but has expired, this returns the stale copy rather
    than blocking on a live fetch. main.py's _team_cache_warmer_loop refreshes
    every period in the background on a cadence inside the TTL, so in normal
    operation this branch serves data that's stale by minutes, not hours.
    Only force_refresh=True (the manual "Refresh Data" button) or a
    completely missing cache (first-ever request for a period) triggers a
    live, blocking fetch here.
    """
    cache_file = _team_period_issues_cache_file(period)
    cached_issues: list[dict] | None = None
    if cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text())
            cached_issues = raw.get("issues", [])
            if not force_refresh and time.time() - raw.get("cached_at", 0) < _TEAM_PERIOD_ISSUES_TTL_SECONDS:
                return cached_issues
        except (json.JSONDecodeError, OSError, KeyError):
            cached_issues = None

    if not force_refresh and period in _PERIOD_ORDER:
        created_after, _ = make_date_range(period)
        for broader in _PERIOD_ORDER[_PERIOD_ORDER.index(period) + 1:]:
            broader_file = _team_period_issues_cache_file(broader)
            if not broader_file.exists():
                continue
            try:
                raw = json.loads(broader_file.read_text())
                cached_at = raw.get("cached_at", 0)
                if time.time() - cached_at >= _TEAM_PERIOD_ISSUES_TTL_SECONDS:
                    continue  # broader cache itself is stale — fall through
                filtered = [i for i in raw.get("issues", []) if (i.get("created_at") or "") >= created_after]
            except (json.JSONDecodeError, OSError, KeyError):
                continue

            # Persist the derived slice under its own key too, so a repeat
            # request for this exact period hits it directly next time.
            # Keeps the source's cached_at (not time.time()) so it expires
            # at the same wall-clock moment the broader data would.
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"cached_at": cached_at, "issues": filtered}, default=str))
            return filtered

    if not force_refresh and cached_issues is not None:
        print(f"pylon_client: serving stale team_period_issues cache for period={period!r} "
              "(background warmer will refresh it)")
        return cached_issues

    created_after, created_before = make_date_range(period)
    issues = search_all_issues(created_after=created_after, created_before=created_before)
    trimmed = [_trim_issue_for_team_dashboard(i) for i in issues]

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"cached_at": time.time(), "issues": trimmed}, default=str))
    return trimmed


def get_team_backlog_issues(states: list[str], force_refresh: bool = False) -> list[dict]:
    """Trimmed, org-wide currently-open issues (in any of `states`). Disk-cached 5 minutes.

    Callers should pass a consistent `states` list (e.g. the app's OPEN_STATES) —
    the cache key doesn't vary by state set, so mixing different state lists
    across call sites would serve stale/wrong data from the wrong query.

    Stale-while-revalidate, same as get_team_period_issues: if the cache is
    expired but present, returns the stale copy rather than blocking a live
    request on a slow org-wide fetch. main.py's _team_cache_warmer_loop keeps
    this fresh in the background.
    """
    cached_issues: list[dict] | None = None
    if _TEAM_BACKLOG_CACHE_FILE.exists():
        try:
            raw = json.loads(_TEAM_BACKLOG_CACHE_FILE.read_text())
            cached_issues = raw.get("issues", [])
            if not force_refresh and time.time() - raw.get("cached_at", 0) < _TEAM_BACKLOG_TTL_SECONDS:
                return cached_issues
        except (json.JSONDecodeError, OSError, KeyError):
            cached_issues = None

    if not force_refresh and cached_issues is not None:
        print("pylon_client: serving stale team_backlog cache (background warmer will refresh it)")
        return cached_issues

    issues = search_all_issues(states=states)
    trimmed = [_trim_issue_for_team_dashboard(i) for i in issues]

    _TEAM_BACKLOG_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TEAM_BACKLOG_CACHE_FILE.write_text(
        json.dumps({"cached_at": time.time(), "issues": trimmed}, default=str)
    )
    return trimmed


def get_issue_messages(issue_id: str) -> list[dict]:
    """Get the message history for a Pylon issue.

    Args:
        issue_id: The issue UUID.

    Returns:
        List of message dicts. Never None — Pylon has been observed returning
        {"data": null} (not just an absent key) for at least one ticket, and
        `.get("data", [])` only covers the absent-key case.
    """
    data = _get(f"/issues/{issue_id}/messages")
    return data.get("data") or []


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

    period: "7d" | "1m" | "3m" | "6m" | "1y"

    Shared by both the customer-account dashboard and the internal team
    dashboard, so this fix (the "3m" key was previously missing here,
    silently falling through to the 183-day/"6m" default) applies to both.
    """
    now = datetime.now(timezone.utc)
    days = {"7d": 7, "1m": 30, "3m": 91, "6m": 183, "1y": 365}.get(period, 183)
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
