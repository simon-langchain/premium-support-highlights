"""Compute monthly support metrics from Pylon issue data.

All functions accept raw issue dicts as returned by the Pylon REST API.
"""

import statistics
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


def _collect_valid_seconds(
    issues: list[dict], field: str, required_states: set[str] | None = None
) -> list[float]:
    """Shared filtering for the business-hours duration fields Pylon attaches
    to issues: valid positive numeric values only, optionally restricted to
    issues in one of `required_states` (e.g. resolution time only makes
    sense for closed/resolved issues). Shared by the avg/median twins below
    so both apply identical filtering.
    """
    values: list[float] = []
    for issue in issues:
        if required_states is not None and issue.get("state") not in required_states:
            continue
        seconds = issue.get(field)
        if seconds is None:
            continue
        try:
            s = float(seconds)
        except (TypeError, ValueError):
            continue
        if s <= 0:
            continue
        values.append(s)
    return values


def compute_avg_response_time(issues: list[dict]) -> float | None:
    """Compute average first-response time in business hours from issue data.

    Uses Pylon's `business_hours_first_response_seconds` field (Mon–Fri 9–5
    in the account's configured timezone) for all issues that have received
    a response.

    Returns:
        Average response time in business hours, or None if no data is available.
    """
    values = _collect_valid_seconds(issues, "business_hours_first_response_seconds")
    if not values:
        return None
    return (sum(values) / len(values)) / 3600


def compute_median_response_time(issues: list[dict]) -> float | None:
    """Median counterpart to compute_avg_response_time — a single stuck/slow
    ticket can swing the mean far from what a typical ticket looked like;
    median stays anchored to the typical experience. Same filtering."""
    values = _collect_valid_seconds(issues, "business_hours_first_response_seconds")
    if not values:
        return None
    return statistics.median(values) / 3600


def compute_sla_compliance(issues: list[dict], threshold_hours: float = 24.0) -> int | None:
    """Percentage of issues with business_hours_first_response_seconds within threshold_hours.

    Returns an integer 0-100, or None if no issues have response time data.
    """
    threshold_secs = threshold_hours * 3600
    total = 0
    within = 0
    for issue in issues:
        seconds = issue.get("business_hours_first_response_seconds")
        if seconds is None:
            continue
        try:
            s = float(seconds)
        except (TypeError, ValueError):
            continue
        if s <= 0:
            continue
        total += 1
        if s <= threshold_secs:
            within += 1
    if total == 0:
        return None
    return round(within / total * 100)


_RESOLVED_STATES = {"closed", "resolved"}


def compute_avg_resolution_time(issues: list[dict]) -> float | None:
    """Compute average resolution time in business hours from closed issue data.

    Uses Pylon's `business_hours_resolution_seconds` field for all closed/resolved
    issues that have a resolution time recorded.

    Returns:
        Average resolution time in business hours, or None if no data is available.
    """
    values = _collect_valid_seconds(issues, "business_hours_resolution_seconds", _RESOLVED_STATES)
    if not values:
        return None
    return (sum(values) / len(values)) / 3600


def compute_median_resolution_time(issues: list[dict]) -> float | None:
    """Median counterpart to compute_avg_resolution_time. Same filtering."""
    values = _collect_valid_seconds(issues, "business_hours_resolution_seconds", _RESOLVED_STATES)
    if not values:
        return None
    return statistics.median(values) / 3600


def _collect_csat_scores(issues: list[dict]) -> list[float]:
    """Flatten every issue's csat_responses (an issue can in principle have
    more than one response) into a list of scores. Shared by the avg/median
    twins below."""
    scores: list[float] = []
    for issue in issues:
        responses = issue.get("csat_responses") or []
        if not isinstance(responses, list):
            continue
        for response in responses:
            if not isinstance(response, dict):
                continue
            score = response.get("score")
            if score is None:
                continue
            try:
                s = float(score)
            except (TypeError, ValueError):
                continue
            scores.append(s)
    return scores


