"""Authentication: OTP generation/validation and session management.

All state is in-memory — restarting the server invalidates all sessions
and pending OTPs. Sessions are intentionally short-lived (8 hours).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

OTP_TTL = 900        # 15 minutes
SESSION_TTL = 28800  # 8 hours
_MAX_OTP_REQUESTS = 3  # per email per OTP_TTL window


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
