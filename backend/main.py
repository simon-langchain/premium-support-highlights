"""FastAPI backend for Premium Support Highlights dashboard.

API surface:
  GET  /api/accounts                              -sorted list of premium accounts
  GET  /api/accounts/{id}/data                    -metrics + open issues for an account
  GET  /api/accounts/{id}/cached-ticket-summaries -per-ticket AI summaries from disk cache
  POST /api/accounts/{id}/summary                 -stream an AI-generated account summary

The /summary endpoint uses SSE (server-sent events) with keepalive pings while Claude
generates. The Next.js route handler at frontend/.../summary/route.ts converts this
stream to plain JSON before it reaches the browser.

Caching strategy (all in-memory, 5-minute TTL):
  raw:{id}:{period}     -raw Pylon API responses, shared by /data and /summary so
                          they never make duplicate API calls within the same window
  payload:{id}:{period} -computed metrics payload, served directly by /data
  open:{id}             -current open issues for the polled /cached-ticket-summaries
"""

import base64
import hashlib
import hmac
import logging
import os
import re
import time
import asyncio
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import zoneinfo
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, urlencode

import httpx

from dotenv import load_dotenv

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_root, ".env"))

_log = logging.getLogger(__name__)

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

import auth as auth_mod

import pylon_client
import slack_client
import metrics as metrics_mod
import audit
import cache as cache_mod
from summary_agent import generate_account_summary, make_summarise_tickets_tool, generate_qbr_insights
from report import generate_report_html
from ticket_summarizer import parse_ticket_output

app = FastAPI(title="Premium Support Highlights API", version="0.1.0")

# ALLOWED_ORIGINS: comma-separated list of allowed origins.
# Set this in the deployment env to your Vercel frontend URL.
# Defaults to localhost for local development.
_allowed_origins = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory cache (5-minute TTL) and in-flight deduplication
# ---------------------------------------------------------------------------

_CACHE_TTL = 300
_data_cache: dict[str, tuple[float, Any]] = {}

# Per-key asyncio locks: prevent concurrent requests for the same uncached
# account from making duplicate Pylon API calls (which causes race conditions).
_fetch_locks: dict[str, asyncio.Lock] = {}


def _cache_get(key: str) -> Any:
    entry = _data_cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _data_cache[key]
        return None
    return data


def _cache_set(key: str, data: object) -> None:
    _data_cache[key] = (time.monotonic(), data)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPEN_STATES = ["new", "waiting_on_you", "on_hold", "waiting_on_customer"]
VALID_PERIODS = {"7d", "1m", "3m", "6m", "1y"}
ALL_SECTIONS = frozenset({"key_metrics", "ticket_trend", "breakdowns", "account_summary", "open_issues"})


class SummaryRequest(BaseModel):
    account_name: str
    model: str = "claude-sonnet-4-6"
    period: str = "6m"
    force: bool = False


class EmailReportRequest(BaseModel):
    email: str
    account_name: str
    period: str = "6m"
    sort_by: str = "priority"
    sort_order: str = "asc"
    sections: list[str] | None = None  # None = all sections


class SlackReportRequest(BaseModel):
    account_name: str
    period: str = "6m"
    channel_id: str | None = None
    sections: list[str] | None = None  # None = all sections


class GoogleCallbackBody(BaseModel):
    code: str
    state: str


class AuthRequestBody(BaseModel):
    email: str


class AuthVerifyBody(BaseModel):
    email: str
    code: str


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def _get_session_token(request: Request) -> str | None:
    return request.cookies.get("psh_session")


# LOCAL_TEST_MODE bypasses login. Ignored if ALLOWED_ORIGINS is set (production).
_LOCAL_TEST_MODE = (
    os.environ.get("LOCAL_TEST_MODE", "").lower() in ("1", "true", "yes")
    and not os.environ.get("ALLOWED_ORIGINS")
)


async def require_auth(request: Request) -> str:
    if _LOCAL_TEST_MODE:
        return "dev@langchain.dev"
    token = _get_session_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    email = auth_mod.validate_session(token)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return email


# ---------------------------------------------------------------------------
# Google OAuth constants
# ---------------------------------------------------------------------------

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _google_redirect_uri() -> str:
    base = os.environ.get("DASHBOARD_URL", "http://localhost:3000").rstrip("/")
    return f"{base}/auth/google/callback"


# ---------------------------------------------------------------------------
# OTP email helper
# ---------------------------------------------------------------------------

def _send_otp_email(to_email: str, code: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)

    if not smtp_host or not smtp_user or not smtp_password:
        raise RuntimeError("SMTP not configured")

    html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;background:#f8f7ff;margin:0;padding:0;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f7ff;">
  <tr><td align="center">
    <table width="480" cellpadding="0" cellspacing="0"
           style="background:#fff;margin:40px auto;border-radius:8px;overflow:hidden;">
      <tr><td style="padding:32px 40px;">
        <p style="font-size:13px;font-weight:600;letter-spacing:0.08em;
                  text-transform:uppercase;color:#006ddd;margin:0 0 8px;">
          Support Highlights
        </p>
        <h1 style="font-size:22px;font-weight:700;color:#111827;margin:0 0 24px;">
          Your login code
        </h1>
        <p style="font-size:15px;color:#374151;margin:0 0 24px;">
          Enter this code to sign in. It expires in 15 minutes.
        </p>
        <div style="background:#f3f4f6;border-radius:8px;padding:20px;
                    text-align:center;letter-spacing:0.3em;
                    font-size:32px;font-weight:700;color:#111827;margin:0 0 24px;">
          {code}
        </div>
        <p style="font-size:13px;color:#9ca3af;margin:0;">
          If you didn't request this, you can ignore this email.
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Support Highlights login code"
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg["X-PM-Message-Stream"] = "support-highlights"
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_from, [to_email], msg.as_string())


# ---------------------------------------------------------------------------
# Helpers: data fetching and payload computation
# ---------------------------------------------------------------------------

def _compute_csat(responses: list[dict]) -> float | None:
    """Average the score answers from CSAT survey responses (1–5 scale)."""
    scores = []
    for response in responses:
        for answer in (response.get("answers") or []):
            if answer.get("question_type") == "score":
                try:
                    score = float(answer["value"])
                    if 1.0 <= score <= 5.0:
                        scores.append(score)
                except (TypeError, ValueError):
                    pass
    return sum(scores) / len(scores) if scores else None


def _format_field_value(slug: str, field_labels: dict[str, dict[str, str]], field: str) -> str:
    """Return a human-friendly label for a custom field option slug.

    Falls back to generic title-casing when the API didn't return a label.
    """
    if not slug:
        return ""
    label = field_labels.get(field, {}).get(slug)
    if label:
        return label
    for prefix in ("lc_", "ls_", "lsd_", "admin_", "other_"):
        if slug.startswith(prefix):
            slug = slug[len(prefix):]
            break
    return slug.replace("_", " ").replace("-", " ").title()


def _normalise_issue(issue: dict, field_labels: dict, account_id: str = "") -> dict:
    """Convert a raw Pylon issue into the normalised shape used by the frontend.

    Pylon stores category/disposition in nested custom_fields dicts with opaque slugs
    (e.g. "lc_infrastructure"). field_labels maps those slugs to human-readable labels.
    Category fields become the `tags` list shown on each ticket card.
    """
    custom_fields = issue.get("custom_fields") or {}
    tags = []
    disposition = ""
    if isinstance(custom_fields, dict):
        for field_slug in ("category", "category_component"):
            val = (custom_fields.get(field_slug) or {}).get("value", "")
            label = _format_field_value(val, field_labels, field_slug)
            if label:
                tags.append(label)
        disp_val = (custom_fields.get("disposition") or {}).get("value", "")
        disposition = _format_field_value(disp_val, field_labels, "disposition")
    external_issues = [
        {
            "source": ei.get("source", ""),
            "external_id": ei.get("external_id", ""),
            "link": ei.get("link", ""),
        }
        for ei in (issue.get("external_issues") or [])
        if ei.get("link")
    ]
    slack_url = None
    slack_data = issue.get("slack") or {}
    if slack_data.get("channel_id") and slack_data.get("message_ts"):
        ts_no_dot = slack_data["message_ts"].replace(".", "")
        slack_url = f"https://slack.com/archives/{slack_data['channel_id']}/p{ts_no_dot}"

    issue_id = issue.get("id", "")
    portal_url = (
        f"https://app.usepylon.com/accounts/{account_id}/customer-portal"
        f"?tab=issues&conversationID={issue_id}&durationMs=31536000000"
        if issue_id and account_id else None
    )

    return {
        "number": issue.get("number"),
        "title": issue.get("title", ""),
        "state": issue.get("state", ""),
        "priority": metrics_mod.get_priority(issue),
        "created_at": issue.get("created_at", ""),
        "tags": tags,
        "disposition": disposition,
        "external_issues": external_issues,
        "slack_url": slack_url,
        "portal_url": portal_url,
    }


