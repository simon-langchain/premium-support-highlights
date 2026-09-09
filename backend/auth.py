"""Authentication: OTP generation/validation and session management.

Sessions, OTPs, rate-limit counters, and OAuth CSRF state tokens are stored
in the shared LangGraph Platform Store (Postgres-backed) rather than
in-process memory. The API server can run multiple replicas in production
(autoscaled), and each replica has its own process memory -- an in-memory
store would let a session created on one replica appear "invalid" on
another, which is exactly the bug this module was rewritten to fix. See
_lg_client().

Expiry is enforced application-side (via a stored expires_at timestamp) on
every read, independent of the Store's own optional ttl parameter -- so
correctness doesn't depend on TTL support being enabled on the underlying
store backend. The ttl we still pass is a best-effort janitor to keep
Postgres from accumulating stale rows forever.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

import httpx

OTP_TTL = 900        # 15 minutes
SESSION_TTL = 28800  # 8 hours
_MAX_OTP_REQUESTS = 3  # per email per OTP_TTL window
STATE_TTL = 600      # 10 minutes

_OTP_TTL_MIN = OTP_TTL // 60
_SESSION_TTL_MIN = SESSION_TTL // 60
_STATE_TTL_MIN = STATE_TTL // 60

_NS_OTP = ("psh_auth", "otp")
_NS_SESSION = ("psh_auth", "session")
_NS_RATE = ("psh_auth", "rate")
_NS_STATE = ("psh_auth", "state")

_client = None


def _lg_client():
    """Return a shared LangGraph Platform SDK client for Store access.

    Defaults to http://localhost:8000 -- correct both for local dev
    (`langgraph dev --port 8000`) and inside an LSD deployment, where the
    platform's own API (including the Store) is served on the same port in
    the same container. Cached at module level since this is called on
    every authenticated request.
    """
    global _client
    if _client is None:
        from langgraph_sdk import get_client
        url = os.environ.get("LANGGRAPH_API_URL") or "http://localhost:8000"
        _client = get_client(url=url)
    return _client


async def _store_get(namespace: tuple[str, ...], key: str) -> dict | None:
    """Fetch a Store item's value, or None if missing/expired-and-swept."""
    try:
        item = await _lg_client().store.get_item(namespace, key)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    return item.get("value") if item else None


async def _store_put(namespace: tuple[str, ...], key: str, value: dict, ttl_minutes: int) -> None:
    await _lg_client().store.put_item(namespace, key, value, index=False, ttl=ttl_minutes)


async def _store_delete(namespace: tuple[str, ...], key: str) -> None:
    try:
        await _lg_client().store.delete_item(namespace, key)
    except httpx.HTTPStatusError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise


async def is_rate_limited(email: str) -> bool:
    """Return True if this email has exceeded the OTP request rate limit."""
    now = time.time()
    entry = await _store_get(_NS_RATE, email)
    if entry is None or now - entry.get("window_start", 0) > OTP_TTL:
        await _store_put(_NS_RATE, email, {"count": 1, "window_start": now}, _OTP_TTL_MIN)
        return False
    if entry.get("count", 0) >= _MAX_OTP_REQUESTS:
        return True
    await _store_put(
        _NS_RATE, email,
        {"count": entry["count"] + 1, "window_start": entry["window_start"]},
        _OTP_TTL_MIN,
    )
    return False


async def generate_otp(email: str) -> str:
    """Generate and store a 6-digit OTP for the given email."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    await _store_put(_NS_OTP, email, {"code": code, "expires_at": time.time() + OTP_TTL}, _OTP_TTL_MIN)
    return code


async def verify_otp(email: str, code: str) -> bool:
    """Return True if the code matches and is not expired. Consumes the OTP."""
    entry = await _store_get(_NS_OTP, email)
    if not entry:
        return False
    if time.time() > entry.get("expires_at", 0):
        await _store_delete(_NS_OTP, email)
        return False
    if entry.get("code") != code:
        return False
    await _store_delete(_NS_OTP, email)  # single use
    return True


async def create_session(email: str) -> str:
    """Create and store a new session token for the given email."""
    token = secrets.token_hex(32)
    await _store_put(
        _NS_SESSION, token, {"email": email, "expires_at": time.time() + SESSION_TTL}, _SESSION_TTL_MIN,
    )
    return token


async def validate_session(token: str) -> str | None:
    """Return the email for a valid session token, or None if invalid/expired."""
    entry = await _store_get(_NS_SESSION, token)
    if not entry:
        return None
    if time.time() > entry.get("expires_at", 0):
        await _store_delete(_NS_SESSION, token)
        return None
    return entry.get("email")


async def revoke_session(token: str) -> None:
    """Delete a session token, effectively signing the user out."""
    await _store_delete(_NS_SESSION, token)


async def generate_state() -> str:
    """Generate a single-use CSRF state token for OAuth flows."""
    token = secrets.token_urlsafe(32)
    await _store_put(_NS_STATE, token, {"expires_at": time.time() + STATE_TTL}, _STATE_TTL_MIN)
    return token


async def consume_state(token: str) -> bool:
    """Return True and invalidate the token if valid and unexpired."""
    entry = await _store_get(_NS_STATE, token)
    if entry is None:
        return False
    await _store_delete(_NS_STATE, token)
    return time.time() < entry.get("expires_at", 0)


# ---------------------------------------------------------------------------
# Internal team-dashboard admin list
#
# Independent of Pylon "Support" team membership — an admin still needs to
# pass the live Pylon Support-team check on every request (see
# require_admin/require_support_team in main.py) to reach any admin route,
# so removal from Pylon auto-revokes admin access without needing this list
# to be kept in sync separately.
# ---------------------------------------------------------------------------

_ADMIN_FILE = Path(__file__).parent / ".cache" / "team_dashboard_admins.json"
_DEFAULT_ADMINS = ["simon@langchain.dev", "sri.puthucode@langchain.dev", "nithin@langchain.dev"]
_admin_lock = threading.Lock()


def _load_admins() -> list[str]:
    if not _ADMIN_FILE.exists():
        return list(_DEFAULT_ADMINS)
    try:
        raw = json.loads(_ADMIN_FILE.read_text())
        admins = raw.get("admins")
        if isinstance(admins, list) and admins:
            return admins
    except (json.JSONDecodeError, OSError, KeyError):
        pass
    return list(_DEFAULT_ADMINS)


def _save_admins(admins: list[str]) -> None:
    _ADMIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ADMIN_FILE.write_text(json.dumps({"admins": admins}, indent=2))


def get_admin_emails() -> set[str]:
    """Return the current set of team-dashboard admin emails.

    Seeds the file with the default admins on first read if it doesn't exist.
    """
    with _admin_lock:
        admins = _load_admins()
        if not _ADMIN_FILE.exists():
            _save_admins(admins)
        return {e.strip().lower() for e in admins}


def add_admin(email: str) -> None:
    """Add an email to the team-dashboard admin list. Idempotent."""
    email = email.strip().lower()
    with _admin_lock:
        admins = _load_admins()
        normalized = {e.strip().lower() for e in admins}
        if email not in normalized:
            admins.append(email)
            _save_admins(admins)


def remove_admin(email: str) -> None:
    """Remove an email from the team-dashboard admin list.

    Raises ValueError if this would remove the last remaining admin.
    """
    email = email.strip().lower()
    with _admin_lock:
        admins = _load_admins()
        remaining = [e for e in admins if e.strip().lower() != email]
        if not remaining:
            raise ValueError("Cannot remove the last remaining admin")
        _save_admins(remaining)
