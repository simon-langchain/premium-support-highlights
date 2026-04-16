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

import hashlib
import hmac
import os
import re
import time
import asyncio
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from urllib.parse import parse_qs

from dotenv import load_dotenv

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_root, ".env"))

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

import auth as auth_mod

import pylon_client
import slack_client
import metrics as metrics_mod
import audit
import cache as cache_mod
from summary_agent import generate_account_summary, make_summarise_tickets_tool
from report import generate_report_html

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


class SlackReportRequest(BaseModel):
    account_name: str
    period: str = "6m"
    channel_id: str | None = None  # Override the default channel for this post


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


def _normalise_issue(issue: dict, field_labels: dict) -> dict:
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
) -> dict:
    """Compute the full API payload from raw Pylon data."""
    disposition_bd_raw = metrics_mod.get_disposition_breakdown(open_issues)
    return {
        "open_issues": [_normalise_issue(i, field_labels) for i in open_issues],
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
    summarise_tickets = make_summarise_tickets_tool(open_issues, force=True)
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

@app.post("/api/auth/request")
async def auth_request(body: AuthRequestBody):
    """Check eligibility and send a login OTP. Never reveals whether an email
    exists in Pylon -unauthorized addresses get the same non-error response."""
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
    response = JSONResponse({"ok": True})
    # secure=True is required in production (HTTPS) but breaks localhost (HTTP).
    # LSD always runs over HTTPS; local dev uses http://localhost.
    is_https = bool(os.environ.get("ALLOWED_ORIGINS"))
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


@app.get("/api/accounts")
def get_accounts(_email: str = Depends(require_auth)):
    """Return sorted list of premium accounts [{id, name}, ...]."""
    try:
        accounts = pylon_client.get_premium_accounts()
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
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period)
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

    def _read_summaries() -> dict[int, str]:
        out: dict[int, str] = {}
        for issue in open_issues:
            issue_id = issue.get("id", "")
            number = issue.get("number")
            if number is None:
                continue
            latest_msg_time = issue.get("latest_message_time") or issue.get("updated_at") or ""
            summary = cache_mod.get_ticket_summary(issue_id, latest_msg_time)
            if summary:
                out[number] = summary
        return out

    return await asyncio.to_thread(_read_summaries)


@app.post("/api/accounts/{account_id}/summary")
async def get_account_summary(account_id: str, body: SummaryRequest, _email: str = Depends(require_auth)):
    """Generate an AI account summary, streamed as SSE to keep the connection alive."""
    period = body.period if body.period in VALID_PERIODS else "6m"
    field_labels, open_issues, period_issues, csat_responses = await _fetch_raw_data(account_id, period)
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period)
    summarise_tickets = make_summarise_tickets_tool(open_issues, body.force)

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
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period)

    # Collect cached per-ticket summaries (same logic as /cached-ticket-summaries)
    def _read_summaries() -> dict[int, str]:
        out: dict[int, str] = {}
        for issue in open_issues:
            issue_id = issue.get("id", "")
            number = issue.get("number")
            if number is None:
                continue
            latest_msg_time = issue.get("latest_message_time") or issue.get("updated_at") or ""
            summary = cache_mod.get_ticket_summary(issue_id, latest_msg_time)
            if summary:
                out[number] = summary
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
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period)

    def _read_summaries() -> dict[int, str]:
        out: dict[int, str] = {}
        for issue in open_issues:
            issue_id = issue.get("id", "")
            number = issue.get("number")
            if number is None:
                continue
            latest_msg_time = issue.get("latest_message_time") or issue.get("updated_at") or ""
            summary = cache_mod.get_ticket_summary(issue_id, latest_msg_time)
            if summary:
                out[number] = summary
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