async def _fetch_raw_data(
    account_id: str, period: str
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Fetch raw Pylon data for an account, cached for 5 minutes.

    Returns (field_labels, open_issues, period_issues, csat_responses).
    Shared by the /data and /summary routes so they never duplicate API calls.

    Uses a per-key asyncio lock so that concurrent requests for the same
    uncached account don't make duplicate Pylon API calls (which causes 500s).
    """
    cache_key = f"raw:{account_id}:{period}"

    # Fast path: already cached
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached["field_labels"], cached["open_issues"], cached["period_issues"], cached["csat_responses"]

    # Ensure a lock exists for this key (safe: event loop is single-threaded)
    if cache_key not in _fetch_locks:
        _fetch_locks[cache_key] = asyncio.Lock()

    async with _fetch_locks[cache_key]:
        # Re-check after acquiring: another coroutine may have populated the cache
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached["field_labels"], cached["open_issues"], cached["period_issues"], cached["csat_responses"]

        created_after, created_before = pylon_client.make_date_range(period)
        try:
            field_labels, open_issues, period_issues, csat_responses = await asyncio.gather(
                asyncio.to_thread(pylon_client.get_issue_field_labels),
                asyncio.to_thread(pylon_client.search_issues_for_account, account_id, OPEN_STATES),
                asyncio.to_thread(
                    pylon_client.search_issues_for_account,
                    account_id,
                    None,
                    created_after,
                    created_before,
                ),
                asyncio.to_thread(pylon_client.get_csat_responses_for_account, account_id),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Pylon API error: {exc}") from exc

        _cache_set(cache_key, {
            "field_labels": field_labels,
            "open_issues": open_issues,
            "period_issues": period_issues,
            "csat_responses": csat_responses,
        })
        return field_labels, open_issues, period_issues, csat_responses


def _build_payload(
    field_labels: dict,
    open_issues: list[dict],
    period_issues: list[dict],
    csat_responses: list[dict],
    period: str,
    account_id: str = "",
) -> dict:
    """Compute the full API payload from raw Pylon data."""
    disposition_bd_raw = metrics_mod.get_disposition_breakdown(open_issues)
    return {
        "open_issues": [_normalise_issue(i, field_labels, account_id) for i in open_issues],
        "monthly_metrics": metrics_mod.compute_period_metrics(period_issues, period),
        "avg_response_time": metrics_mod.compute_avg_response_time(period_issues),
        "csat": _compute_csat(csat_responses),
        "priority_breakdown": metrics_mod.get_priority_breakdown(open_issues),
        "state_breakdown": metrics_mod.get_state_breakdown(open_issues),
        "disposition_breakdown": {
            _format_field_value(slug, field_labels, "disposition"): count
            for slug, count in disposition_bd_raw.items()
            if slug != "unknown"
        },
    }


# ---------------------------------------------------------------------------
# Summary freshness helper
# ---------------------------------------------------------------------------

async def _get_or_regenerate_account_summary(
    account_id: str,
    account_name: str,
    period: str,
    payload: dict,
    open_issues: list[dict],
    model: str = "claude-sonnet-4-6",
) -> str | None:
    """Return a fresh account summary, regenerating if missing or stale.

    Also ensures ticket summaries are fresh -stale ones are regenerated as
    part of the account summary pipeline (the agent calls summarise_tickets).
    """
    summary = await asyncio.to_thread(cache_mod.get_account_summary, account_id, period)
    if summary:
        return summary

    # Regenerate -force=True ensures stale ticket summaries are also refreshed
    summarise_tickets = make_summarise_tickets_tool(open_issues, force=True, account_name=account_name)
    try:
        summary = await generate_account_summary(
            account_name=account_name,
            open_tickets=payload["open_issues"],
            monthly_metrics=payload["monthly_metrics"],
            avg_response_time=payload["avg_response_time"],
            csat=payload["csat"],
            priority_breakdown=payload["priority_breakdown"],
            state_breakdown=payload["state_breakdown"],
            disposition_breakdown=payload["disposition_breakdown"],
            model=model,
            period=period,
            tools=[summarise_tickets],
        )
        await asyncio.to_thread(cache_mod.set_account_summary, account_id, period, summary)
    except Exception:
        summary = None
    return summary


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/auth/google/start")
async def auth_google_start():
    """Return a Google OAuth URL. The frontend redirects the user there."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")
    state = auth_mod.generate_state()
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": _google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email",
        "hd": "langchain.dev",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    return {"url": f"{_GOOGLE_AUTH_URL}?{params}"}


@app.post("/api/auth/google/callback")
async def auth_google_callback(body: GoogleCallbackBody):
    """Exchange a Google auth code for a session cookie."""
    if not auth_mod.consume_state(body.state):
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(_GOOGLE_TOKEN_URL, data={
            "code": body.code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": _google_redirect_uri(),
            "grant_type": "authorization_code",
        })

    if not token_resp.is_success:
        raise HTTPException(status_code=401, detail="Failed to exchange code with Google")

    token_data = token_resp.json()
    id_token = token_data.get("id_token", "")
    if not id_token:
        raise HTTPException(status_code=401, detail="No ID token in Google response")

    # Decode JWT payload — token received directly from Google over HTTPS, no sig verify needed
    try:
        payload_b64 = id_token.split(".")[1]
        padding = (4 - len(payload_b64) % 4) % 4
        claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * padding))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Failed to parse ID token") from exc

    email = (claims.get("email") or "").lower().strip()
    hd = claims.get("hd", "")
    email_verified = claims.get("email_verified", False)

    # Belt-and-suspenders: validate domain even though hd= restricts at Google level
    if not email_verified or not email.endswith("@langchain.dev") or hd != "langchain.dev":
        raise HTTPException(status_code=403, detail="Access restricted to @langchain.dev accounts")

    try:
        members = await asyncio.to_thread(pylon_client.get_team_members)
        member_emails = {(m.get("email") or "").lower() for m in members}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to verify membership: {exc}") from exc

    if email not in member_emails:
        raise HTTPException(status_code=403, detail="Not an active Pylon team member")

    token = auth_mod.create_session(email)
    is_https = bool(os.environ.get("ALLOWED_ORIGINS"))
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key="psh_session",
        value=token,
        max_age=28800,  # 8 hours
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/api/auth/request")
async def auth_request(body: AuthRequestBody):
    """Check eligibility and send a login OTP. Never reveals whether an email
    exists in Pylon — unauthorized addresses get the same non-error response."""
    email = body.email.lower().strip()

    if not email.endswith("@langchain.dev"):
        return {"status": "not_authorized"}

    if auth_mod.is_rate_limited(email):
        return {"status": "rate_limited"}

    try:
        members = await asyncio.to_thread(pylon_client.get_team_members)
        member_emails = {(m.get("email") or "").lower() for m in members}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to verify membership: {exc}") from exc

    if email not in member_emails:
        return {"status": "not_authorized"}

    code = auth_mod.generate_otp(email)
    try:
        await asyncio.to_thread(_send_otp_email, email, code)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to send email: {exc}") from exc

    return {"status": "sent"}


@app.post("/api/auth/verify")
async def auth_verify(body: AuthVerifyBody):
    """Validate a login OTP, create a session, and set the session cookie."""
    email = body.email.lower().strip()
    if not auth_mod.verify_otp(email, body.code.strip()):
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    token = auth_mod.create_session(email)
    is_https = bool(os.environ.get("ALLOWED_ORIGINS"))
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key="psh_session",
        value=token,
        max_age=28800,  # 8 hours
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """Revoke the current session and clear the session cookie."""
    token = _get_session_token(request)
    if token:
        auth_mod.revoke_session(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(key="psh_session", path="/")
    return response


@app.get("/api/tiers")
def get_tiers(_email: str = Depends(require_auth)):
    """Return sorted list of available Support Tier values from Pylon."""
    try:
        return pylon_client.get_available_tiers()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pylon API error: {exc}") from exc


@app.get("/api/accounts")
def get_accounts(tier: str = "Premium", _email: str = Depends(require_auth)):
    """Return sorted list of accounts for the given support tier [{id, name}, ...]."""
    try:
        accounts = pylon_client.get_accounts_by_tier(tier)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pylon API error: {exc}") from exc
    result = [{"id": a.get("id", ""), "name": a.get("name", "")} for a in accounts]
    result.sort(key=lambda a: a["name"].lower())
    return result


@app.get("/api/accounts/{account_id}/data")
async def get_account_data(
    account_id: str,
    account_name: str = Query(...),
    period: str = Query("6m"),
    _email: str = Depends(require_auth),
):
    """Fetch issues and compute all metrics for an account."""
    if period not in VALID_PERIODS:
        period = "6m"

    payload_key = f"payload:{account_id}:{period}"
    if (payload := _cache_get(payload_key)) is not None:
        return payload

    field_labels, open_issues, period_issues, csat_responses = await _fetch_raw_data(account_id, period)
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period, account_id)
    _cache_set(payload_key, payload)
    await asyncio.to_thread(audit.log, "account_loaded", {"account_id": account_id, "account_name": account_name})
    return payload


