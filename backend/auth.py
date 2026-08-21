"""Authentication: OTP generation/validation and session management.

All state is in-memory — restarting the server invalidates all sessions
and pending OTPs. Sessions are intentionally short-lived (8 hours).
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

OTP_TTL = 900        # 15 minutes
SESSION_TTL = 28800  # 8 hours
_MAX_OTP_REQUESTS = 3  # per email per OTP_TTL window
STATE_TTL = 600      # 10 minutes


@dataclass
class _OTPEntry:
    code: str
    expires_at: float


@dataclass
class _SessionEntry:
    email: str
    expires_at: float


@dataclass
class _RateEntry:
    count: int
    window_start: float


_otps: dict[str, _OTPEntry] = {}
_sessions: dict[str, _SessionEntry] = {}
_rate: dict[str, _RateEntry] = {}
_states: dict[str, float] = {}  # state_token -> expires_at


def is_rate_limited(email: str) -> bool:
    """Return True if this email has exceeded the OTP request rate limit."""
    now = time.monotonic()
    entry = _rate.get(email)
    if entry is None or now - entry.window_start > OTP_TTL:
        _rate[email] = _RateEntry(count=1, window_start=now)
        return False
    if entry.count >= _MAX_OTP_REQUESTS:
        return True
    entry.count += 1
    return False


def generate_otp(email: str) -> str:
    """Generate and store a 6-digit OTP for the given email."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    _otps[email] = _OTPEntry(code=code, expires_at=time.monotonic() + OTP_TTL)
    return code


def verify_otp(email: str, code: str) -> bool:
    """Return True if the code matches and is not expired. Consumes the OTP."""
    entry = _otps.get(email)
    if not entry:
        return False
    if time.monotonic() > entry.expires_at:
        del _otps[email]
        return False
    if entry.code != code:
        return False
    del _otps[email]  # single use
    return True


def create_session(email: str) -> str:
    """Create and store a new session token for the given email."""
    token = secrets.token_hex(32)
    _sessions[token] = _SessionEntry(
        email=email,
        expires_at=time.monotonic() + SESSION_TTL,
    )
    return token


def validate_session(token: str) -> str | None:
    """Return the email for a valid session token, or None if invalid/expired."""
    entry = _sessions.get(token)
    if not entry:
        return None
    if time.monotonic() > entry.expires_at:
        del _sessions[token]
        return None
    return entry.email


def revoke_session(token: str) -> None:
    """Delete a session token, effectively signing the user out."""
    _sessions.pop(token, None)


def generate_state() -> str:
    """Generate a single-use CSRF state token for OAuth flows."""
    token = secrets.token_urlsafe(32)
    _states[token] = time.monotonic() + STATE_TTL
    return token


def consume_state(token: str) -> bool:
    """Return True and invalidate the token if valid and unexpired."""
    exp = _states.pop(token, None)
    return exp is not None and time.monotonic() < exp


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