def compute_avg_csat(issues: list[dict]) -> float | None:
    """Compute the average CSAT score from issues' csat_responses arrays.

    Returns:
        Average CSAT score, or None if no responses are available.
    """
    scores = _collect_csat_scores(issues)
    if not scores:
        return None
    return sum(scores) / len(scores)


def compute_median_csat(issues: list[dict]) -> float | None:
    """Median counterpart to compute_avg_csat. Same filtering."""
    scores = _collect_csat_scores(issues)
    if not scores:
        return None
    return statistics.median(scores)


# Shared by compute_rep_trend/compute_message_activity_trend's day/week bucket
# counts — not "bucket logic" itself (see those functions' docstrings for why
# the grouping loops stay separate), just the period -> day-span mapping,
# same values as pylon_client.make_date_range.
_PERIOD_DAYS = {"7d": 7, "1m": 30, "3m": 91, "6m": 183, "1y": 365}
_PERIOD_MONTHS = {"7d": 1, "1m": 1, "3m": 3, "6m": 6, "1y": 12}


def _default_granularity(period: str) -> str:
    return "day" if period in ("7d", "1m") else "month"


def _month_buckets(period: str, now: datetime) -> list[tuple[int, int]]:
    months = _PERIOD_MONTHS.get(period, 6)
    year, month = now.year, now.month
    buckets: list[tuple[int, int]] = []
    for offset in range(months - 1, -1, -1):
        m = month - offset
        y = year
        while m <= 0:
            m += 12
            y -= 1
        buckets.append((y, m))
    return buckets