@app.get("/api/accounts/{account_id}/cached-ticket-summaries")
async def get_cached_ticket_summaries(account_id: str, _email: str = Depends(require_auth)):
    """Return cached per-ticket summaries keyed by ticket number.

    Uses the open-issues cache so the frequent frontend polling doesn't
    hit the Pylon API on every request.
    """
    open_key = f"open:{account_id}"
    open_issues = _cache_get(open_key)
    if open_issues is None:
        try:
            open_issues = await asyncio.to_thread(
                pylon_client.search_issues_for_account, account_id, OPEN_STATES
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Pylon API error: {exc}") from exc
        _cache_set(open_key, open_issues)

    def _read_summaries() -> dict[int, dict]:
        out: dict[int, dict] = {}
        for issue in open_issues:
            issue_id = issue.get("id", "")
            number = issue.get("number")
            if number is None:
                continue
            latest_msg_time = issue.get("latest_message_time") or issue.get("updated_at") or ""
            raw = cache_mod.get_ticket_summary(issue_id, latest_msg_time)
            if raw:
                s, ns = parse_ticket_output(raw)
                if s or ns:
                    out[number] = {"summary": s, "next_steps": ns}
        return out

    return await asyncio.to_thread(_read_summaries)


@app.post("/api/accounts/{account_id}/summary")
async def get_account_summary(account_id: str, body: SummaryRequest, _email: str = Depends(require_auth)):
    """Generate an AI account summary, streamed as SSE to keep the connection alive."""
    period = body.period if body.period in VALID_PERIODS else "6m"
    field_labels, open_issues, period_issues, csat_responses = await _fetch_raw_data(account_id, period)
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period, account_id)
    summarise_tickets = make_summarise_tickets_tool(open_issues, body.force, account_name=body.account_name)

    async def event_stream():
        # Run the agent as a background task and emit SSE keepalive pings every 3
        # seconds while it works (summaries can take 30-90 seconds). This keeps the
        # HTTP connection alive through proxies and load balancers. The Next.js route
        # handler buffers the stream and only returns to the browser once it sees the
        # final `result` or `error` event.
        task = asyncio.create_task(
            generate_account_summary(
                account_name=body.account_name,
                open_tickets=payload["open_issues"],
                monthly_metrics=payload["monthly_metrics"],
                avg_response_time=payload["avg_response_time"],
                csat=payload["csat"],
                priority_breakdown=payload["priority_breakdown"],
                state_breakdown=payload["state_breakdown"],
                disposition_breakdown=payload["disposition_breakdown"],
                model=body.model,
                period=period,
                tools=[summarise_tickets],
            )
        )
        try:
            while not task.done():
                yield "event: ping\ndata: {}\n\n"
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            task.cancel()
            return

        try:
            summary = await task
        except (Exception, asyncio.CancelledError) as exc:
            yield f"event: error\ndata: {json.dumps({'error': str(exc) or 'Summary generation failed'})}\n\n"
            return

        yield f"event: result\ndata: {json.dumps({'summary': summary})}\n\n"
        try:
            await asyncio.to_thread(cache_mod.set_account_summary, account_id, period, summary)
            audit.log(
                "summary_generated",
                {"account_id": account_id, "account_name": body.account_name, "model": body.model},
            )
        except Exception:
            pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/accounts/{account_id}/report", response_class=HTMLResponse)
async def get_account_report(
    account_id: str,
    account_name: str = Query(...),
    period: str = Query("6m"),
    sort_by: str = Query("priority"),
    sort_order: str = Query("asc"),
    sections: list[str] | None = Query(default=None),
    _email: str = Depends(require_auth),
):
    """Return a self-contained HTML report for an account.

    Includes cached ticket summaries and the latest cached AI account summary
    if one exists. Suitable for printing to PDF or sending as an HTML email.
    """
    if period not in VALID_PERIODS:
        period = "6m"

    field_labels, open_issues, period_issues, csat_responses = await _fetch_raw_data(
        account_id, period
    )
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period, account_id)

    # Collect cached per-ticket summaries (same logic as /cached-ticket-summaries)
    def _read_summaries() -> dict[int, dict]:
        out: dict[int, dict] = {}
        for issue in open_issues:
            issue_id = issue.get("id", "")
            number = issue.get("number")
            if number is None:
                continue
            latest_msg_time = issue.get("latest_message_time") or issue.get("updated_at") or ""
            raw = cache_mod.get_ticket_summary(issue_id, latest_msg_time)
            if raw:
                s, ns = parse_ticket_output(raw)
                if s or ns:
                    out[number] = {"summary": s, "next_steps": ns}
        return out

    ticket_summaries = await asyncio.to_thread(_read_summaries)
    account_summary = await _get_or_regenerate_account_summary(
        account_id, account_name, period, payload, open_issues
    )

    html = generate_report_html(
        account_name=account_name,
        period=period,
        payload=payload,
        ticket_summaries=ticket_summaries,
        account_summary=account_summary,
        sort_by=sort_by,
        sort_order=sort_order,
        banner_url=os.environ.get("REPORT_BANNER_URL") or None,
        sections=set(sections) if sections else None,
    )
    return HTMLResponse(content=html)


