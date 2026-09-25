"""Account groups: merge several Pylon accounts into one virtual dashboard account.

Some customers are split across multiple Pylon accounts (e.g. "Orange Group"
and "Orange Business"). A group gives them a single synthetic account ID
(``grp_<hex>``) that every account-scoped code path accepts: pylon_client
expands it to the member account IDs and merges the results, so metrics,
summaries, reports, Slack/email, schedules and QBR decks all work unchanged.

Groups are stored in the shared LangGraph Platform Store (same reasoning as
auth.py — must be consistent across API server replicas). Callers are mostly
sync code running in worker threads (pylon_client), so this uses the sync SDK
client, with a short in-process cache so a single dashboard load doesn't hit
the Store once per Pylon call.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from datetime import datetime, timezone

import lg_store

_log = logging.getLogger(__name__)

GROUP_ID_PREFIX = "grp_"

_NS = ("psh_account_groups",)
_CACHE_TTL_SECONDS = 30

_lock = threading.Lock()
_cache: tuple[float, list[dict]] | None = None


def is_group_id(account_id: str) -> bool:
    return account_id.startswith(GROUP_ID_PREFIX)


def list_groups(force_refresh: bool = False) -> list[dict]:
    """Return all groups as [{id, name, member_ids, created_by, created_at}], sorted by name."""
    global _cache
    with _lock:
        if not force_refresh and _cache and time.monotonic() - _cache[0] < _CACHE_TTL_SECONDS:
            return _cache[1]
    groups = lg_store.search(_NS)
    groups.sort(key=lambda g: g.get("name", "").lower())
    with _lock:
        _cache = (time.monotonic(), groups)
    return groups


def get_group(group_id: str) -> dict | None:
    group = next((g for g in list_groups() if g.get("id") == group_id), None)
    if group is None and is_group_id(group_id):
        # Maybe created on another replica since our 30s cache was filled: re-read before
        # treating it as unknown (an empty group would otherwise cache an empty payload)
        group = next((g for g in list_groups(force_refresh=True) if g.get("id") == group_id), None)
    return group


def member_ids(account_id: str) -> list[str]:
    """Expand an account ID to the Pylon account IDs it covers.

    A plain Pylon ID maps to itself; an unknown group ID maps to nothing
    (rather than being sent to Pylon, which would just return no results).
    """
    if not is_group_id(account_id):
        return [account_id]
    group = get_group(account_id)
    if group is None:
        _log.warning("Unknown account group %s", account_id)
        return []
    return list(group.get("member_ids") or [])


def grouped_member_ids() -> dict[str, dict]:
    """Map each grouped Pylon account ID -> its group. Empty (logged) if the Store is unreachable,
    so the account list degrades to ungrouped rather than failing."""
    try:
        groups = list_groups()
    except Exception:
        _log.exception("Failed to load account groups")
        return {}
    return {mid: g for g in groups for mid in g.get("member_ids") or []}


class GroupConflict(ValueError):
    """An account is already in a group, or the name is taken."""


def create_group(name: str, member_ids: list[str], created_by: str, names_by_id: dict[str, str]) -> dict:
    """Create a group, checking name/member clashes against a fresh read under a lock so two
    concurrent creates can't put one account in two groups (within this process)."""
    with lg_store.lock(_NS, "__create__"):
        groups = list_groups(force_refresh=True)
        already = {mid: g for g in groups for mid in g.get("member_ids") or []}
        clashes = [f"{names_by_id.get(mid, mid)} (in {already[mid]['name']})" for mid in member_ids if mid in already]
        if clashes:
            raise GroupConflict(f"Already grouped: {', '.join(clashes)}")
        if any(g["name"].lower() == name.lower() for g in groups):
            raise GroupConflict(f"A group named {name!r} already exists")
        group = {
            "id": GROUP_ID_PREFIX + secrets.token_hex(8),
            "name": name,
            "member_ids": member_ids,
            "created_by": created_by,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        lg_store.put(_NS, group["id"], group)
        _invalidate()
    return group


MEMBER_COLOR_SLOTS = 8  # --member-1..8 in frontend globals.css
# Light-mode values of --member-1..8 (globals.css), for exports on light backgrounds (PDF/email)
MEMBER_COLOR_HEX = ("#2a78d6", "#e76530", "#00a16d", "#c77d00", "#d56a93", "#008300", "#4a3aa7", "#e34948")


def default_member_labels(group: dict, names_by_id: dict[str, str]) -> dict[str, str]:
    """Member id -> default short label: the leading words every member shares are dropped
    ("Orange Group" / "Orange Business" -> "Group" / "Business"), falling back to full names
    when that would leave a label empty or ambiguous. The single source for the dashboard
    (sent as `default_label`) and exports."""
    ids = list(group.get("member_ids") or [])
    names = [names_by_id.get(mid, mid) for mid in ids]
    words = [n.split() for n in names]
    shared = 0
    while words and all(len(w) > shared + 1 for w in words) and all(
        w[shared].lower() == words[0][shared].lower() for w in words
    ):
        shared += 1
    short = [" ".join(w[shared:]) for w in words]
    use_short = len({x.lower() for x in short}) == len(ids)
    return {mid: short[i] if use_short else names[i] for i, mid in enumerate(ids)}


def describe_members(group: dict, names_by_id: dict[str, str]) -> dict[str, dict]:
    """Member id -> {name, color (slot), color_hex, custom_label (or None), default_label,
    label (custom else default)}: the one place member display rules are resolved, for
    both the API (main._format_group) and exports (member_labels)."""
    slots = member_color_slots(group)
    defaults = default_member_labels(group, names_by_id)
    overrides = group.get("member_label_overrides") or {}
    return {
        mid: {
            "name": names_by_id.get(mid, mid),
            "color": slot,
            "color_hex": MEMBER_COLOR_HEX[(slot - 1) % MEMBER_COLOR_SLOTS],
            "custom_label": overrides.get(mid),
            "default_label": defaults[mid],
            "label": overrides.get(mid) or defaults[mid],
        }
        for mid, slot in slots.items()
    }


def member_labels(group: dict, names_by_id: dict[str, str]) -> dict[str, dict]:
    """Member id -> {label, full_name, color_hex} for exports."""
    return {
        mid: {"label": m["label"], "full_name": m["name"], "color_hex": m["color_hex"]}
        for mid, m in describe_members(group, names_by_id).items()
    }


def member_color_slots(group: dict) -> dict[str, int]:
    """Member id -> dot colour slot (1..MEMBER_COLOR_SLOTS): the stored choice,
    else slots in member order."""
    stored = group.get("member_colors") or {}
    return {
        mid: stored.get(mid) or (i % MEMBER_COLOR_SLOTS) + 1
        for i, mid in enumerate(group.get("member_ids") or [])
    }


MAX_LABEL_LENGTH = 40


def update_members(
    group_id: str,
    member_colors: dict[str, int] | None = None,
    member_labels: dict[str, str] | None = None,
) -> dict | None:
    """Merge per-member colour slots and/or custom labels into a group. An empty
    label clears that member's override (back to the derived default). Returns the
    updated group, or None if it doesn't exist."""
    with lg_store.lock(_NS, group_id):  # a label save and a colour pick can land together
        # Fresh read, not the 30s cache: another replica may have just changed this group
        group = next((g for g in list_groups(force_refresh=True) if g.get("id") == group_id), None)
        if group is None:
            return None
        colors = {**(group.get("member_colors") or {}), **(member_colors or {})}
        labels = {**(group.get("member_label_overrides") or {}), **(member_labels or {})}
        labels = {mid: label for mid, label in labels.items() if label}
        group = {**group, "member_colors": colors, "member_label_overrides": labels}
        lg_store.put(_NS, group_id, group)
        _invalidate()
    return group


def delete_group(group_id: str) -> None:
    lg_store.delete(_NS, group_id)
    _invalidate()


def _invalidate() -> None:
    global _cache
    with _lock:
        _cache = None