def _week_starts(period: str, now: datetime) -> list[date]:
    """Monday-anchored week buckets, oldest to newest, covering the period's day-span."""
    days = _PERIOD_DAYS.get(period, 183)
    weeks = max(1, -(-days // 7))  # ceil division
    this_monday = now.date() - timedelta(days=now.date().weekday())
    return [this_monday - timedelta(weeks=offset) for offset in range(weeks - 1, -1, -1)]


def compute_rep_trend(issues: list[dict], period: str, granularity: str | None = None) -> list[dict]:
    """Bucket issues by period and compute per-bucket tickets_taken, avg_response_time,
    and avg_resolution_time (oldest to newest, empty buckets included).

    granularity ("day" | "week" | "month") overrides the period's default
    bucketing (day for 7d/1m, month otherwise) — e.g. a "6m" period can be
    viewed bucketed by week instead of by month. None keeps the default.

    Mirrors the day/month windowing in compute_period_metrics/compute_monthly_metrics,
    but tracks per-bucket averages instead of just raised/closed counts — kept as a
    separate self-contained function rather than a shared refactor, to avoid touching
    the existing (working) account-dashboard trend functions.
    """
    now = datetime.now(timezone.utc)
    effective = granularity or _default_granularity(period)

    if effective == "day":
        days = _PERIOD_DAYS.get(period, 183)
        bucket_dates = [now.date() - timedelta(days=i) for i in range(days - 1, -1, -1)]
        grouped: dict[date, list[dict]] = {d: [] for d in bucket_dates}
        for issue in issues:
            dt = _parse_dt(issue.get("created_at"))
            if dt is None:
                continue
            key = dt.date()
            if key in grouped:
                grouped[key].append(issue)
        ordered = [(d.strftime("%b %-d"), grouped[d]) for d in bucket_dates]
    elif effective == "week":
        week_starts = _week_starts(period, now)
        grouped_weeks: dict[date, list[dict]] = {ws: [] for ws in week_starts}
        for issue in issues:
            dt = _parse_dt(issue.get("created_at"))
            if dt is None:
                continue
            wk_start = dt.date() - timedelta(days=dt.date().weekday())
            if wk_start in grouped_weeks:
                grouped_weeks[wk_start].append(issue)
        ordered = [(ws.strftime("%b %-d"), grouped_weeks[ws]) for ws in week_starts]
    else:  # month
        unique_buckets = _month_buckets(period, now)
        grouped_months: dict[tuple[int, int], list[dict]] = {b: [] for b in unique_buckets}
        for issue in issues:
            dt = _parse_dt(issue.get("created_at"))
            if dt is None:
                continue
            key = (dt.year, dt.month)
            if key in grouped_months:
                grouped_months[key].append(issue)
        ordered = [(f"{month_abbr[m]} {y}", grouped_months[(y, m)]) for y, m in unique_buckets]

    return [
        {
            "label": label,
            "tickets_taken": len(bucket_issues),
            "avg_response_time": compute_avg_response_time(bucket_issues),
            "median_response_time": compute_median_response_time(bucket_issues),
            "avg_resolution_time": compute_avg_resolution_time(bucket_issues),
            "median_resolution_time": compute_median_resolution_time(bucket_issues),
        }
        for label, bucket_issues in ordered
    ]


def compute_message_activity_trend(
    daily_counts: dict[str, int], period: str, granularity: str | None = None
) -> list[dict]:
    """Bucket a {"YYYY-MM-DD": count} dict into the same day/week/month windows as
    compute_rep_trend, returning [{"label": ..., "update_count": ...}].

    granularity behaves identically to compute_rep_trend's — None keeps the
    period's default (day for 7d/1m, month otherwise).

    Sums from pre-aggregated daily counts (message_activity.py's sync store)
    rather than grouping issue objects — message-level data, not issue-level —
    so this can't share compute_rep_trend's grouping loop directly. Kept
    self-contained rather than factored out, matching compute_rep_trend's own
    stated convention of not sharing bucket logic across trend functions.
    """
    now = datetime.now(timezone.utc)
    effective = granularity or _default_granularity(period)

    if effective == "day":
        days = _PERIOD_DAYS.get(period, 183)
        bucket_dates = [now.date() - timedelta(days=i) for i in range(days - 1, -1, -1)]
        ordered = [
            (d.strftime("%b %-d"), daily_counts.get(d.isoformat(), 0))
            for d in bucket_dates
        ]
    elif effective == "week":
        week_starts = _week_starts(period, now)

        def _week_total(ws: date) -> int:
            we = ws + timedelta(days=6)
            return sum(
                count for day, count in daily_counts.items()
                if ws.isoformat() <= day <= we.isoformat()
            )

        ordered = [(ws.strftime("%b %-d"), _week_total(ws)) for ws in week_starts]
    else:  # month
        unique_buckets = _month_buckets(period, now)

        def _month_total(y: int, m: int) -> int:
            prefix = f"{y:04d}-{m:02d}-"
            return sum(count for day, count in daily_counts.items() if day.startswith(prefix))

        ordered = [(f"{month_abbr[m]} {y}", _month_total(y, m)) for y, m in unique_buckets]

    return [{"label": label, "update_count": total} for label, total in ordered]


def compute_response_time_trend(
    daily_response_data: dict[str, list[float]], period: str, granularity: str | None = None
) -> list[dict]:
    """Bucket a {"YYYY-MM-DD": [gap_seconds, ...]} dict into the same
    day/week/month windows as the other trend functions, returning
    [{"label": ..., "avg_reply_time": hrs|None, "median_reply_time": hrs|None,
    "reply_count": int}].

    avg/median_reply_time are wall-clock hours between a customer's message
    and a rep's next public reply to it (see
    message_activity._compute_response_times_by_day) — NOT business-hours-
    adjusted like avg_response_time, which is a value Pylon itself computes
    for a ticket's first response only; no equivalent business-hours
    calendar is available for arbitrary message pairs, so this is
    deliberately a different kind of number.

    Week/month buckets pool every matching day's raw values together before
    computing avg/median for that bucket — median isn't associative across
    days the way a sum is, so it can't be derived from already-computed
    per-day averages; the underlying values have to be combined first.
    """
    now = datetime.now(timezone.utc)
    effective = granularity or _default_granularity(period)

    if effective == "day":
        days = _PERIOD_DAYS.get(period, 183)
        bucket_dates = [now.date() - timedelta(days=i) for i in range(days - 1, -1, -1)]
        ordered = [
            (d.strftime("%b %-d"), daily_response_data.get(d.isoformat(), []))
            for d in bucket_dates
        ]
    elif effective == "week":
        week_starts = _week_starts(period, now)

        def _week_values(ws: date) -> list[float]:
            we = ws + timedelta(days=6)
            pooled: list[float] = []
            for day, values in daily_response_data.items():
                if ws.isoformat() <= day <= we.isoformat():
                    pooled.extend(values)
            return pooled

        ordered = [(ws.strftime("%b %-d"), _week_values(ws)) for ws in week_starts]
    else:  # month
        unique_buckets = _month_buckets(period, now)

        def _month_values(y: int, m: int) -> list[float]:
            prefix = f"{y:04d}-{m:02d}-"
            pooled: list[float] = []
            for day, values in daily_response_data.items():
                if day.startswith(prefix):
                    pooled.extend(values)
            return pooled

        ordered = [(f"{month_abbr[m]} {y}", _month_values(y, m)) for y, m in unique_buckets]

    return [
        {
            "label": label,
            "avg_reply_time": round(sum(values) / len(values) / 3600, 1) if values else None,
            "median_reply_time": round(statistics.median(values) / 3600, 1) if values else None,
            "reply_count": len(values),
        }
        for label, values in ordered
    ]


def compute_response_time_summary(daily_response_data: dict[str, list[float]], period: str) -> dict:
    """Pool every value within the period's day-window into one flat list and
    compute {"avg": hrs|None, "median": hrs|None, "count": int} directly.

    Unlike compute_response_time_trend's per-bucket stats, a period-total
    median can't be reconstructed from already-bucketed aggregates the way a
    weighted-average total can from bucket averages+counts — median isn't
    associative — so this pools the full period's raw values and computes
    both stats over that, rather than combining bucket-level results.
    """
    now = datetime.now(timezone.utc)
    days = _PERIOD_DAYS.get(period, 183)
    window_start = (now - timedelta(days=days - 1)).date()

    pooled: list[float] = []
    for day, values in daily_response_data.items():
        try:
            d = date.fromisoformat(day)
        except ValueError:
            continue
        if d >= window_start:
            pooled.extend(values)

    if not pooled:
        return {"avg": None, "median": None, "count": 0}
    return {
        "avg": round(sum(pooled) / len(pooled) / 3600, 1),
        "median": round(statistics.median(pooled) / 3600, 1),
        "count": len(pooled),
    }


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


# Tickets in these states have a customer message awaiting a first response —
# see compute_pending_wait_stats.
_PENDING_RESPONSE_STATES = {"new", "waiting_on_you"}


def compute_pending_wait_stats(open_issues: list[dict], now: datetime | None = None) -> dict[str, dict]:
    """{"new": {"count", "avg_wait_hours", "median_wait_hours"}, "waiting_on_you": {...}}
    for currently-open tickets awaiting a first response.

    Computed fresh from each ticket's latest_message_time — in these two
    states the most recent message is always the customer's (a rep reply
    would move the ticket to waiting_on_customer), so it marks when the
    wait began. Deliberately not synced/cached like the completed-reply
    metric in message_activity._compute_response_times_by_day, so a
    long-open ticket can't inflate "today" in that trend.

    Only states with at least one qualifying ticket appear in the result.
    """
    now = now or datetime.now(timezone.utc)
    waits_by_state: dict[str, list[float]] = {}
    for issue in open_issues:
        state = issue.get("state")
        if state not in _PENDING_RESPONSE_STATES:
            continue
        dt = _parse_dt(issue.get("latest_message_time"))
        if dt is None:
            continue
        wait = (now - dt).total_seconds()
        if wait < 0:
            continue
        waits_by_state.setdefault(state, []).append(wait)

    return {
        state: {
            "count": len(waits),
            "avg_wait_hours": round(sum(waits) / len(waits) / 3600, 1),
            "median_wait_hours": round(statistics.median(waits) / 3600, 1),
        }
        for state, waits in waits_by_state.items()
    }