@app.post("/api/accounts/{account_id}/email-report")
async def email_account_report(account_id: str, body: EmailReportRequest, _email: str = Depends(require_auth)):
    """Generate a report and email it as HTML to the specified address.

    Requires SMTP_HOST, SMTP_PORT, SMTP_USER, and SMTP_PASSWORD env vars.
    SMTP_FROM defaults to SMTP_USER if not set.
    """
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)

    if not smtp_host or not smtp_user or not smtp_password:
        raise HTTPException(
            status_code=503,
            detail="Email is not configured. Set SMTP_HOST, SMTP_USER, and SMTP_PASSWORD.",
        )

    period = body.period if body.period in VALID_PERIODS else "6m"

    field_labels, open_issues, period_issues, csat_responses = await _fetch_raw_data(
        account_id, period
    )
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period, account_id)

    def _read_summaries() -> dict[int, dict]:
        out: dict[int, dict] = {}
        for issue in open_issues:
            issue_id = issue.get("id", "")
            number = issue.get("number")
            if number is None:
                continue
            latest_msg_time = issue.get("latest_message_time") or issue.get("updated_at") or ""
            raw = cache_mod.get_ticket_summary(issue_id, latest_msg_time)
            if raw:
                s, ns = parse_ticket_output(raw)
                if s or ns:
                    out[number] = {"summary": s, "next_steps": ns}
        return out

    ticket_summaries = await asyncio.to_thread(_read_summaries)
    account_summary = await _get_or_regenerate_account_summary(
        account_id, body.account_name, period, payload, open_issues
    )

    logo_url = os.environ.get("REPORT_LOGO_URL") or None
    banner_url = os.environ.get("REPORT_BANNER_URL") or None
    html = generate_report_html(
        account_name=body.account_name,
        period=period,
        payload=payload,
        ticket_summaries=ticket_summaries,
        account_summary=account_summary,
        sort_by=body.sort_by,
        sort_order=body.sort_order,
        logo_url=logo_url,
        is_email=True,
        banner_url=banner_url,
        sections=set(body.sections) if body.sections is not None else None,
    )

    subject = f"Support Highlights: {body.account_name}"

    def _send_email() -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = body.email
        msg["X-PM-Message-Stream"] = "support-highlights"
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [body.email], msg.as_string())

    try:
        await asyncio.to_thread(_send_email)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to send email: {exc}") from exc

    await asyncio.to_thread(
        audit.log,
        "report_emailed",
        {"account_id": account_id, "account_name": body.account_name, "to": body.email},
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Slack report helpers
# ---------------------------------------------------------------------------

_PERIOD_DISPLAY = {
    "7d": "7 Days", "1m": "1 Month", "3m": "3 Months", "6m": "6 Months", "1y": "1 Year",
}

_PRIORITY_LABELS = {
    "urgent": "Sev 1", "high": "Sev 2", "medium": "Sev 3", "low": "Sev 4", "none": "—",
}

# Emoji stand-ins for colour-coded badges
_PRIORITY_EMOJI = {
    "urgent": ":red_circle:",
    "high":   ":large_orange_circle:",
    "medium": ":large_yellow_circle:",
    "low":    ":white_circle:",
    "none":   ":white_circle:",
}

def _state_labels(account_name: str = "") -> dict:
    return {
        "new":                 "New",
        "waiting_on_you":      "Waiting on LangChain",
        "on_hold":             "On Hold",
        "waiting_on_customer": f"Waiting on {account_name}" if account_name else "Waiting on Customer",
    }

_STATE_EMOJI = {
    "new":                 ":large_green_circle:",
    "waiting_on_you":      ":large_blue_circle:",
    "on_hold":             ":large_purple_circle:",
    "waiting_on_customer": ":white_circle:",
}

def _fmt_csat(v: float) -> str:
    """Format a CSAT score without a trailing .0 when it's a whole number."""
    return str(int(v)) if v == int(v) else f"{v:.1f}"


# Attachment sidebar colours -one per priority level
_PRIORITY_COLORS = {
    "urgent": "#C0392B",  # red
    "high":   "#E67E22",  # orange
    "medium": "#F1C40F",  # yellow
    "low":    "#95A5A6",  # light grey
    "none":   "#95A5A6",
}


def _field(label: str, value: str) -> dict:
    return {"type": "mrkdwn", "text": f"*{label}*\n{value}"}


def _build_metrics_blocks(
    account_id: str,
    account_name: str,
    payload: dict,
    period: str,
    sections: set[str] | None = None,
) -> tuple[str, list[dict]]:
    """Compact metrics snapshot with 2-column field grid and action buttons."""
    secs = sections if sections is not None else ALL_SECTIONS
    period_label = _PERIOD_DISPLAY.get(period, period)
    open_count = len(payload["open_issues"])
    total_raised = sum(m["tickets_raised"] for m in payload["monthly_metrics"])
    total_closed = sum(m["closed_tickets"] for m in payload["monthly_metrics"])
    avg_rt: float | None = payload.get("avg_response_time")
    csat: float | None = payload.get("csat")

    # Priority breakdown as a compact string, e.g. "Sev 1: 2  Sev 2: 5  Sev 3: 3"
    priority_bd = payload.get("priority_breakdown", {})
    _p_order = ["urgent", "high", "medium", "low"]
    priority_parts = [
        f"{_PRIORITY_EMOJI.get(p, '')} {_PRIORITY_LABELS[p]}: *{priority_bd[p]}*"
        for p in _p_order if priority_bd.get(p, 0) > 0
    ]

    fields = [
        _field("Current Open Issues", str(open_count)),
        _field(f"Raised ({period_label})", str(total_raised)),
        _field(f"Closed ({period_label})", str(total_closed)),
        _field("Avg Response", f"{avg_rt:.1f} hrs" if avg_rt is not None else "—"),
    ]
    if csat is not None:
        fields.append(_field("CSAT", f"{_fmt_csat(csat)} / 5"))

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": account_name, "emoji": False},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"*{period_label}*  ·  {_today()}"}],
        },
    ]

    if "key_metrics" in secs:
        blocks += [
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": ":dart: *KEY METRICS*"}]},
            {"type": "section", "fields": fields},
        ]

    # Trend chart
    monthly = payload.get("monthly_metrics", [])
    if "ticket_trend" in secs and len(monthly) >= 2:
        is_daily = period in ("7d", "1m")
        label_w = 6 if is_daily else 3
        raised_vals = [m["tickets_raised"] for m in monthly]
        closed_vals = [m["closed_tickets"] for m in monthly]
        shared_max = max(max(raised_vals), max(closed_vals), 1)
        bar_width = 15
        rows = []
        for m in monthly:
            label = m["month"] if is_daily else m["month"][:3]
            raised = m["tickets_raised"]
            closed = m["closed_tickets"]
            r_len = round(raised / shared_max * bar_width)
            c_len = round(closed / shared_max * bar_width)
            r_bar = "█" * r_len + "░" * (bar_width - r_len)
            c_bar = "█" * c_len + "░" * (bar_width - c_len)
            rows.append(f"`{label:<{label_w}}  {r_bar} {raised:>2}  {c_bar} {closed:>2}`")
        gap = label_w + 2
        header_row = f"`{'':{gap}}{'Raised':^18}  {'Closed':^18}`"
        trend_label = ":calendar: *TICKET TREND*"
        blocks += [
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": trend_label}]},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join([header_row] + rows)}},
        ]

    if "breakdowns" in secs:
        state_bd = payload.get("state_breakdown", {})
        _s_order = ["new", "waiting_on_you", "on_hold", "waiting_on_customer"]
        sl = _state_labels(account_name)
        state_parts = [
            f"{_STATE_EMOJI.get(s, '')} {sl.get(s, s)}: *{state_bd[s]}*"
            for s in _s_order if state_bd.get(s, 0) > 0
        ]
        if priority_parts or state_parts:
            breakdown_fields = []
            if priority_parts:
                breakdown_fields.append(_field("Priority", "\n".join(priority_parts)))
            if state_parts:
                breakdown_fields.append(_field("State", "\n".join(state_parts)))
            blocks += [
                {"type": "divider"},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": ":mag: *BREAKDOWNS*"}]},
                {"type": "section", "fields": breakdown_fields},
            ]
    elif "breakdowns" not in secs:
        pass  # skip — but we still need state_parts cleared to avoid NameError below

    action_value = json.dumps({"account_id": account_id, "account_name": account_name, "period": period})
    action_buttons = []
    if "account_summary" in secs:
        action_buttons.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Account Summary", "emoji": True},
            "action_id": "psh_post_summary",
            "value": action_value,
        })
    if "open_issues" in secs:
        action_buttons.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Current Open Issues", "emoji": True},
            "action_id": "psh_post_issues",
            "value": action_value,
        })
    if action_buttons:
        has_other = any(k in secs for k in ("key_metrics", "ticket_trend", "breakdowns"))
        details_label = "MORE DETAILS" if has_other else "DETAILS"
        blocks += [
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f":open_book: *{details_label}*"}]},
            {"type": "actions", "elements": action_buttons},
        ]

    fallback = f"Support Highlights: {account_name} - {open_count} open issues, {total_raised} raised ({period_label})"
    return fallback, blocks


def _today() -> str:
    from datetime import datetime as _dt, timezone as _tz
    return _dt.now(_tz.utc).strftime("%-d %b %Y")


def _build_summary_blocks(
    account_name: str,
    account_summary: str,
    period: str,
    ticket_urls: dict[str, str] | None = None,
) -> tuple[str, list[dict]]:
    """AI-generated account summary, split into paragraphs to avoid the 3000-char limit."""
    period_label = _PERIOD_DISPLAY.get(period, period)

    # Convert standard Markdown bold/italic to Slack mrkdwn equivalents
    slack_summary = re.sub(r'\*\*(.+?)\*\*', r'*\1*', account_summary)  # **bold** → *bold*
    slack_summary = re.sub(r'__(.+?)__', r'_\1_', slack_summary)         # __italic__ → _italic_

    # Hyperlink ticket numbers to their Slack threads where available
    if ticket_urls:
        def _linkify(m: re.Match) -> str:
            url = ticket_urls.get(m.group(1))
            return f"<{url}|#{m.group(1)}>" if url else m.group(0)
        slack_summary = re.sub(r'#(\d+)', _linkify, slack_summary)

    # Split into paragraphs and post each as its own section (max 3000 chars each)
    paragraphs = [p.strip() for p in slack_summary.split("\n\n") if p.strip()]
    # Merge short paragraphs so we don't exceed Slack's 50-block limit
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        candidate = f"{current}\n\n{p}".strip() if current else p
        if len(candidate) > 2800:
            if current:
                chunks.append(current)
            current = p
        else:
            current = candidate
    if current:
        chunks.append(current)

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{account_name} - Account Summary", "emoji": False},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"*{period_label}*  ·  {_today()}"}],
        },
        {"type": "divider"},
    ]
    for chunk in chunks:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})

    fallback = f"Account summary for {account_name} ({period_label})"
    return fallback, blocks


_PAGE_SIZE = 5