_STATE_LABELS = {
    "new":                 "New",
    "waiting_on_you":      "Waiting on LangChain",
    "on_hold":             "On Hold",
    "waiting_on_customer": "Waiting on Customer",
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
) -> tuple[str, list[dict]]:
    """Compact metrics snapshot with 2-column field grid and action buttons."""
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
            "text": {
                "type": "plain_text",
                "text": account_name,
                "emoji": False,
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"*{period_label}*  ·  {_today()}"}],
        },
        {"type": "divider"},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":dart: *KEY METRICS*"}]},
        {"type": "section", "fields": fields},
    ]

    # Trend chart — daily labels for short periods, monthly abbreviations otherwise
    monthly = payload.get("monthly_metrics", [])
    if len(monthly) >= 2:
        is_daily = period in ("7d", "1m")
        # Daily labels: "Apr 9" / "Apr 10" (max 6 chars); monthly: "Apr" (3 chars)
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
        gap = label_w + 2  # label + 2 spaces before bars
        header_row = f"`{'':{gap}}{'Raised':^18}  {'Closed':^18}`"
        trend_label = ":calendar: *DAILY TREND*" if is_daily else ":calendar: *MONTHLY TREND*"
        blocks += [
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": trend_label}]},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join([header_row] + rows)}},
        ]

    state_bd = payload.get("state_breakdown", {})
    _s_order = ["new", "waiting_on_you", "on_hold", "waiting_on_customer"]
    state_parts = [
        f"{_STATE_EMOJI.get(s, '')} {_STATE_LABELS.get(s, s)}: *{state_bd[s]}*"
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

    action_value = json.dumps({"account_id": account_id, "account_name": account_name, "period": period})
    blocks += [
        {"type": "divider"},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":open_book: *MORE DETAILS*"}]},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Account Summary", "emoji": True},
                    "action_id": "psh_post_summary",
                    "value": action_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Current Open Issues", "emoji": True},
                    "action_id": "psh_post_issues",
                    "value": action_value,
                },
            ],
        },
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
    ticket_summaries: dict[int, str],
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
        summary = ticket_summaries.get(number, "")

        priority_label = _PRIORITY_LABELS.get(priority, priority.title())
        state_label = _STATE_LABELS.get(state, state.replace("_", " ").title())
        color = _PRIORITY_COLORS.get(priority, _PRIORITY_COLORS["none"])

        s_emoji = _STATE_EMOJI.get(state, "")

        slack_link = issue.get("slack_url")
        ticket_ref = f"<{slack_link}|#{number}>" if slack_link else f"#{number}"
        title_line = f"*{ticket_ref}  {title}*"

        if summary:
            summary_text = summary[:1200] + ("…" if len(summary) > 1200 else "")
            body = f"{title_line}\n{summary_text}"
        else:
            body = title_line

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

    # Always ensure the default channel appears first in the list
    if default_id:
        if not any(c["id"] == default_id for c in available_channels):
            default_name = await _resolve_name(default_id)
            available_channels = [{"id": default_id, "name": default_name or default_id}] + available_channels
        else:
            # Move it to the front
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
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period)
    fallback_text, blocks = _build_metrics_blocks(account_id, body.account_name, payload, period)

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
            raise HTTPException(
                status_code=422,
                detail=(
                    f"The bot is not a member of channel '{channel_id}'. "
                    "Invite it with /invite @BotName in that channel, then try again."
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
            payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period)
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
            payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period)

            # For the first page, regenerate stale/missing summaries.
            # Subsequent pages skip regeneration -summaries were already warmed on first click.
            if action_id == "psh_post_issues":
                summarise_tickets = make_summarise_tickets_tool(open_issues, force=False)
                await summarise_tickets.ainvoke({})

            def _read_ticket_summaries() -> dict[int, str]:
                out: dict[int, str] = {}
                for issue in open_issues:
                    number = issue.get("number")
                    if number is None:
                        continue
                    latest = issue.get("latest_message_time") or issue.get("updated_at") or ""
                    summary = cache_mod.get_ticket_summary(issue.get("id", ""), latest)
                    if summary:
                        out[number] = summary
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
