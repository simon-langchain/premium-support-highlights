"""Compute monthly support metrics from Pylon issue data.

All functions accept raw issue dicts as returned by the Pylon REST API.
"""

from datetime import datetime, timedelta, timezone, date
from calendar import month_abbr


def _parse_dt(iso_str: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp, returning None on failure."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def compute_monthly_metrics(issues: list[dict], months: int = 6) -> list[dict]:
    """Compute monthly ticket counts for the last N months.

    Generates a bucket for every month in the window, even if zero tickets
    were created that month, so charts never have gaps.

    Returns:
        List of dicts sorted oldest-to-newest, each with keys:
          month (str), year (int), month_num (int),
          tickets_raised (int), closed_tickets (int)
    """
    now = datetime.now(timezone.utc)

    # Build ordered bucket list: (year, month_num) tuples by subtracting
    # calendar months directly — avoids 30-day approximation skipping short
    # months like February when the current day is the 29th, 30th, or 31st.
    year, month = now.year, now.month
    unique_buckets: list[tuple[int, int]] = []
    for offset in range(months - 1, -1, -1):
        m = month - offset
        y = year
        while m <= 0:
            m += 12
            y -= 1
        unique_buckets.append((y, m))

    CLOSED_STATES = {"closed", "resolved"}

    counts: dict[tuple[int, int], dict] = {
        b: {"tickets_raised": 0, "closed_tickets": 0} for b in unique_buckets
    }

    for issue in issues:
        created_dt = _parse_dt(issue.get("created_at"))
        if created_dt is None:
            continue
        key = (created_dt.year, created_dt.month)
        if key not in counts:
            continue
        counts[key]["tickets_raised"] += 1
        state = issue.get("state", "")
        if state in CLOSED_STATES:
            counts[key]["closed_tickets"] += 1

    result = []
    for year, month_num in unique_buckets:
        label = f"{month_abbr[month_num]} {year}"
        result.append({
            "month": label,
            "year": year,
            "month_num": month_num,
            "tickets_raised": counts[(year, month_num)]["tickets_raised"],
            "closed_tickets": counts[(year, month_num)]["closed_tickets"],
        })
    return result


def _compute_daily_metrics(issues: list[dict], days: int) -> list[dict]:
    """Compute daily ticket counts for the last N days."""
    now = datetime.now(timezone.utc).date()
    buckets: list[date] = [now - timedelta(days=i) for i in range(days - 1, -1, -1)]
    bucket_set = set(buckets)
    CLOSED_STATES = {"closed", "resolved"}

    counts: dict[date, dict] = {d: {"tickets_raised": 0, "closed_tickets": 0} for d in buckets}

    for issue in issues:
        created_dt = _parse_dt(issue.get("created_at"))
        if created_dt is None:
            continue
        key = created_dt.date()
        if key not in bucket_set:
            continue
        counts[key]["tickets_raised"] += 1
        if issue.get("state", "") in CLOSED_STATES:
            counts[key]["closed_tickets"] += 1

    return [
        {
            "month": d.strftime("%b %-d"),
            "tickets_raised": counts[d]["tickets_raised"],
            "closed_tickets": counts[d]["closed_tickets"],
        }
        for d in buckets
    ]


def compute_period_metrics(issues: list[dict], period: str) -> list[dict]:
    """Dispatch to daily or monthly metric computation based on period."""
    if period == "7d":
        return _compute_daily_metrics(issues, 7)
    if period == "1m":
        return _compute_daily_metrics(issues, 30)
    if period == "3m":
        return compute_monthly_metrics(issues, months=3)
    if period == "1y":
        return compute_monthly_metrics(issues, months=12)
    return compute_monthly_metrics(issues, months=6)  # default: 6m


def compute_avg_response_time(issues: list[dict]) -> float | None:
    """Compute average first-response time in hours from issue data.

    Uses the `first_response_seconds` field returned directly by Pylon for all
    issues that have received a response.

    Returns:
        Average response time in hours, or None if no data is available.
    """
    total_seconds = 0.0
    count = 0

    for issue in issues:
        seconds = issue.get("first_response_seconds")
        if seconds is None:
            continue
        try:
            s = float(seconds)
        except (TypeError, ValueError):
            continue
        if s <= 0:
            continue
        total_seconds += s
        count += 1

    if count == 0:
        return None
    return (total_seconds / count) / 3600


def get_priority(issue: dict) -> str:
    """Extract priority string from a Pylon issue dict.

    Pylon stores priority in custom_fields.priority.value, not as a top-level field.
    """
    custom_fields = issue.get("custom_fields") or {}
    if isinstance(custom_fields, dict):
        field = custom_fields.get("priority") or {}
        val = field.get("value") if isinstance(field, dict) else str(field)
        return val or "none"
    return "none"


_PRIORITY_ORDER = ["urgent", "high", "medium", "low", "none"]


def get_priority_breakdown(issues: list[dict]) -> dict[str, int]:
    """Count issues by priority level, ordered Sev 1 → Sev 4.

    Returns:
        Dict mapping priority string to count (e.g. {"high": 3, "medium": 5}).
    """
    counts: dict[str, int] = {}
    for issue in issues:
        priority = get_priority(issue)
        counts[priority] = counts.get(priority, 0) + 1
    # Return in severity order (urgent first), unknown keys appended after
    ordered = {k: counts[k] for k in _PRIORITY_ORDER if k in counts}
    ordered.update({k: v for k, v in counts.items() if k not in ordered})
    return ordered


def get_disposition_breakdown(issues: list[dict]) -> dict[str, int]:
    """Count issues by disposition slug.

    Returns:
        Dict mapping disposition slug to count (e.g. {"bug": 4, "feature_request": 2}).
    """
    counts: dict[str, int] = {}
    for issue in issues:
        custom_fields = issue.get("custom_fields") or {}
        if isinstance(custom_fields, dict):
            val = (custom_fields.get("disposition") or {}).get("value", "") or "unknown"
        else:
            val = "unknown"
        counts[val] = counts.get(val, 0) + 1
    return dict(sorted(counts.items()))


def get_state_breakdown(issues: list[dict]) -> dict[str, int]:
    """Count issues by state.

    Returns:
        Dict mapping state string to count (e.g. {"new": 2, "on_hold": 1}).
    """
    counts: dict[str, int] = {}
    for issue in issues:
        state = issue.get("state") or "unknown"
        counts[state] = counts.get(state, 0) + 1
    return dict(sorted(counts.items()))