def _build_issues_blocks(
    account_name: str,
    open_issues: list[dict],
    ticket_summaries: dict[int, dict],
    period: str,
    offset: int = 0,
    account_id: str = "",
) -> tuple[str, list[dict], list[dict]]:
    """Open issues breakdown sorted by priority.

    Returns (fallback_text, blocks, attachments).
    blocks — header message (rendered first by Slack).
    attachments — one legacy attachment per issue, each with a severity colour bar.
    """
    _PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
    sorted_issues = sorted(open_issues, key=lambda i: _PRIORITY_ORDER.get(i.get("priority", "none"), 4))
    total = len(open_issues)
    page = sorted_issues[offset:offset + _PAGE_SIZE]
    next_offset = offset + _PAGE_SIZE
    has_more = next_offset < total

    header_text = f"{account_name} - Open Issues ({total})"
    if offset > 0:
        header_text = f"{account_name} - Open Issues ({offset + 1}–{min(next_offset, total)} of {total})"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text, "emoji": False},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _today()}],
        },
    ]

    attachments: list[dict] = []

    for issue in page:
        number = issue.get("number", "")
        title = issue.get("title", "")
        priority = issue.get("priority", "none")
        state = issue.get("state", "")
        entry = ticket_summaries.get(number, {})
        ticket_summary = entry.get("summary", "")
        ticket_next_steps = entry.get("next_steps", "")

        priority_label = _PRIORITY_LABELS.get(priority, priority.title())
        state_label = _state_labels(account_name).get(state, state.replace("_", " ").title())
        color = _PRIORITY_COLORS.get(priority, _PRIORITY_COLORS["none"])

        s_emoji = _STATE_EMOJI.get(state, "")

        slack_link = issue.get("slack_url")
        ticket_ref = f"<{slack_link}|#{number}>" if slack_link else f"#{number}"
        title_line = f"*{ticket_ref}  {title}*"

        body_parts = [title_line]
        if ticket_summary:
            body_parts.append(ticket_summary)
        if ticket_next_steps:
            body_parts.append(f"*Next steps:* {ticket_next_steps}")
        full_body = "\n".join(body_parts)
        body = full_body[:1200] + ("…" if len(full_body) > 1200 else "")

        p_emoji = _PRIORITY_EMOJI.get(priority, "")
        disposition = issue.get("disposition") or ""
        if disposition:
            d_lower = disposition.lower()
            if "bug" in d_lower:
                d_prefix = ":bug: "
            elif "feature" in d_lower:
                d_prefix = ":hatching_chick: "
            else:
                d_prefix = ""
            disposition_str = f"  ·  {d_prefix}{disposition}"
        else:
            disposition_str = ""
        meta = f"{p_emoji} {priority_label}  ·  {s_emoji} {state_label}{disposition_str}"

        attachments.append({
            "color": color,
            "fallback": f"#{number} {title}",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": body}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": meta}]},
            ],
        })

    if has_more:
        remaining = total - next_offset
        btn_value = json.dumps({
            "account_id": account_id,
            "account_name": account_name,
            "period": period,
            "offset": next_offset,
        })
        attachments.append({
            "fallback": f"Show next {min(remaining, _PAGE_SIZE)} issues",
            "blocks": [
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": f"Show Next {min(remaining, _PAGE_SIZE)}", "emoji": False},
                            "action_id": "psh_post_issues_more",
                            "value": btn_value,
                        }
                    ],
                }
            ],
        })

    fallback = f"Open issues for {account_name}: {total} tickets"
    return fallback, blocks, attachments


@app.get("/api/accounts/{account_id}/slack-channel")
async def get_slack_channel(
    account_id: str,
    _email: str = Depends(require_auth),
):
    """Return the Slack channel name configured for this account."""
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()

    if not slack_token:
        return {"channel_name": None, "channel_id": None, "override": False, "available_channels": []}

    # Fetch available channels and resolve default in parallel
    override = os.environ.get("SLACK_OVERRIDE_CHANNEL", "").strip()

    async def _resolve_name(cid: str) -> str | None:
        return await asyncio.to_thread(slack_client.get_channel_name, slack_token, cid)

    # Fetch account info and available channels in parallel
    account = await asyncio.to_thread(pylon_client.get_account, account_id)
    available_channels = await asyncio.to_thread(slack_client.get_channels, slack_token)

    # Resolve the default channel (override env var takes precedence)
    if override:
        default_id = override
    elif account:
        info = pylon_client.get_slack_channel_info(account)
        default_id = info["channel_id"] if info else None
    else:
        default_id = None

    # Prepend all account-linked Slack channels (internal + external) so they always
    # appear in the picker regardless of the external-prefix filter applied by get_channels().
    if account and not override:
        account_channels = pylon_client.get_all_slack_channels(account)
        seen_ids = {c["id"] for c in available_channels}
        for ch in reversed(account_channels):
            if ch["id"] not in seen_ids:
                # Name may be just the raw channel_id from Pylon — resolve the real name
                real_name = await _resolve_name(ch["id"])
                available_channels = [{"id": ch["id"], "name": real_name or ch["name"]}] + available_channels
                seen_ids.add(ch["id"])

    # Ensure the default channel is first
    if default_id:
        if not any(c["id"] == default_id for c in available_channels):
            default_name = await _resolve_name(default_id)
            available_channels = [{"id": default_id, "name": default_name or default_id}] + available_channels
        else:
            available_channels = [c for c in available_channels if c["id"] == default_id] + \
                                  [c for c in available_channels if c["id"] != default_id]

    default_channel = next((c for c in available_channels if c["id"] == default_id), None)
    default_name = default_channel["name"] if default_channel else None

    return {
        "channel_id": default_id,
        "channel_name": default_name,
        "override": bool(override),
        "available_channels": available_channels,
    }


@app.post("/api/accounts/{account_id}/slack-report")
async def post_slack_report(
    account_id: str,
    body: SlackReportRequest,
    _email: str = Depends(require_auth),
):
    """Post a support highlights summary to the account's Slack channel.

    Requires SLACK_BOT_TOKEN. If SLACK_OVERRIDE_CHANNEL is set, all messages are
    redirected there regardless of the account's real channel (use during testing).
    """
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not slack_token:
        raise HTTPException(
            status_code=503,
            detail="Slack is not configured. Set SLACK_BOT_TOKEN.",
        )

    period = body.period if body.period in VALID_PERIODS else "6m"

    # Resolve target channel: env override → request body override → account default
    override_channel = os.environ.get("SLACK_OVERRIDE_CHANNEL", "").strip()
    if override_channel:
        channel_id = override_channel
    elif body.channel_id:
        channel_id = body.channel_id
    else:
        account = await asyncio.to_thread(pylon_client.get_account, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        channel_id = pylon_client.get_slack_channel_id(account)
        if not channel_id:
            raise HTTPException(
                status_code=422,
                detail="No Slack channel configured for this account in Pylon",
            )

    field_labels, open_issues, period_issues, csat_responses = await _fetch_raw_data(
        account_id, period
    )
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period, account_id)
    fallback_text, blocks = _build_metrics_blocks(
        account_id, body.account_name, payload, period,
        sections=set(body.sections) if body.sections is not None else None,
    )

    try:
        await asyncio.to_thread(
            slack_client.post_message, slack_token, channel_id, fallback_text, blocks
        )
    except Exception as exc:
        msg = str(exc)
        if "channel_not_found" in msg:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Slack channel '{channel_id}' not found. "
                    "If this is a DM, open a conversation with the bot in Slack first "
                    "(search for it and send any message), then try again."
                ),
            ) from exc
        if "not_in_channel" in msg:
            channel_name = slack_client.get_channel_name(slack_token, channel_id)
            bot_name = slack_client.get_bot_name(slack_token) or "lc-support-highlights"
            channel_label = f"#{channel_name}" if channel_name else f"'{channel_id}'"
            invite_cmd = f"/invite @{bot_name}"
            raise HTTPException(
                status_code=422,
                detail=(
                    f"The bot is not a member of channel {channel_label}. "
                    f"Invite it with {invite_cmd} in that channel, then try again."
                ),
            ) from exc
        raise HTTPException(status_code=502, detail=f"Slack error: {msg}") from exc

    await asyncio.to_thread(
        audit.log,
        "slack_report_sent",
        {
            "account_id": account_id,
            "account_name": body.account_name,
            "channel_id": channel_id,
            "override": bool(override_channel),
        },
    )

    # Pre-warm summaries in the background so they're ready if buttons are clicked
    asyncio.create_task(_get_or_regenerate_account_summary(
        account_id, body.account_name, period, payload, open_issues
    ))

    return {"ok": True}


# ---------------------------------------------------------------------------
# Slack interactive callbacks
# ---------------------------------------------------------------------------

