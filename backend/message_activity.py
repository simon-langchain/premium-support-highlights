"""Background sync of per-ticket message activity for the internal Support-Team Dashboard.

Tracks how many messages each (non-admin) Support-team member has posted on
their assigned tickets, bucketed by day, so the dashboard can show an
"updates over time" trend without ever fetching messages for every ticket on
every request — Pylon rate-limits GET /issues/{id}/messages to 20/min
(see pylon_client.py's module docstring), which makes an on-demand fetch a
non-starter at this team's ticket volume (measured live: 10,000+ tickets
touched in 13 months for just 10 non-admin reps).

Only per-day message COUNTS are ever persisted — never message content,
mirroring pylon_client._trim_issue_for_team_dashboard's privacy-conscious
trimming. Customer-authored messages (author.contact) are excluded; only
internal-authored messages (author.user) count as "updates".

Sync strategy: a staged, newest-first backfill (7d -> 1m -> 3m -> 6m -> 1y)
so the dashboard has useful recent data almost immediately while older
history fills in behind it, followed by a periodic incremental pass that
only re-checks tickets updated since the last run. Both stages call
pylon_client.acquire_messages_slot(background=True) before every message
fetch — a single limiter shared with the customer-facing AI summary feature
(summary_agent.py), which gets priority, so this always-on sync can never
starve that on-demand, latency-sensitive feature of Pylon's rate budget.
Driven by a background asyncio loop started from main.py's app lifespan —
see run_backfill_step() / run_incremental_sync_step() / backfill_complete().
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import auth as auth_mod
import pylon_client

_CACHE_DIR = Path(__file__).parent / ".cache"
_STORE_FILE = _CACHE_DIR / "team_message_activity.json"

# Newest-first so the most-used dashboard views (7d/1m) become accurate almost
# immediately; each later stage only fetches the *delta* beyond the previous
# stage's boundary, so nothing already-synced gets re-fetched.
_STAGES = ["7d", "1m", "3m", "6m", "1y"]

# How many tickets to sync per run_backfill_step() call — keeps each call
# short (~1 min at the pacing above) so progress persists to disk regularly
# and the loop stays responsive to a restart rather than running for hours
# inside one call.
_BACKFILL_CHUNK_SIZE = 20

# Bump whenever stored data can't be trusted or migrated under the new code
# (e.g. a new/changed aggregate that can't be recovered without re-fetching).
# _load() discards the whole store on a mismatch; combined with the staged/
# resumable backfill, that's a clean way to force a full recompute — the
# backfill loop just starts over from "7d".
#
# v3: response_seconds switched from a per-day {total_seconds, count}
# aggregate to a per-day list of raw values, so median (not just average)
# can be computed at read time.
# v4: response_seconds no longer includes a synthetic "ongoing wait" entry
# for open tickets (that's now metrics.compute_pending_wait_stats, computed
# live) — old entries can be contaminated with those values.
_SCHEMA_VERSION = 4

# How often to fetch a fresh incremental-sync candidate list. Gated here
# (persisted, disk-backed) rather than by an in-process timer in main.py, so
# a backend restart doesn't reset the clock and immediately trigger a fresh
# pass.
_INCREMENTAL_QUEUE_INTERVAL_SECONDS = 15 * 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    if not _STORE_FILE.exists():
        return {"schema_version": _SCHEMA_VERSION}
    try:
        store = json.loads(_STORE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"schema_version": _SCHEMA_VERSION}
    if store.get("schema_version") != _SCHEMA_VERSION:
        return {"schema_version": _SCHEMA_VERSION}
    return store


def _save(store: dict) -> None:
    store["schema_version"] = _SCHEMA_VERSION
    _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STORE_FILE.write_text(json.dumps(store, default=str))


def _new_backfill_state() -> dict:
    return {
        "stage_index": 0,
        "stages": list(_STAGES),
        "stages_completed": [],
        "pending": [],  # [{"id": ..., "updated_at": ...}, ...] remaining in the current stage
        "current_stage_total": 0,
        "current_stage_synced": 0,
        "started_at": _now_iso(),
        "complete": False,
    }


async def _throttled_get_messages(issue_id: str) -> list[dict]:
    """Rate-limited wrapper around pylon_client.get_issue_messages.

    Paced through pylon_client's shared, foreground-priority limiter
    (background=True) rather than an independent local pacer — this sync
    runs continuously for the app's lifetime, and the customer-facing AI
    summary feature (summary_agent.py) hits the same endpoint on demand, so
    both need to share one rate budget with the summary feature prioritised.
    """
    await pylon_client.acquire_messages_slot(background=True)
    return await asyncio.to_thread(pylon_client.get_issue_messages, issue_id)


def _count_internal_messages_by_day(messages: list[dict] | None) -> dict[str, dict[str, int]]:
    """{member_id: {"YYYY-MM-DD": count}} for one ticket's messages.

    Only counts internal-authored messages (author.user.id) — customer
    replies (author.contact) don't count as a team member's "update".

    This also naturally excludes Pylon's AI agent ("Blu" in this workspace —
    role=Admin, no email, not a Support-team member): its author.user.id just
    never matches a tracked member in get_member_daily_counts, so its replies
    on a rep's assigned tickets are silently dropped rather than attributed to
    anyone. Confirmed intentional: only a human rep's own typed messages
    should count, even when Blu has replied on their behalf.

    messages is typed as possibly-None defensively — pylon_client.get_issue_messages
    itself now guarantees a list, but this stays defensive in case some other
    caller passes through a raw/unvalidated value.
    """
    if not messages:
        return {}
    counts: dict[str, dict[str, int]] = {}
    for msg in messages:
        author = msg.get("author") or {}
        member_id = (author.get("user") or {}).get("id")
        if not member_id:
            continue
        ts = msg.get("timestamp") or msg.get("created_at") or ""
        day = ts[:10]
        if len(day) != 10:
            continue
        by_day = counts.setdefault(member_id, {})
        by_day[day] = by_day.get(day, 0) + 1
    return counts


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _compute_response_times_by_day(
    messages: list[dict] | None, tracked_member_ids: set[str]
) -> dict[str, dict[str, list[float]]]:
    """{member_id: {"YYYY-MM-DD": [gap_seconds, ...]}} — the elapsed time
    between a customer's message and a tracked rep's next public reply to
    it, bucketed by the day the reply was sent. Raw values (not a running
    sum/count) so avg AND median can both be computed at read time.

    Only records COMPLETED gaps — a customer message still awaiting a reply
    (ticket currently new/waiting_on_you) contributes nothing here. That
    current wait is a separate, live-computed metric — see
    metrics.compute_pending_wait_stats, which reads it straight off the
    ticket's latest_message_time on every request rather than baking it into
    this synced/day-bucketed metric (which would otherwise make "today"
    balloon with every still-open ticket's ever-growing wait).

    Only privacy (is_private), not authorship, determines transparency:
    - A customer message (author.contact) becomes the pending "waiting since".
    - A private/internal note is skipped entirely, regardless of who wrote
      it (including a tracked rep's own internal notes) — it isn't a reply
      the customer can see, so it must not close the gap.
    - Any PUBLIC message clears the pending wait — the customer got some
      kind of response — but only a public message from a tracked non-admin
      rep gets the gap recorded and credited to them. A public reply from
      anyone else (Blu, an admin, someone from another team) still clears
      pending (so a later rep reply isn't wrongly measured from further
      back than it should be), it's just not credited to anyone, since
      they're not who this metric measures.
    """
    if not messages:
        return {}
    try:
        sorted_msgs = sorted(messages, key=lambda m: m.get("timestamp") or m.get("created_at") or "")
    except Exception:
        return {}

    result: dict[str, dict[str, list[float]]] = {}
    pending_since: datetime | None = None
    for msg in sorted_msgs:
        raw_ts = msg.get("timestamp") or msg.get("created_at") or ""
        ts = _parse_ts(raw_ts)
        if ts is None:
            continue
        author = msg.get("author") or {}

        if author.get("contact"):
            pending_since = ts
            continue

        if msg.get("is_private"):
            continue  # internal note — transparent regardless of author

        # Any public message clears the pending wait; only crediting a
        # tracked rep with the gap is conditional.
        member_id = (author.get("user") or {}).get("id")
        if pending_since is not None and member_id in tracked_member_ids:
            gap = (ts - pending_since).total_seconds()
            if gap >= 0:
                day = raw_ts[:10]
                if len(day) == 10:
                    result.setdefault(member_id, {}).setdefault(day, []).append(gap)
        pending_since = None

    return result


async def _non_admin_member_ids() -> list[str]:
    members = await asyncio.to_thread(pylon_client.get_support_team_member_ids)
    admin_emails = await asyncio.to_thread(auth_mod.get_admin_emails)
    return [mid for mid, email in members.items() if (email or "").strip().lower() not in admin_emails]


def _needs_sync(store: dict, ticket_id: str, updated_at: str | None) -> bool:
    existing = store.get("tickets", {}).get(ticket_id)
    if not existing:
        return True
    return existing.get("synced_updated_at") != updated_at


def _tracked_window_start() -> str:
    """Oldest boundary any stage ever needs — the start of the broadest stage."""
    start, _ = pylon_client.make_date_range(_STAGES[-1])
    return start


async def _sync_one_ticket(
    ticket_id: str, updated_at: str | None, store: dict, tracked_member_ids: set[str]
) -> None:
    """Sync one ticket's messages into the store.

    The whole body is wrapped in try/except, not just the network fetch:
    run_backfill_step pops the ticket off the pending queue before calling
    this and only persists that pop afterward, so an uncaught exception here
    would abort the step before the pop is saved — the same ticket then
    reappears at the front of the queue forever, stalling the rest of the
    backfill on one bad ticket. Any failure just leaves this ticket unsynced;
    it's naturally retried next time something touches it.
    """
    try:
        messages = await _throttled_get_messages(ticket_id)
        counts = _count_internal_messages_by_day(messages)
        response_seconds = _compute_response_times_by_day(messages, tracked_member_ids)
    except Exception:
        return
    store.setdefault("tickets", {})[ticket_id] = {
        "synced_updated_at": updated_at,
        "counts": counts,
        "response_seconds": response_seconds,
    }


async def _populate_next_stage(store: dict, backfill: dict) -> bool:
    """Fetch the candidate ticket list for the next backfill stage.

    Returns False once every stage has been populated (backfill is done).
    """
    idx = backfill["stage_index"]
    stages = backfill["stages"]
    if idx >= len(stages):
        return False
    stage = stages[idx]
    prev_stage = stages[idx - 1] if idx > 0 else None

    stage_start, _ = pylon_client.make_date_range(stage)
    updated_before = pylon_client.make_date_range(prev_stage)[0] if prev_stage else None

    member_ids = await _non_admin_member_ids()
    issues = await asyncio.to_thread(
        pylon_client.search_issues_by_assignees, member_ids, stage_start, updated_before
    )

    window_start = _tracked_window_start()
    candidates = [
        {"id": i["id"], "updated_at": i.get("updated_at")}
        for i in issues
        if i.get("id")
        and (i.get("latest_message_time") or "") >= window_start
        and _needs_sync(store, i["id"], i.get("updated_at"))
    ]

    backfill["pending"] = candidates
    backfill["current_stage_total"] = len(candidates)
    backfill["current_stage_synced"] = 0
    return True


async def run_backfill_step(chunk_size: int = _BACKFILL_CHUNK_SIZE) -> bool:
    """Process one bounded chunk of the backfill. Returns True if there's more work to do.

    Safe to call repeatedly in a loop (main.py does exactly that) — each call
    is short, persists progress to disk as it goes, and picks up wherever the
    stored state left off, so a restart mid-backfill resumes rather than
    starting over.
    """
    store = await asyncio.to_thread(_load)
    backfill = store.setdefault("backfill", _new_backfill_state())
    if backfill.get("complete"):
        return False

    tracked_member_ids = set(await _non_admin_member_ids())

    if not backfill["pending"]:
        has_more_stages = await _populate_next_stage(store, backfill)
        if not has_more_stages:
            backfill["complete"] = True
            await asyncio.to_thread(_save, store)
            return False
        await asyncio.to_thread(_save, store)
        if not backfill["pending"]:
            # This stage had no candidates — advance immediately, more work may remain.
            backfill["stages_completed"].append(backfill["stages"][backfill["stage_index"]])
            backfill["stage_index"] += 1
            await asyncio.to_thread(_save, store)
            return True

    for _ in range(chunk_size):
        if not backfill["pending"]:
            break
        ticket = backfill["pending"].pop(0)
        await _sync_one_ticket(ticket["id"], ticket.get("updated_at"), store, tracked_member_ids)
        backfill["current_stage_synced"] += 1
        await asyncio.to_thread(_save, store)

    if not backfill["pending"]:
        backfill["stages_completed"].append(backfill["stages"][backfill["stage_index"]])
        backfill["stage_index"] += 1
        await asyncio.to_thread(_save, store)

    return True


def backfill_complete() -> bool:
    store = _load()
    return (store.get("backfill") or {}).get("complete", False)


async def _populate_incremental_queue(store: dict) -> None:
    """Fetch a fresh incremental-sync candidate list: tickets updated since
    the last cycle.

    Stashes the result in store["incremental_pending"], a resumable queue
    drained a chunk at a time by run_incremental_sync_step — mirroring the
    backfill's own `pending` list — rather than synced all at once here
    (a burst of updates after downtime could otherwise mean a single
    unbounded pass).
    """
    since = store.get("last_incremental_sync_at") or pylon_client.make_date_range("7d")[0]
    member_ids = await _non_admin_member_ids()
    issues = await asyncio.to_thread(
        pylon_client.search_issues_by_assignees, member_ids, since
    )

    window_start = _tracked_window_start()
    store["incremental_pending"] = [
        {"id": i["id"], "updated_at": i.get("updated_at")}
        for i in issues
        if i.get("id") and (i.get("latest_message_time") or "") >= window_start
    ]
    store["last_incremental_sync_at"] = _now_iso()


async def run_incremental_sync_step(chunk_size: int = _BACKFILL_CHUNK_SIZE) -> bool:
    """Process one bounded chunk of the incremental sync. Returns True if
    there's more queued work to do (call again soon), False if this cycle's
    queue is empty and a new one isn't due yet.

    Chunked exactly like run_backfill_step, for the same reason: keeping
    each call short means progress persists to disk regularly and
    _message_sync_loop's sequential backfill-step/incremental-step calls
    both get a turn rather than one blocking the other for an unbounded
    duration.
    """
    store = await asyncio.to_thread(_load)

    if not store.get("incremental_pending"):
        last_raw = store.get("last_incremental_sync_at")
        last = _parse_ts(last_raw) if last_raw else None
        due = last is None or (datetime.now(timezone.utc) - last).total_seconds() >= _INCREMENTAL_QUEUE_INTERVAL_SECONDS
        if not due:
            return False
        await _populate_incremental_queue(store)
        await asyncio.to_thread(_save, store)
        if not store.get("incremental_pending"):
            return False

    tracked_member_ids = set(await _non_admin_member_ids())
    pending: list[dict] = store["incremental_pending"]
    for _ in range(chunk_size):
        if not pending:
            break
        info = pending.pop(0)
        ticket_id = info.get("id")
        if ticket_id and _needs_sync(store, ticket_id, info.get("updated_at")):
            await _sync_one_ticket(ticket_id, info.get("updated_at"), store, tracked_member_ids)
        await asyncio.to_thread(_save, store)

    return bool(pending)


def get_member_daily_counts(member_ids: list[str]) -> dict[str, dict[str, int]]:
    """{member_id: {"YYYY-MM-DD": count}}, summed across all tracked tickets."""
    store = _load()
    result: dict[str, dict[str, int]] = {mid: {} for mid in member_ids}
    for ticket in store.get("tickets", {}).values():
        for member_id, day_counts in (ticket.get("counts") or {}).items():
            bucket = result.get(member_id)
            if bucket is None:
                continue  # not currently a tracked member (e.g. departed) — skip
            for day, count in day_counts.items():
                bucket[day] = bucket.get(day, 0) + count
    return result


def get_member_response_seconds(member_ids: list[str]) -> dict[str, dict[str, list[float]]]:
    """{member_id: {"YYYY-MM-DD": [gap_seconds, ...]}}, pooled across all
    tracked tickets — mirrors get_member_daily_counts. Raw values (not just
    a sum/count) are kept so avg AND median can both be computed at read
    time — see metrics.compute_response_time_trend / compute_response_time_summary."""
    store = _load()
    result: dict[str, dict[str, list[float]]] = {mid: {} for mid in member_ids}
    for ticket in store.get("tickets", {}).values():
        for member_id, day_values in (ticket.get("response_seconds") or {}).items():
            bucket = result.get(member_id)
            if bucket is None:
                continue  # not currently a tracked member (e.g. departed) — skip
            for day, values in day_values.items():
                bucket.setdefault(day, []).extend(values)
    return result


def get_backfill_status() -> dict:
    store = _load()
    backfill = store.get("backfill") or _new_backfill_state()
    stage_index = backfill.get("stage_index", 0)
    stages = backfill.get("stages", _STAGES)
    return {
        "stage": stages[stage_index] if stage_index < len(stages) else None,
        "stages_completed": backfill.get("stages_completed", []),
        "current_stage_synced": backfill.get("current_stage_synced", 0),
        "current_stage_total": backfill.get("current_stage_total", 0),
        "complete": backfill.get("complete", False),
        "started_at": backfill.get("started_at"),
        "last_incremental_sync_at": store.get("last_incremental_sync_at"),
    }