def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify a Slack request using HMAC-SHA256 and SLACK_SIGNING_SECRET.

    Returns True if the signature is valid, or if SLACK_SIGNING_SECRET is not
    configured (allows local testing without a public URL).
    """
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "").strip()
    if not signing_secret:
        return True  # Skip verification when not configured (local dev)
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False  # Replay attack guard: reject if older than 5 minutes
    except (ValueError, TypeError):
        return False
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    computed = "v0=" + hmac.new(signing_secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


async def _handle_slack_action(
    action_id: str,
    account_id: str,
    account_name: str,
    period: str,
    channel_id: str,
    thread_ts: str | None = None,
    **kwargs,
) -> None:
    """Post a follow-up message in response to a Slack button click."""
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not slack_token or not channel_id:
        return
    attachments: list[dict] | None = None
    try:
        if action_id == "psh_post_summary":
            field_labels, open_issues, period_issues, csat_responses = await _fetch_raw_data(account_id, period)
            payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period, account_id)
            account_summary = await _get_or_regenerate_account_summary(
                account_id, account_name, period, payload, open_issues
            )
            if not account_summary:
                await asyncio.to_thread(
                    slack_client.post_message,
                    slack_token,
                    channel_id,
                    "Summary generation failed",
                    [{"type": "section", "text": {"type": "mrkdwn", "text": ":warning: Summary generation failed. Please try again."}}],
                    thread_ts,
                )
                return
            ticket_urls = {
                str(i["number"]): i["slack_url"]
                for i in payload["open_issues"]
                if i.get("number") is not None and i.get("slack_url")
            }
            fallback_text, blocks = _build_summary_blocks(account_name, account_summary, period, ticket_urls=ticket_urls)

        elif action_id in ("psh_post_issues", "psh_post_issues_more"):
            offset = kwargs.get("offset", 0) if action_id == "psh_post_issues_more" else 0
            field_labels, open_issues, period_issues, csat_responses = await _fetch_raw_data(account_id, period)
            payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period, account_id)

            # For the first page, regenerate stale/missing summaries.
            # Subsequent pages skip regeneration -summaries were already warmed on first click.
            if action_id == "psh_post_issues":
                summarise_tickets = make_summarise_tickets_tool(open_issues, force=False, account_name=account_name)
                await summarise_tickets.ainvoke({})

            def _read_ticket_summaries() -> dict[int, dict]:
                out: dict[int, dict] = {}
                for issue in open_issues:
                    number = issue.get("number")
                    if number is None:
                        continue
                    latest = issue.get("latest_message_time") or issue.get("updated_at") or ""
                    raw = cache_mod.get_ticket_summary(issue.get("id", ""), latest)
                    if raw:
                        s, ns = parse_ticket_output(raw)
                        if s or ns:
                            out[number] = {"summary": s, "next_steps": ns}
                return out

            ticket_summaries = await asyncio.to_thread(_read_ticket_summaries)
            fallback_text, blocks, attachments = _build_issues_blocks(
                account_name, payload["open_issues"], ticket_summaries, period,
                offset=offset, account_id=account_id,
            )
        else:
            return

        await asyncio.to_thread(
            slack_client.post_message, slack_token, channel_id, fallback_text, blocks, thread_ts,
            attachments=attachments,
        )
    except Exception:
        pass  # Best-effort; failures silently dropped so Slack doesn't retry


@app.post("/api/slack/actions")
async def slack_actions(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack interactive component callbacks (button clicks).

    Slack POSTs here when a user clicks a button in a Block Kit message.
    Must respond 200 within 3 seconds; the actual work runs via BackgroundTasks
    after the response is sent.
    """
    body = await request.body()
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")

    if not _verify_slack_signature(body, timestamp, signature):
        return Response(status_code=200)  # Return 200 to prevent Slack retries

    form_data = parse_qs(body.decode())
    payload = json.loads(form_data.get("payload", ["{}"])[0])

    actions = payload.get("actions") or []
    if not actions:
        return Response(status_code=200)

    action = actions[0]
    action_id = action.get("action_id", "")
    channel_id = (payload.get("channel") or {}).get("id", "")
    thread_ts = (payload.get("message") or {}).get("ts")

    if action_id not in ("psh_post_summary", "psh_post_issues", "psh_post_issues_more"):
        return Response(status_code=200)

    try:
        value = json.loads(action.get("value", "{}"))
        account_id = value["account_id"]
        account_name = value["account_name"]
        period = value["period"]
        if period not in VALID_PERIODS:
            period = "6m"
        offset = int(value.get("offset", 0))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return Response(status_code=200)

    # Fire and forget - BackgroundTasks runs after the 200 response is sent,
    # ensuring Slack's 3-second deadline is always met.
    background_tasks.add_task(
        _handle_slack_action,
        action_id, account_id, account_name, period, channel_id, thread_ts,
        offset=offset,
    )

    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Scheduled reports — CRUD using LangGraph Platform cron jobs
# ---------------------------------------------------------------------------

class ScheduleRequest(BaseModel):
    account_id: str
    account_name: str
    label: str = ""
    destination_type: Literal["slack", "email"]
    channel_id: str | None = None
    email_addresses: list[str] | None = None
    sections: list[str] | None = None   # None = all sections; filtered to ALL_SECTIONS
    period: str = "1m"
    frequency: Literal["weekly", "monthly", "quarterly"]
    weekday: Annotated[int, Field(ge=0, le=6)] = 0    # 0=Mon … 6=Sun
    nth: Annotated[int, Field(ge=-1, le=4)] = 1       # 1–4 or -1 (last); 0 is invalid but excluded by ge=-1
    month_in_quarter: Annotated[int, Field(ge=1, le=3)] = 1
    hour_local: Annotated[int, Field(ge=0, le=23)] = 9
    timezone: str = "UTC"

    @field_validator("nth")
    @classmethod
    def validate_nth(cls, v: int) -> int:
        if v == 0:
            raise ValueError("nth must be 1–4 or -1 (last)")
        return v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            zoneinfo.ZoneInfo(v)
        except (zoneinfo.ZoneInfoNotFoundError, Exception):
            raise ValueError(f"Unknown timezone: {v!r}")
        return v

    @field_validator("email_addresses")
    @classmethod
    def validate_emails(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for addr in v:
                if "@" not in addr or not addr.split("@")[-1]:
                    raise ValueError(f"Invalid email address: {addr!r}")
        return v

    @field_validator("sections")
    @classmethod
    def validate_sections(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            v = [s for s in v if s in ALL_SECTIONS]
        return v or None


def _build_cron_expression(weekday: int, hour_local: int) -> str:
    """Return a cron expression using the local hour directly.

    The timezone is passed separately to crons.create so the platform handles
    DST transitions and sub-hour offsets (e.g. IST +5:30) natively.
    Python weekday 0=Mon → cron weekday 1=Mon (cron uses 0=Sun).
    """
    cron_wd = (weekday + 1) % 7
    return f"0 {hour_local} * * {cron_wd}"


def _build_run_condition(frequency: str, nth: int, weekday: int, month_in_quarter: int = 1) -> dict | None:
    if frequency == "weekly":
        return None
    if frequency == "monthly":
        return {"type": "nth_weekday_of_month", "n": nth, "weekday": weekday}
    # quarterly: fire on the nth weekday of a specific month within the quarter
    return {"type": "nth_weekday_of_month_in_quarter", "n": nth, "weekday": weekday, "month_in_quarter": month_in_quarter}


def _format_schedule(cron: dict) -> dict:
    """Flatten a LangGraph cron record into a frontend-friendly dict.

    Handles both nested payload structures ({"input": {...}}) and flat ones,
    and both "cron_id" and "id" key names, for robustness across SDK versions.
    """
    payload = cron.get("payload") or {}
    # Support both {"input": {...}} and flat payload (different SDK/server versions)
    raw_inp = payload.get("input")
    inp = raw_inp if isinstance(raw_inp, dict) else payload
    run_condition = inp.get("run_condition")

    frequency = "weekly"
    nth = 1
    month_in_quarter = 1
    if run_condition:
        ctype = run_condition.get("type", "")
        nth = run_condition.get("n", 1)
        if ctype == "nth_weekday_of_month":
            frequency = "monthly"
        elif ctype in ("nth_weekday_of_quarter", "nth_weekday_of_month_in_quarter"):
            frequency = "quarterly"
            month_in_quarter = run_condition.get("month_in_quarter", 1)

    # Parse weekday and hour from the stored cron expression "0 H * * WD"
    parts = (cron.get("schedule") or "0 9 * * 1").split()
    hour_utc = int(parts[1]) if len(parts) > 1 else 9
    cron_wd = int(parts[4]) if len(parts) > 4 else 1
    weekday = (cron_wd - 1) % 7  # cron 0=Sun → Python 6=Sun; cron 1=Mon → Python 0=Mon

    # For new crons the expression hour IS the local hour (platform handles UTC conversion).
    # For old crons (no platform timezone) it was UTC; fall back to payload's hour_local.
    cron_tz = cron.get("timezone")  # set by platform on new crons
    hour_local = inp.get("hour_local") or (hour_utc if not cron_tz else int(parts[1]))
    timezone = cron_tz or inp.get("timezone", "UTC")

    return {
        "cron_id": cron.get("cron_id") or cron.get("id"),
        "account_id": inp.get("account_id"),
        "account_name": inp.get("account_name"),
        "label": inp.get("label", ""),
        "destination_type": inp.get("destination_type"),
        "channel_id": inp.get("channel_id"),
        "email_addresses": inp.get("email_addresses"),
        "sections": inp.get("sections"),
        "period": inp.get("period", "1m"),
        "frequency": frequency,
        "weekday": weekday,
        "nth": nth,
        "month_in_quarter": month_in_quarter,
        "hour_utc": hour_utc,
        "hour_local": hour_local,
        "timezone": timezone,
        "created_by": inp.get("created_by"),
        "schedule": cron.get("schedule"),
        "next_run_date": cron.get("next_run_date"),
        "created_at": cron.get("created_at"),
    }


def _lg_client():
    """Return a LangGraph SDK client pointed at the local/deployed server.

    in-process ASGI transport (url=None) only works from within a graph node,
    not from a FastAPI HTTP handler. Default to http://localhost:8000 — that's
    the port used by both `langgraph dev` locally and LSD containers in production.
    Override with LANGGRAPH_API_URL if the server is on a different address.
    """
    from langgraph_sdk import get_client as _get_lg_client
    url = os.environ.get("LANGGRAPH_API_URL") or "http://localhost:8000"
    return _get_lg_client(url=url)


@app.get("/api/schedules")
async def list_schedules(
    account_id: str | None = None,
    _email: str = Depends(require_auth),
):
    """List all scheduled reports, optionally filtered to a single account."""
    try:
        client = _lg_client()
        # crons.search requires a UUID; resolve by graph_id rather than fetching all assistants
        assistants = await client.assistants.search(graph_id="report_dispatcher", limit=1)
        dispatcher_id = assistants[0]["assistant_id"] if assistants else None
        crons = await client.crons.search(
            assistant_id=dispatcher_id if dispatcher_id else None,
            limit=500,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to list schedules: {exc}") from exc

    result = []
    for cron in crons:
        formatted = _format_schedule(cron)
        if account_id and formatted.get("account_id") != account_id:
            continue
        result.append(formatted)
    return result


@app.post("/api/schedules")
async def create_schedule(body: ScheduleRequest, created_by: str = Depends(require_auth)):
    """Create a new scheduled report cron job in LangGraph Platform."""
    period = body.period if body.period in VALID_PERIODS else "1m"
    cron_expr = _build_cron_expression(body.weekday, body.hour_local)
    run_condition = _build_run_condition(body.frequency, body.nth, body.weekday, body.month_in_quarter)

    dispatcher_input = {
        "account_id": body.account_id,
        "account_name": body.account_name,
        "period": period,
        "destination_type": body.destination_type,
        "channel_id": body.channel_id,
        "email_addresses": body.email_addresses,
        "sections": body.sections,
        "run_condition": run_condition,
        "label": body.label,
        "hour_local": body.hour_local,
        "timezone": body.timezone,
        "created_by": created_by,
        "skipped": False,
        "result": None,
        "error": None,
    }
    try:
        client = _lg_client()
        cron = await client.crons.create(
            assistant_id="report_dispatcher",
            schedule=cron_expr,
            timezone=body.timezone,
            input=dispatcher_input,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to create schedule: {exc}") from exc

    await asyncio.to_thread(
        audit.log,
        "schedule_created",
        {
            "account_id": body.account_id,
            "account_name": body.account_name,
            "destination_type": body.destination_type,
            "frequency": body.frequency,
            "schedule": cron_expr,
        },
    )
    return _format_schedule(cron)


# ---------------------------------------------------------------------------
# QBR slide generation — Google Slides API
# ---------------------------------------------------------------------------

def _compute_qbr_data(
    open_issues: list[dict],
    closed_issues: list[dict],
    quarter_issues: list[dict],
    sla_pct: int | None,
    insights: dict,
    avg_response_hours: float | None = None,
) -> tuple[dict, dict]:
    """Derive slide 14 (Enterprise Support) and slide 15 (Product Feedback) data."""
    from datetime import datetime, timezone

    def is_fr(issue: dict) -> bool:
        cf = issue.get("custom_fields") or {}
        return (cf.get("disposition") or {}).get("value", "") == "feature_request"

    sev1 = sum(1 for i in open_issues if metrics_mod.get_priority(i) == "urgent")
    sev2 = sum(1 for i in open_issues if metrics_mod.get_priority(i) == "high")
    sev3 = sum(1 for i in open_issues if metrics_mod.get_priority(i) == "medium")
    sev4 = sum(1 for i in open_issues if metrics_mod.get_priority(i) == "low")
    waiting = sum(1 for i in open_issues if i.get("state") == "waiting_on_you")
    open_frs = [i for i in open_issues if is_fr(i)]

    # Median resolution time from closed quarter tickets (days from created to updated/closed)
    now = datetime.now(timezone.utc)
    resolution_days = []
    for i in quarter_issues:
        if i.get("state") not in {"closed", "resolved"}:
            continue
        raw_created = i.get("created_at", "")
        raw_updated = i.get("updated_at", "")
        if raw_created and raw_updated:
            try:
                created = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
                updated = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
                if updated > created:
                    resolution_days.append((updated - created).days)
            except (ValueError, TypeError):
                pass
    resolution_days.sort()
    median_resolution_days: float | None = None
    if resolution_days:
        mid = len(resolution_days) // 2
        median_resolution_days = float(
            resolution_days[mid] if len(resolution_days) % 2 else (resolution_days[mid - 1] + resolution_days[mid]) / 2
        )

    slide14 = {
        "sev1_open": sev1,
        "sev2_open": sev2,
        "sev3_open": sev3,
        "sev4_open": sev4,
        "waiting_on_you": waiting,
        "feature_requests_open": len(open_frs),
        "sla_pct": sla_pct,
        "avg_response_hours": avg_response_hours,
        "median_resolution_days": median_resolution_days,
        "observations": insights.get("observations", []),
        "opportunities": insights.get("opportunities", []),
    }

    delivered_frs = sum(1 for i in closed_issues if is_fr(i))
    fr_titles = [i.get("title", "").strip() for i in open_frs if i.get("title")]

    slide15 = {
        "feature_requests_open": len(open_frs),
        "feature_requests_delivered": delivered_frs,
        "open_feature_request_titles": fr_titles,
    }

    return slide14, slide15


@app.post("/api/accounts/{account_id}/qbr-slides")
async def create_qbr_slides(
    account_id: str,
    request: Request,
    user_email: str = Depends(require_auth),
):
    """Stream QBR slide generation progress via SSE, ending with a result event containing the URL."""
    if not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        raise HTTPException(
            status_code=501,
            detail="QBR slides not configured (GOOGLE_SERVICE_ACCOUNT_JSON missing)",
        )

    body = await request.json()
    account_name = (body.get("account_name") or "").strip()
    if not account_name:
        raise HTTPException(status_code=422, detail="account_name is required")

    import slides_client as slides_mod
    import roadmap_client as roadmap_mod

    quarter_start, quarter_end, quarter_label = slides_mod.get_last_quarter()
    chart_start = slides_mod.get_chart_start(6)

    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    async def _stream():
        try:
            # Step 1: fetch ticket data
            yield _sse("progress", {"step": "fetch", "label": "Fetching ticket data", "status": "running"})
            try:
                open_issues, closed_issues, quarter_issues, chart_issues = await asyncio.gather(
                    asyncio.to_thread(pylon_client.search_issues_for_account, account_id, OPEN_STATES),
                    asyncio.to_thread(
                        pylon_client.search_issues_for_account,
                        account_id, ["closed", "resolved"], None, None, quarter_start, quarter_end,
                    ),
                    asyncio.to_thread(
                        pylon_client.search_issues_for_account,
                        account_id, None, quarter_start, quarter_end,
                    ),
                    asyncio.to_thread(
                        pylon_client.search_issues_for_account,
                        account_id, None, chart_start,
                    ),
                )
            except Exception as exc:
                yield _sse("error", {"detail": f"Pylon API error: {exc}"})
                return
            yield _sse("progress", {"step": "fetch", "label": "Fetching ticket data", "status": "done"})

            # Step 2: AI insights
            yield _sse("progress", {"step": "insights", "label": "Generating AI insights", "status": "running"})
            sla_pct = metrics_mod.compute_sla_compliance(quarter_issues)
            avg_rt = metrics_mod.compute_avg_response_time(quarter_issues)

            def is_fr(i: dict) -> bool:
                cf = i.get("custom_fields") or {}
                return (cf.get("disposition") or {}).get("value", "") == "feature_request"

            open_frs = [i for i in open_issues if is_fr(i)]
            sev1 = sum(1 for i in open_issues if metrics_mod.get_priority(i) == "urgent")
            sev2 = sum(1 for i in open_issues if metrics_mod.get_priority(i) == "high")
            waiting = sum(1 for i in open_issues if i.get("state") == "waiting_on_you")
            tickets_closed_qtr = sum(1 for i in quarter_issues if i.get("state") in {"closed", "resolved"})
            try:
                insights = await generate_qbr_insights(
                    account_name=account_name,
                    quarter_label=quarter_label,
                    open_issues=open_issues,
                    sev1=sev1,
                    sev2=sev2,
                    waiting_on_you=waiting,
                    total_open=len(open_issues),
                    fr_open=len(open_frs),
                    sla_pct=sla_pct,
                    avg_response_hours=avg_rt,
                    tickets_raised_qtr=len(quarter_issues),
                    tickets_closed_qtr=tickets_closed_qtr,
                )
            except Exception as exc:
                yield _sse("error", {"detail": f"AI insights failed: {exc}"})
                return
            yield _sse("progress", {"step": "insights", "label": "Generating AI insights", "status": "done"})

            slide14, slide15 = _compute_qbr_data(open_issues, closed_issues, quarter_issues, sla_pct, insights, avg_rt)

            # Step 3: roadmap lookup and AI item selection (non-fatal)
            yield _sse("progress", {"step": "roadmap", "label": "Finding roadmap items", "status": "running"})
            roadmap_items: list[dict] = []
            try:
                from datetime import date as _date
                _today = _date.today()
                _slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
                _shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None

                # Build list of months: current + last 3 (= last quarter)
                _months: list[_date] = []
                _m, _y = _today.month, _today.year
                for _ in range(4):
                    _months.append(_date(_y, _m, 1))
                    _m -= 1
                    if _m == 0:
                        _m, _y = 12, _y - 1

                def _fetch_all_roadmaps() -> list[tuple[str, _date]]:
                    from googleapiclient.discovery import build as _build
                    from google.oauth2 import service_account as _sa
                    import json as _json
                    _creds = _sa.Credentials.from_service_account_info(
                        _json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
                        scopes=["https://www.googleapis.com/auth/drive"],
                    )
                    _drive = _build("drive", "v3", credentials=_creds)
                    found: list[tuple[str, _date]] = []
                    seen_ids: set[str] = set()
                    for month in _months:
                        pres_id = roadmap_mod.find_roadmap_in_drive(_drive, month)
                        if not pres_id and _slack_token and month == _months[0]:
                            url = roadmap_mod.fetch_roadmap_link_from_slack(_slack_token, month)
                            if url:
                                pres_id = roadmap_mod.copy_roadmap_to_drive(_drive, url, month, _shared_drive_id)
                        if pres_id and pres_id not in seen_ids:
                            seen_ids.add(pres_id)
                            found.append((pres_id, month))
                    return found

                def _extract_all(roadmap_list: list[tuple[str, _date]]) -> list[dict]:
                    from googleapiclient.discovery import build as _build
                    from google.oauth2 import service_account as _sa
                    import json as _json
                    _creds = _sa.Credentials.from_service_account_info(
                        _json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
                        scopes=["https://www.googleapis.com/auth/presentations"],
                    )
                    _slides = _build("slides", "v1", credentials=_creds)
                    all_items: list[dict] = []
                    for pres_id, month in roadmap_list:
                        try:
                            all_items.extend(roadmap_mod.extract_roadmap_items(_slides, pres_id, month))
                        except Exception as exc:
                            _log.warning("Could not extract roadmap from %s: %s", pres_id, exc)
                    return all_items

                roadmap_list = await asyncio.to_thread(_fetch_all_roadmaps)
                if roadmap_list:
                    raw_items = await asyncio.to_thread(_extract_all, roadmap_list)
                    # Deduplicate by title — keep only the latest month's version
                    from datetime import datetime as _dt
                    _seen: dict[str, dict] = {}
                    for _item in raw_items:
                        _title = _item.get("title", "").strip().lower()
                        _existing = _seen.get(_title)
                        if not _existing:
                            _seen[_title] = _item
                        else:
                            try:
                                if _dt.strptime(_item["month"], "%B %Y") > _dt.strptime(_existing["month"], "%B %Y"):
                                    _seen[_title] = _item
                            except (ValueError, KeyError):
                                pass
                    raw_items = list(_seen.values())
                    selected = await roadmap_mod.select_roadmap_items(
                        raw_items, open_issues, account_name, _today
                    )
                    # Order selected items oldest-month-first (left→right on slide)
                    def _month_key(item: dict) -> tuple:
                        try:
                            return (_dt.strptime(item["month"], "%B %Y").year,
                                    _dt.strptime(item["month"], "%B %Y").month)
                        except (ValueError, KeyError):
                            return (9999, 99)
                    roadmap_items = sorted(selected, key=_month_key)
            except Exception:
                _log.exception("Roadmap lookup failed — continuing without roadmap items")
            yield _sse("progress", {"step": "roadmap", "label": "Finding roadmap items", "status": "done"})

            # Step 4: create slide deck (copy template + text replacements)
            yield _sse("progress", {"step": "slides", "label": "Creating slide deck", "status": "running"})
            _quarter = (_today.month - 1) // 3 + 1
            _month_label = f"Q{_quarter} {_today.strftime('%B %Y')}"
            try:
                pres_id, url, customer_folder_id = await asyncio.to_thread(
                    slides_mod.create_slide_deck,
                    account_name, slide14, slide15, quarter_label, _month_label,
                )
            except Exception as exc:
                yield _sse("error", {"detail": f"Slides creation failed: {exc}"})
                return
            if roadmap_items:
                try:
                    await asyncio.to_thread(slides_mod.add_roadmap_items, pres_id, roadmap_items)
                except Exception:
                    _log.exception("Roadmap items insertion failed — continuing")
            yield _sse("progress", {"step": "slides", "label": "Creating slide deck", "status": "done"})

            # Step 5: metrics chart (non-fatal — slide keeps original image on failure)
            yield _sse("progress", {"step": "chart", "label": "Generating metrics chart", "status": "running"})
            try:
                await asyncio.to_thread(
                    slides_mod.add_metrics_chart,
                    pres_id, customer_folder_id,
                    quarter_issues, chart_issues, chart_start, quarter_label,
                )
            except Exception:
                _log.exception("Chart generation failed — continuing without chart")
            yield _sse("progress", {"step": "chart", "label": "Generating metrics chart", "status": "done"})

            # Step 6: share with the requesting user
            yield _sse("progress", {"step": "share", "label": "Sharing with you", "status": "running"})
            try:
                await asyncio.to_thread(slides_mod.share_presentation, pres_id, user_email)
            except Exception as exc:
                yield _sse("error", {"detail": f"Sharing failed: {exc}"})
                return
            yield _sse("progress", {"step": "share", "label": "Sharing with you", "status": "done"})

            await asyncio.to_thread(
                audit.log,
                "qbr_slides_generated",
                {"account_id": account_id, "account_name": account_name, "month": _month_label},
            )
            # Persist slide record so history endpoint can surface it
            await asyncio.to_thread(
                cache_mod.set_qbr_slide,
                account_id, _today.strftime("%Y-%m"), url, pres_id, _month_label,
            )
            yield _sse("result", {"url": url, "month": _today.strftime("%Y-%m"), "month_label": _month_label})

        except Exception as exc:
            yield _sse("error", {"detail": str(exc)})

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.get("/api/accounts/{account_id}/qbr-slides/history")
async def get_qbr_history(account_id: str, _email: str = Depends(require_auth)):
    """Return QBR slide history for the last 6 months, newest first.

    Uses Google Drive as the authoritative source (survives redeployments),
    falling back to the local disk cache when Drive is unavailable.
    """
    from datetime import date as _date
    from calendar import month_name as _mn
    today = _date.today()

    # Build 6-month window
    months = []
    m, y = today.month, today.year
    for _ in range(6):
        quarter = (m - 1) // 3 + 1
        months.append({
            "month": f"{y:04d}-{m:02d}",
            "month_label": f"Q{quarter} {_mn[m]} {y}",
            "is_current": (y == today.year and m == today.month),
        })
        m -= 1
        if m == 0:
            m, y = 12, y - 1

    month_keys = {e["month"] for e in months}

    # Drive is authoritative — survives LSD redeployments
    slides_by_month: dict[str, dict] = {}
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        try:
            account = pylon_client.get_account(account_id)
            account_name = account["name"] if account else None
            if account_name:
                shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None
                drive_slides = await asyncio.to_thread(
                    slides_mod.list_qbr_slides, account_name, shared_drive_id
                )
                for s in drive_slides:
                    if s["month"] in month_keys:
                        slides_by_month[s["month"]] = s
        except Exception:
            _log.warning("Drive QBR history lookup failed, falling back to local cache")

    # Local cache fallback for any months not found in Drive
    for ym in month_keys:
        if ym not in slides_by_month:
            cached = cache_mod.get_qbr_slide(account_id, ym)
            if cached:
                slides_by_month[ym] = cached

    return [{**e, "slide": slides_by_month.get(e["month"])} for e in months]


@app.delete("/api/schedules/{cron_id}")
async def delete_schedule(cron_id: str, _email: str = Depends(require_auth)):
    """Delete a scheduled report by its cron ID."""
    try:
        client = _lg_client()
        await client.crons.delete(cron_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to delete schedule: {exc}") from exc
    await asyncio.to_thread(audit.log, "schedule_deleted", {"cron_id": cron_id})
    return {"ok": True}
