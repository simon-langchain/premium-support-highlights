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
import statistics
import time
import asyncio
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import zoneinfo
from contextlib import asynccontextmanager
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
import message_activity
import audit
import cache as cache_mod
from summary_agent import generate_account_summary, make_summarise_tickets_tool, generate_qbr_insights, generate_usage_insights
from report import generate_report_html
from ticket_summarizer import parse_ticket_output
from llm import AVAILABLE_MODELS, DEFAULT_MODEL_ID

_IDLE_POLL_INTERVAL = 15 * 60  # how long to sleep once backfill and incremental sync are both idle


async def _message_sync_loop() -> None:
    """Background task: interleaves the staged newest-first backfill with a
    regular incremental catch-up.

    The two run interleaved in a single loop (not as separate concurrent
    tasks) so they never race on the shared store file — each iteration
    does at most one backfill step and/or one incremental sync step,
    sequentially. Both steps are bounded/chunked (see
    message_activity.run_backfill_step / run_incremental_sync_step) so
    neither can starve the other of a turn. The incremental step's "is a
    new cycle due" gate is persisted to disk rather than an in-process
    timer, so a backend restart doesn't reset the clock.

    Runs for the lifetime of the app. Every Pylon call this makes is already
    paced under Pylon's 20/min message-fetch cap, so it never competes with
    foreground requests for that budget — the dashboard just reads whatever
    is synced so far.
    """
    while True:
        backfill_done = await asyncio.to_thread(message_activity.backfill_complete)
        if not backfill_done:
            try:
                await message_activity.run_backfill_step()
            except Exception:
                # A single step failing (transient Pylon timeout/5xx, etc.) must not
                # abandon the rest of the backfill — retry that step after a short
                # pause rather than skipping ahead.
                _log.exception("message_activity backfill step failed — retrying shortly")
                await asyncio.sleep(30)

        try:
            incremental_has_more = await message_activity.run_incremental_sync_step()
        except Exception:
            _log.exception("message_activity incremental sync step failed")
            incremental_has_more = False

        if backfill_done and not incremental_has_more:
            # Nothing to do right now — idle until the next incremental cycle is due.
            await asyncio.sleep(_IDLE_POLL_INTERVAL)


# Narrowest to broadest — warmed in this order so the periods people look at
# most often become fast first, matching message_activity's newest-first
# backfill philosophy.
_TEAM_CACHE_WARM_PERIODS = ["7d", "1m", "3m", "6m", "1y"]
_TEAM_CACHE_WARM_INTERVAL = 4 * 3600  # well inside the 8-hour cache TTL


async def _team_cache_warmer_loop() -> None:
    """Keep the team-dashboard's org-wide Pylon caches from ever going stale
    during a live request.

    get_team_period_issues/get_team_backlog_issues serve a stale cache rather
    than block a request on a live fetch (a 6m/1y org-wide search can take
    several minutes — long enough to exceed the frontend's fetch timeout and
    500 the whole page, which is exactly what was happening before this loop
    existed). This is what actually keeps that served copy fresh: refreshes
    every period, plus the backlog, well inside their TTL. Runs immediately
    on startup (pre-warming before real traffic typically arrives) and then
    on a fixed interval — each refresh's own failure is independent, so one
    period timing out doesn't stop the others from refreshing.
    """
    while True:
        for period in _TEAM_CACHE_WARM_PERIODS:
            try:
                await asyncio.to_thread(pylon_client.get_team_period_issues, period, True)
            except Exception:
                _log.exception("team cache warmer: failed to refresh period=%s", period)
        try:
            await asyncio.to_thread(pylon_client.get_team_backlog_issues, OPEN_STATES, True)
        except Exception:
            _log.exception("team cache warmer: failed to refresh backlog")
        await asyncio.sleep(_TEAM_CACHE_WARM_INTERVAL)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    task = asyncio.create_task(_message_sync_loop())
    warmer_task = asyncio.create_task(_team_cache_warmer_loop())
    try:
        yield
    finally:
        task.cancel()
        warmer_task.cancel()


app = FastAPI(title="Premium Support Highlights API", version="0.1.0", lifespan=_lifespan)

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
_METRONOME_ID_SLUG = "account.salesforce.Metronome_Customer_Id__c"


class SummaryRequest(BaseModel):
    account_name: str
    model: str = DEFAULT_MODEL_ID
    period: str = "6m"
    force: bool = False

    @field_validator("model", mode="before")
    @classmethod
    def _default_model(cls, v: str | None) -> str:
        return v if v else DEFAULT_MODEL_ID


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


async def require_support_team(email: str = Depends(require_auth)) -> str:
    """Gate for the internal Support-team dashboard.

    Checks live (1hr-cached) Pylon "Support" team membership on top of the
    normal session check — separate from the customer-dashboard routes,
    which only require require_auth.
    """
    if _LOCAL_TEST_MODE:
        return email
    if not await asyncio.to_thread(pylon_client.is_support_team_member, email):
        raise HTTPException(status_code=403, detail="Not a member of the Support team")
    return email


async def require_admin(email: str = Depends(require_support_team)) -> str:
    """Gate for team-dashboard admin routes.

    Composing on require_support_team (rather than require_auth directly)
    means admin access is automatically revoked the moment someone is
    removed from the Pylon "Support" team, with no separate sync needed.
    """
    if _LOCAL_TEST_MODE:
        return email
    admin_emails = await asyncio.to_thread(auth_mod.get_admin_emails)
    if email.strip().lower() not in admin_emails:
        raise HTTPException(status_code=403, detail="Not a team-dashboard admin")
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

def _compute_usage_feature_count(chart_data: dict) -> int:
    """Count distinct LangSmith features with non-zero usage in the last 3 months.

    Checks 8 features: traces, agent runs, agent builder runs, experiments,
    prompts (commits or pulls), datasets, page views, evaluators.
    Result is stored in slide14["usage_feature_count"] and drives _usage_score.
    """
    monthly   = (chart_data.get("monthly_usage") or [])[-3:]
    pv_rows   = (chart_data.get("page_views") or [])[-3:]
    eval_rows = chart_data.get("evaluator_usage") or []

    def _any(rows, field):
        return any(float(r.get(field) or 0) > 0 for r in rows)

    return sum([
        _any(monthly, "billable_traces"),
        _any(monthly, "billable_agent_runs"),
        _any(monthly, "billable_agent_builder_runs"),
        _any(monthly, "total_experiments"),
        _any(monthly, "total_prompt_commits") or _any(monthly, "total_prompt_pulls"),
        _any(monthly, "total_datasets"),
        _any(pv_rows, "total_page_views"),
        any(float(r.get("rules") or 0) > 0 for r in eval_rows),
    ])


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
    _sla_compliance = metrics_mod.compute_sla_compliance(period_issues)
    return {
        "open_issues": [_normalise_issue(i, field_labels, account_id) for i in open_issues],
        "monthly_metrics": metrics_mod.compute_period_metrics(period_issues, period),
        "avg_response_time": metrics_mod.compute_avg_response_time(period_issues),
        "avg_resolution_time": metrics_mod.compute_avg_resolution_time(period_issues),
        "sla_compliance_pct": _sla_compliance,
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
    model: str = DEFAULT_MODEL_ID,
) -> str | None:
    """Return a fresh account summary, regenerating if missing or stale.

    Also ensures ticket summaries are fresh -stale ones are regenerated as
    part of the account summary pipeline (the agent calls summarise_tickets).
    """
    summary = await asyncio.to_thread(cache_mod.get_account_summary, account_id, period)
    if summary:
        return summary

    # Regenerate -force=True ensures stale ticket summaries are also refreshed
    summarise_tickets = make_summarise_tickets_tool(open_issues, force=True, account_name=account_name, model=model)
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


@app.get("/api/me")
async def get_me(email: str = Depends(require_auth)):
    """Return the current session's email and team-dashboard authorization flags.

    Used by the frontend chooser page to decide whether to show the internal
    dashboard option, and by /team pages to decide whether to show admin UI.
    """
    if _LOCAL_TEST_MODE:
        return {"email": email, "is_support_team_member": True, "is_admin": True}
    try:
        is_support = await asyncio.to_thread(pylon_client.is_support_team_member, email)
    except Exception:
        # Fail open to "not on the Support team" — a Pylon hiccup shouldn't
        # 500 the whole app for every logged-in user, just hide the
        # internal-dashboard option for them until it recovers.
        is_support = False
    is_admin = False
    if is_support:
        try:
            admin_emails = await asyncio.to_thread(auth_mod.get_admin_emails)
            is_admin = email.strip().lower() in admin_emails
        except Exception:
            is_admin = False
    return {"email": email, "is_support_team_member": is_support, "is_admin": is_admin}


@app.get("/api/tiers")
def get_tiers(_email: str = Depends(require_auth)):
    """Return sorted list of available Support Tier values from Pylon."""
    try:
        return pylon_client.get_available_tiers()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pylon API error: {exc}") from exc


@app.get("/api/models")
def get_models(_email: str = Depends(require_auth)):
    """Return list of LLM models available through the LangSmith Gateway."""
    return AVAILABLE_MODELS


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


## ---------------------------------------------------------------------------
## Internal Support-Team Dashboard
## ---------------------------------------------------------------------------

_NUMERIC_REP_METRIC_KEYS = [
    "avg_response_time", "sla_compliance_pct", "avg_resolution_time", "avg_csat", "avg_reply_time",
    "median_response_time", "median_resolution_time", "median_reply_time", "median_csat",
]


def _resolve_member_by_email(email: str) -> tuple[str | None, str]:
    """Look up (pylon_user_id, display_name) for an email via GET /users.

    Falls back to the email itself as the display name if not found. Returns
    an id even for members with zero metrics this period (they may simply
    not appear in member_results if they have no period/backlog activity —
    that's a separate "no data" case from "this isn't a real user").
    """
    email_norm = email.strip().lower()
    for member in pylon_client.get_team_members():
        candidates = [member.get("email"), *(member.get("emails") or [])]
        if any((c or "").strip().lower() == email_norm for c in candidates):
            return member.get("id"), member.get("name") or member.get("email") or email
    return None, email


def _build_rep_metrics(
    issues: list[dict],
    open_issues: list[dict],
    update_count: int = 0,
    avg_reply_time: float | None = None,
    median_reply_time: float | None = None,
) -> dict:
    """Compute a RepMetrics dict for one Support-team member's issue subset.

    update_count and avg/median_reply_time are period-totals from
    message_activity's synced data — passed in rather than computed here
    since they come from a completely separate data source (message sync,
    scoped by ticket assignment/update time) than issues/open_issues
    (scoped by ticket creation time).
    """
    state_breakdown = metrics_mod.get_state_breakdown(open_issues)
    return {
        "tickets_taken": len(issues),
        "avg_response_time": metrics_mod.compute_avg_response_time(issues),
        "median_response_time": metrics_mod.compute_median_response_time(issues),
        "sla_compliance_pct": metrics_mod.compute_sla_compliance(issues),
        "avg_resolution_time": metrics_mod.compute_avg_resolution_time(issues),
        "median_resolution_time": metrics_mod.compute_median_resolution_time(issues),
        "backlog_count": state_breakdown.get("waiting_on_you", 0),
        "avg_csat": metrics_mod.compute_avg_csat(issues),
        "median_csat": metrics_mod.compute_median_csat(issues),
        "update_count": update_count,
        "avg_reply_time": avg_reply_time,
        "median_reply_time": median_reply_time,
        "state_breakdown": state_breakdown,
        "pending_wait": metrics_mod.compute_pending_wait_stats(open_issues),
    }


_TEAM_AGGREGATE_KEYS = ["tickets_taken", "backlog_count", "update_count", *_NUMERIC_REP_METRIC_KEYS]


def _average_rep_metrics(all_metrics: list[dict], method: Literal["mean", "median"]) -> dict:
    """Combine RepMetrics dicts across members into the "Team" figure.

    `method` picks how N reps' numbers combine into one team figure —
    "mean" (a straight average across reps) or "median" (outlier-resistant:
    one rep having an unusually quiet or busy period can't swing what
    "Team" means for everyone else). Applies uniformly to every field,
    including metrics that don't have a per-rep avg/median toggle
    (tickets_taken, sla_compliance_pct, etc.) — the caller computes both
    variants so the frontend's Average/Median toggle can pick whichever
    matches what it's showing for "me". Skips None per-metric, not per-member.
    """
    if not all_metrics:
        return {key: (0 if key in ("tickets_taken", "backlog_count", "update_count") else None)
                for key in _TEAM_AGGREGATE_KEYS} | {"state_breakdown": {}, "pending_wait": {}}
    agg = statistics.median if method == "median" else (lambda values: sum(values) / len(values))
    result: dict[str, Any] = {}
    for key in _TEAM_AGGREGATE_KEYS:
        values = [m[key] for m in all_metrics if m.get(key) is not None]
        result[key] = round(agg(values), 1) if values else None
    result["pending_wait"] = {}
    combined_breakdown: dict[str, int] = {}
    for m in all_metrics:
        for state, count in (m.get("state_breakdown") or {}).items():
            combined_breakdown[state] = combined_breakdown.get(state, 0) + count
    result["state_breakdown"] = combined_breakdown
    return result


async def _fetch_team_raw(
    period: str, force_refresh: bool = False
) -> tuple[dict[str, str], list[dict], list[dict]]:
    """Fetch (member_id->email map, period issues, backlog issues), in-memory cached.

    Layers on top of pylon_client's own disk cache — same two-tier pattern as
    _fetch_raw_data — and uses the same per-key asyncio.Lock dedup so
    concurrent requests from multiple reps don't trigger duplicate
    multi-thousand-row Pylon fetches.

    force_refresh mirrors the account dashboard's force=true handling in
    get_account_data: evict the in-memory entry and force pylon_client past
    its own disk cache too, so the manual refresh button actually refreshes.
    """
    cache_key = f"team_raw:{period}"

    if force_refresh:
        _data_cache.pop(cache_key, None)
    else:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached["members"], cached["period_issues"], cached["backlog_issues"]

    if cache_key not in _fetch_locks:
        _fetch_locks[cache_key] = asyncio.Lock()

    async with _fetch_locks[cache_key]:
        if not force_refresh:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached["members"], cached["period_issues"], cached["backlog_issues"]

        try:
            members, period_issues, backlog_issues = await asyncio.gather(
                asyncio.to_thread(pylon_client.get_support_team_member_ids, force_refresh),
                asyncio.to_thread(pylon_client.get_team_period_issues, period, force_refresh),
                asyncio.to_thread(pylon_client.get_team_backlog_issues, OPEN_STATES, force_refresh),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Pylon API error: {exc}") from exc

        _cache_set(cache_key, {
            "members": members,
            "period_issues": period_issues,
            "backlog_issues": backlog_issues,
        })
        return members, period_issues, backlog_issues


_TREND_METRIC_KEYS = [
    "tickets_taken", "avg_response_time", "avg_resolution_time", "update_count", "avg_reply_time",
    "median_response_time", "median_resolution_time", "median_reply_time",
]


def _average_rep_trends(trends: list[list[dict]], method: Literal["mean", "median"]) -> list[dict]:
    """Combine N members' per-bucket trend series into one team series —
    `method` picks mean-across-reps or median-across-reps per bucket, same
    as _average_rep_metrics.

    All trend series for the same period share identical bucket labels/order
    (same windowing logic in compute_rep_trend), so buckets align by index.
    """
    if not trends:
        return []
    agg = statistics.median if method == "median" else (lambda values: sum(values) / len(values))
    result: list[dict] = []
    for i in range(len(trends[0])):
        row: dict[str, Any] = {"label": trends[0][i]["label"]}
        for key in _TREND_METRIC_KEYS:
            values = [t[i][key] for t in trends if t[i].get(key) is not None]
            row[key] = round(agg(values), 1) if values else None
        result.append(row)
    return result


# Bounded so a manual "Refresh Data" click can never hang the way a full
# org-wide Pylon search could (see pylon_client's stale-while-revalidate fix
# for that exact problem on the issue-data side) — an incremental sync is
# normally fast (a handful of changed tickets since the last 15-minute
# background pass), but this caps it just in case.
_MESSAGE_ACTIVITY_REFRESH_TIMEOUT = 12.0


async def _maybe_refresh_message_activity(force: bool) -> None:
    """On a manual refresh, opportunistically pull messages for tickets that
    changed since the last incremental sync, so the response reflects fresh
    update/reply-time data — not just fresh issue counts. Never wipes or
    resets anything (that's message_activity's schema-version migration,
    unrelated to this); if it doesn't finish within the timeout, it's simply
    left for the next scheduled background pass rather than blocking the
    request further.
    """
    if not force:
        return
    try:
        await asyncio.wait_for(
            message_activity.run_incremental_sync_step(), timeout=_MESSAGE_ACTIVITY_REFRESH_TIMEOUT
        )
    except TimeoutError:
        pass
    except Exception:
        _log.exception("message_activity: manual-refresh incremental sync failed")


async def _compute_all_member_metrics(
    period: str, force_refresh: bool = False, granularity: str | None = None
) -> tuple[dict[str, dict], dict, list[dict], dict, list[dict]]:
    """Return ({member_id: {email, name, is_admin, metrics, trend}}, team_average,
    team_average_trend, team_median, team_median_trend).

    team_average/team_average_trend combine reps via mean; team_median/
    team_median_trend combine the same reps via median — both are always
    computed so the frontend's Average/Median toggle can pick whichever
    matches what it's showing for "me", for every tile, not just the ones
    with a per-rep avg/median split.

    Shared by /api/team-dashboard/data (slices out just the caller) and the
    admin-only /api/team-dashboard/team (returns everyone) — same underlying
    fetch, sliced differently depending on who's asking.

    granularity ("day" | "week" | "month") overrides the period's default
    trend bucketing; None keeps the existing per-period default. Only affects
    the trend series — metrics (period totals) are unaffected.

    Admins are excluded from both team_average*/team_median* (but still
    appear in member_results, e.g. for the admin table) — admins often
    aren't doing frontline ticket work at the same volume as individual
    reps, so folding them into the benchmark reps are compared against
    would skew it.
    """
    members, period_issues, backlog_issues = await _fetch_team_raw(period, force_refresh)
    admin_emails = await asyncio.to_thread(auth_mod.get_admin_emails)
    message_daily_counts = await asyncio.to_thread(
        message_activity.get_member_daily_counts, list(members.keys())
    )
    message_response_seconds = await asyncio.to_thread(
        message_activity.get_member_response_seconds, list(members.keys())
    )

    period_by_member: dict[str, list[dict]] = {mid: [] for mid in members}
    backlog_by_member: dict[str, list[dict]] = {mid: [] for mid in members}

    for issue in period_issues:
        mid = (issue.get("assignee") or {}).get("id")
        if mid in period_by_member:
            period_by_member[mid].append(issue)

    for issue in backlog_issues:
        mid = (issue.get("assignee") or {}).get("id")
        if mid in backlog_by_member:
            backlog_by_member[mid].append(issue)

    names_by_id: dict[str, str] = {}
    try:
        for u in await asyncio.to_thread(pylon_client.get_team_members):
            uid = u.get("id")
            if uid:
                names_by_id[uid] = u.get("name") or u.get("email") or ""
    except Exception:
        pass

    member_results: dict[str, dict] = {}
    for mid, email in members.items():
        p_issues = period_by_member.get(mid, [])
        b_issues = backlog_by_member.get(mid, [])
        activity_trend = metrics_mod.compute_message_activity_trend(
            message_daily_counts.get(mid, {}), period, granularity
        )
        update_total = sum(b["update_count"] for b in activity_trend)
        response_trend = metrics_mod.compute_response_time_trend(
            message_response_seconds.get(mid, {}), period, granularity
        )
        total_replies = sum(b["reply_count"] for b in response_trend)
        reply_summary = metrics_mod.compute_response_time_summary(
            message_response_seconds.get(mid, {}), period
        )
        if not p_issues and not b_issues and not update_total and not total_replies:
            continue  # no activity this period — excluded from results and the average
        trend = metrics_mod.compute_rep_trend(p_issues, period, granularity)
        for bucket, activity_bucket, response_bucket in zip(trend, activity_trend, response_trend):
            bucket["update_count"] = activity_bucket["update_count"]
            bucket["avg_reply_time"] = response_bucket["avg_reply_time"]
            bucket["median_reply_time"] = response_bucket["median_reply_time"]
        member_results[mid] = {
            "email": email,
            "name": names_by_id.get(mid, email),
            "is_admin": email.strip().lower() in admin_emails,
            "metrics": _build_rep_metrics(
                p_issues, b_issues, update_total, reply_summary["avg"], reply_summary["median"]
            ),
            "trend": trend,
        }

    non_admin_results = [r for r in member_results.values() if not r["is_admin"]]
    non_admin_metrics = [r["metrics"] for r in non_admin_results]
    non_admin_trends = [r["trend"] for r in non_admin_results]
    team_average = _average_rep_metrics(non_admin_metrics, method="mean")
    team_median = _average_rep_metrics(non_admin_metrics, method="median")
    team_average_trend = _average_rep_trends(non_admin_trends, method="mean")
    team_median_trend = _average_rep_trends(non_admin_trends, method="median")
    return member_results, team_average, team_average_trend, team_median, team_median_trend


@app.get("/api/team-dashboard/data")
async def get_team_dashboard_data(
    period: str = Query("1m"),
    as_email: str | None = Query(None, alias="as"),
    force: bool = Query(False),
    granularity: str | None = Query(None),
    email: str = Depends(require_support_team),
):
    """Return the caller's own metrics vs. the Support-team average.

    Never includes another member's individual numbers — except when an
    admin passes `as`, which lets them view any single member's metrics
    through this same "me vs average" view (still never exposes everyone's
    numbers at once — that's what the admin-only /api/team-dashboard/team
    route is for).

    force=true mirrors GET /api/accounts/{id}/data?force=true — bypasses
    both the in-memory and disk caches for a manual refresh.

    granularity ("day" | "week" | "month") overrides the period's default
    trend-chart bucketing; anything else (including omitted) keeps the
    existing per-period default.
    """
    if period not in VALID_PERIODS:
        period = "1m"
    if granularity not in ("day", "week", "month"):
        granularity = None
    await _maybe_refresh_message_activity(force)
    try:
        member_results, team_average, team_average_trend, team_median, team_median_trend = (
            await _compute_all_member_metrics(period, force, granularity)
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pylon API error: {exc}") from exc

    target_email = email
    viewing_as = None
    if as_email:
        if not _LOCAL_TEST_MODE:
            admin_emails = await asyncio.to_thread(auth_mod.get_admin_emails)
            if email.strip().lower() not in admin_emails:
                raise HTTPException(status_code=403, detail="Only admins can view another member's metrics")
        target_email = as_email

    my_id, resolved_name = await asyncio.to_thread(_resolve_member_by_email, target_email)
    my_entry = member_results.get(my_id) if my_id else None
    me_metrics = my_entry["metrics"] if my_entry else _average_rep_metrics([], method="median")
    # Even a real member with zero activity this period should still get a
    # correctly-shaped (all-empty) trend series, not just an empty list —
    # keeps "viewing a quiet rep" visually distinct from "no data at all".
    me_trend = my_entry["trend"] if my_entry else metrics_mod.compute_rep_trend([], period, granularity)
    if as_email:
        viewing_as = {"email": target_email, "name": my_entry["name"] if my_entry else resolved_name}

    return {
        "me": me_metrics,
        "me_trend": me_trend,
        "team_average": team_average,
        "team_average_trend": team_average_trend,
        "team_median": team_median,
        "team_median_trend": team_median_trend,
        "team_member_count": len(member_results),
        "viewing_as": viewing_as,
    }


@app.get("/api/team-dashboard/team")
async def get_team_dashboard_team(
    period: str = Query("1m"),
    member: str | None = Query(None),
    force: bool = Query(False),
    _email: str = Depends(require_admin),
):
    """Admin-only: every Support-team member's individual metrics + team average."""
    if period not in VALID_PERIODS:
        period = "1m"
    await _maybe_refresh_message_activity(force)
    try:
        member_results, team_average, team_average_trend, team_median, team_median_trend = (
            await _compute_all_member_metrics(period, force)
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pylon API error: {exc}") from exc

    members_out = [
        {"email": r["email"], "name": r["name"], "is_admin": r["is_admin"], "metrics": r["metrics"]}
        for r in member_results.values()
    ]
    if member:
        members_out = [m for m in members_out if m["email"].strip().lower() == member.strip().lower()]
    members_out.sort(key=lambda m: m["name"].lower())

    return {
        "members": members_out,
        "team_average": team_average,
        "team_average_trend": team_average_trend,
        "team_median": team_median,
        "team_median_trend": team_median_trend,
    }


@app.get("/api/team-dashboard/sync-status")
async def get_team_dashboard_sync_status(_email: str = Depends(require_support_team)):
    """Progress of the background message-activity backfill (see message_activity.py).

    Read-only aggregate counters only — no ticket/message content.
    """
    return await asyncio.to_thread(message_activity.get_backfill_status)


class AdminEmailBody(BaseModel):
    email: str


@app.get("/api/team-dashboard/admins")
async def list_team_dashboard_admins(_email: str = Depends(require_admin)):
    """Admin-only: list current team-dashboard admin emails."""
    admins = await asyncio.to_thread(auth_mod.get_admin_emails)
    return {"admins": sorted(admins)}


@app.post("/api/team-dashboard/admins")
async def add_team_dashboard_admin(body: AdminEmailBody, _email: str = Depends(require_admin)):
    """Admin-only: add an email to the team-dashboard admin list."""
    candidate = body.email.strip().lower()
    if not candidate or "@" not in candidate:
        raise HTTPException(status_code=400, detail="Invalid email")
    await asyncio.to_thread(auth_mod.add_admin, candidate)
    admins = await asyncio.to_thread(auth_mod.get_admin_emails)
    return {"admins": sorted(admins)}


@app.delete("/api/team-dashboard/admins/{admin_email}")
async def remove_team_dashboard_admin(admin_email: str, _email: str = Depends(require_admin)):
    """Admin-only: remove an email from the team-dashboard admin list.

    Refuses to remove the last remaining admin (400).
    """
    try:
        await asyncio.to_thread(auth_mod.remove_admin, admin_email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    admins = await asyncio.to_thread(auth_mod.get_admin_emails)
    return {"admins": sorted(admins)}


@app.get("/api/accounts/{account_id}/data")
async def get_account_data(
    account_id: str,
    account_name: str = Query(...),
    period: str = Query("6m"),
    force: bool = Query(False),
    _email: str = Depends(require_auth),
):
    """Fetch issues and compute all metrics for an account."""
    if period not in VALID_PERIODS:
        period = "6m"

    payload_key = f"payload:{account_id}:{period}"
    raw_key = f"raw:{account_id}:{period}"

    if not force:
        # 1. In-memory cache (hot path — same process, sub-5-min)
        if (payload := _cache_get(payload_key)) is not None:
            return payload

        # 2. Disk cache (cold start / post-redeployment path)
        if (payload := await asyncio.to_thread(cache_mod.get_payload_cache, account_id, period)) is not None:
            _cache_set(payload_key, payload)
            return payload
    else:
        # Force refresh: evict both in-memory caches so _fetch_raw_data also hits Pylon
        _data_cache.pop(payload_key, None)
        _data_cache.pop(raw_key, None)

    # Fresh fetch from Pylon
    field_labels, open_issues, period_issues, csat_responses = await _fetch_raw_data(account_id, period)
    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period, account_id)
    _cache_set(payload_key, payload)
    await asyncio.to_thread(cache_mod.set_payload_cache, account_id, period, payload)
    await asyncio.to_thread(audit.log, "account_loaded", {"account_id": account_id, "account_name": account_name})
    return payload


@app.get("/api/accounts/{account_id}/cached-ticket-summaries")
async def get_cached_ticket_summaries(
    account_id: str,
    model: str = Query(default=""),
    _email: str = Depends(require_auth),
):
    """Return cached per-ticket summaries keyed by ticket number.

    Uses the open-issues cache so the frequent frontend polling doesn't
    hit the Pylon API on every request. The model query param ensures
    summaries are read from the correct model-specific cache entries.
    """
    ticket_model = model or DEFAULT_MODEL_ID
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
            raw = cache_mod.get_ticket_summary(issue_id, latest_msg_time, ticket_model)
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
    summarise_tickets = make_summarise_tickets_tool(open_issues, body.force, account_name=body.account_name, model=body.model)

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
    avg_res: float | None = payload.get("avg_resolution_time")
    sla_pct: int | None = payload.get("sla_compliance_pct")
    csat: float | None = payload.get("csat")

    # Priority breakdown as a compact string, e.g. "Sev 1: 2  Sev 2: 5  Sev 3: 3"
    priority_bd = payload.get("priority_breakdown", {})
    _p_order = ["urgent", "high", "medium", "low"]
    priority_parts = [
        f"{_PRIORITY_EMOJI.get(p, '')} {_PRIORITY_LABELS[p]}: *{priority_bd[p]}*"
        for p in _p_order if priority_bd.get(p, 0) > 0
    ]

    def _fmt_res(h: float) -> str:
        weeks = int(h // 40)
        after = h - weeks * 40
        days = int(after // 8)
        hrs = round(after - days * 8)
        if weeks > 0:
            return f"{weeks}w {days}d" if days > 0 else f"{weeks}w"
        if days > 0:
            return f"{days}d {hrs}h" if hrs > 0 else f"{days}d"
        return f"{hrs}h"

    fields = [
        _field("Open Issues (Current)", str(open_count)),
        _field(f"Raised ({period_label})", str(total_raised)),
        _field(f"Closed ({period_label})", str(total_closed)),
        _field(f"Avg Time to First Response ({period_label})", f"{avg_rt:.1f} hrs" if avg_rt is not None else "—"),
        _field(f"Avg Resolution Time ({period_label})", _fmt_res(avg_res) if avg_res is not None else "—"),
        _field(f"SLA Compliance ({period_label})", f"{sla_pct}%" if sla_pct is not None else "—"),
    ]
    if csat is not None:
        fields.append(_field(f"CSAT ({period_label})", f"{_fmt_csat(csat)} / 5"))

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


def _slack_not_in_channel_message(
    slack_token: str,
    channel_id: str,
    channel_name: str | None,
    bot_name: str | None = None,
) -> str:
    """Build the standard "bot isn't in this channel, here's how to invite it" message.

    Shared by the manual-send error handler, the schedule list/create channel
    warnings, and the live pre-save channel check, so the frontend's single
    invite-command parser (SlackInviteWarning) renders all three identically.
    bot_name can be pre-fetched by batch callers to avoid one auth.test call
    per channel.
    """
    bot = bot_name or slack_client.get_bot_name(slack_token) or "lc-support-highlights"
    channel_label = f"#{channel_name}" if channel_name else f"'{channel_id}'"
    return f"The bot is not a member of channel {channel_label}. Invite it with /invite @{bot} in that channel, then try again."


@app.get("/api/slack/channel-check")
async def check_slack_channel(channel_id: str, _email: str = Depends(require_auth)) -> dict:
    """Live pre-save check for the Schedule form: is the bot in this channel?

    Checks the literal channel_id passed in (not resolved through
    SLACK_OVERRIDE_CHANNEL) — this answers "is the bot in the channel I just
    picked," which is what's useful while building a schedule. Fails soft:
    missing token, network errors, or an inconclusive membership check all
    just return no warning rather than an error the form has to handle.
    """
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not slack_token:
        return {"warning": None}
    try:
        info = await asyncio.to_thread(slack_client.check_channel_membership, slack_token, channel_id)
        if info.get("is_member") is not False:
            return {"warning": None}
        message = await asyncio.to_thread(
            _slack_not_in_channel_message, slack_token, channel_id, info.get("name")
        )
        return {"warning": message}
    except Exception:
        return {"warning": None}


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
            detail = _slack_not_in_channel_message(slack_token, channel_id, channel_name)
            raise HTTPException(status_code=422, detail=detail) from exc
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
                summarise_tickets = make_summarise_tickets_tool(open_issues, force=False, account_name=account_name, model=DEFAULT_MODEL_ID)
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
    destination_type: Literal["slack", "email", "qbr"]
    channel_id: str | None = None
    email_addresses: list[str] | None = None
    # QBR-specific: how to notify when slides are ready
    qbr_notify_type: Literal["slack", "email"] | None = None
    qbr_notify_channel_id: str | None = None
    qbr_notify_emails: list[str] | None = None
    qbr_template_type: Literal["full_deck", "support_highlights"] = "full_deck"
    sections: list[str] | None = None   # None = all sections; filtered to ALL_SECTIONS
    period: str = "1m"
    model: str = DEFAULT_MODEL_ID
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

    @field_validator("qbr_notify_emails")
    @classmethod
    def validate_qbr_notify_emails(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for addr in v:
                if "@" not in addr or not addr.split("@")[-1]:
                    raise ValueError(f"Invalid email address: {addr!r}")
                domain = addr.split("@")[-1].lower()
                if domain != "langchain.dev":
                    raise ValueError(
                        f"QBR notifications can only be sent to @langchain.dev addresses, got: {addr!r}"
                    )
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


def _next_run_date_from_condition(
    condition: dict | None,
    cron_next: str | None,
    weekday: int,
    hour_local: int,
    tz_name: str,
) -> str | None:
    """Return the next ISO datetime string when this condition will actually fire.

    For weekly schedules (condition=None) the raw cron next_run_date is correct.
    For monthly/quarterly we walk forward week-by-week until we hit a matching date,
    so the displayed "next run" reflects the real run rather than every Monday.
    """
    if condition is None:
        return cron_next

    from datetime import date as _d, timedelta as _td, datetime as _dt, timezone as _tz
    from zoneinfo import ZoneInfo as _ZI

    ctype = condition.get("type", "")
    n = int(condition.get("n", 1))
    miq = int(condition.get("month_in_quarter", 1))

    today = _dt.now(_tz.utc).date()
    start = today + _td(days=1)
    days_ahead = (weekday - start.weekday()) % 7
    d = start + _td(days=days_ahead)

    for _ in range(60):  # scan up to ~14 months
        if ctype == "nth_weekday_of_month":
            if n > 0:
                count = sum(1 for x in range(1, d.day + 1) if _d(d.year, d.month, x).weekday() == weekday)
                hit = count == n
            else:
                hit = (d + _td(days=7)).month != d.month
        elif ctype == "nth_weekday_of_month_in_quarter":
            q = (d.month - 1) // 3
            tgt = q * 3 + miq
            if d.month != tgt:
                hit = False
            elif n > 0:
                count = sum(1 for x in range(1, d.day + 1) if _d(d.year, d.month, x).weekday() == weekday)
                hit = count == n
            else:
                hit = (d + _td(days=7)).month != d.month
        else:
            return cron_next  # legacy type — leave as-is
        if hit:
            try:
                local_dt = _dt(d.year, d.month, d.day, hour_local, 0, 0, tzinfo=_ZI(tz_name))
            except Exception:
                local_dt = _dt(d.year, d.month, d.day, hour_local, 0, 0, tzinfo=_tz.utc)
            return local_dt.astimezone(_tz.utc).isoformat()
        d += _td(days=7)

    return cron_next


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
        "qbr_notify_type": inp.get("qbr_notify_type"),
        "qbr_notify_channel_id": inp.get("qbr_notify_channel_id"),
        "qbr_notify_emails": inp.get("qbr_notify_emails"),
        "qbr_template_type": inp.get("qbr_template_type", "full_deck"),
        "sections": inp.get("sections"),
        "period": inp.get("period", "1m"),
        "model": inp.get("model", DEFAULT_MODEL_ID),
        "frequency": frequency,
        "weekday": weekday,
        "nth": nth,
        "month_in_quarter": month_in_quarter,
        "hour_utc": hour_utc,
        "hour_local": hour_local,
        "timezone": timezone,
        "created_by": inp.get("created_by"),
        "schedule": cron.get("schedule"),
        "next_run_date": _next_run_date_from_condition(run_condition, cron.get("next_run_date"), weekday, hour_local, timezone),
        "created_at": cron.get("created_at"),
    }


async def _annotate_channel_warnings(schedules: list[dict]) -> None:
    """Mutate each schedule dict in place, adding channel_warning (None or a message).

    Live-checks whether the bot is actually a member of each schedule's
    literal configured Slack channel, so a broken channel (e.g. never
    invited, kicked, Slack Connect channel needing approval) is visible in
    the list view rather than only discovered when a scheduled send silently
    fails. Deliberately does NOT resolve through SLACK_OVERRIDE_CHANNEL —
    that's a local-testing-only redirect for actual sends (report_dispatcher.py
    still honors it there, unchanged), and this warning should always describe
    the channel the schedule is actually configured for, matching the live
    pre-save check in the create/edit form.

    Fails soft everywhere: any unexpected error here must never break
    GET /api/schedules, since that would take down schedule management for
    reasons unrelated to the schedules themselves.
    """
    for s in schedules:
        s["channel_warning"] = None

    try:
        slack_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
        if not slack_token:
            return

        def target_channel(s: dict) -> str | None:
            if s.get("destination_type") == "slack":
                return s.get("channel_id")
            if s.get("destination_type") == "qbr" and s.get("qbr_notify_type") == "slack":
                return s.get("qbr_notify_channel_id")
            return None

        by_channel: dict[str, list[dict]] = {}
        for s in schedules:
            ch = target_channel(s)
            if ch:
                by_channel.setdefault(ch, []).append(s)
        if not by_channel:
            return

        channel_ids = list(by_channel.keys())
        results = await asyncio.gather(
            *[asyncio.to_thread(slack_client.check_channel_membership, slack_token, ch) for ch in channel_ids],
            return_exceptions=True,
        )
        bad = [(ch, info) for ch, info in zip(channel_ids, results) if isinstance(info, dict) and info.get("is_member") is False]
        if not bad:
            return

        # Fetch the bot's name once for the whole batch rather than once per channel.
        bot_name = await asyncio.to_thread(slack_client.get_bot_name, slack_token)
        for ch, info in bad:
            message = _slack_not_in_channel_message(slack_token, ch, info.get("name"), bot_name=bot_name)
            for s in by_channel[ch]:
                s["channel_warning"] = message
    except Exception:
        for s in schedules:
            s["channel_warning"] = None


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

    await _annotate_channel_warnings(result)
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
        "qbr_notify_type": body.qbr_notify_type,
        "qbr_notify_channel_id": body.qbr_notify_channel_id,
        "qbr_notify_emails": body.qbr_notify_emails,
        "qbr_template_type": body.qbr_template_type,
        "sections": body.sections,
        "run_condition": run_condition,
        "label": body.label,
        "model": body.model,
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
    formatted = _format_schedule(cron)
    await _annotate_channel_warnings([formatted])
    return formatted


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


async def _do_qbr_generation(account_id: str, account_name: str, template_type: str = "full_deck", model: str | None = None) -> tuple[str, str, str]:
    """Generate QBR slides and share with the langchain.dev domain.

    Called by the scheduled dispatcher. Returns (pres_id, url, month_label).
    Raises on unrecoverable failures; roadmap/chart/Hex steps are non-fatal.
    """
    from datetime import date as _date, datetime as _dt
    import slides_client as slides_mod
    import roadmap_client as roadmap_mod

    if template_type not in ("support_highlights", "full_deck"):
        template_type = "full_deck"
    is_full_deck = template_type == "full_deck"
    resolved_template_id = await asyncio.to_thread(slides_mod.resolve_template_id, template_type)
    _ctx_token = slides_mod._template_ctx.set(resolved_template_id)

    # If a slide for this month already exists (e.g. a parallel schedule triggered
    # simultaneously), return it immediately without regenerating.
    _now_ym = _date.today().strftime("%Y-%m")
    _cached = await asyncio.to_thread(cache_mod.get_qbr_slide, account_id, _now_ym)
    if _cached:
        return _cached["pres_id"], _cached["url"], _cached["month_label"]

    quarter_start, quarter_end, quarter_label = slides_mod.get_last_quarter()
    chart_start = slides_mod.get_chart_start(6)

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
        asyncio.to_thread(pylon_client.search_issues_for_account, account_id, None, chart_start),
    )

    sla_pct = metrics_mod.compute_sla_compliance(quarter_issues)
    avg_rt = metrics_mod.compute_avg_response_time(quarter_issues)

    def _is_fr(i: dict) -> bool:
        cf = i.get("custom_fields") or {}
        return (cf.get("disposition") or {}).get("value", "") == "feature_request"

    open_frs = [i for i in open_issues if _is_fr(i)]
    sev1 = sum(1 for i in open_issues if metrics_mod.get_priority(i) == "urgent")
    sev2 = sum(1 for i in open_issues if metrics_mod.get_priority(i) == "high")
    waiting = sum(1 for i in open_issues if i.get("state") == "waiting_on_you")
    tickets_closed_qtr = sum(1 for i in quarter_issues if i.get("state") in {"closed", "resolved"})

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
        model=model,
    )

    slide14, slide15 = _compute_qbr_data(
        open_issues, closed_issues, quarter_issues, sla_pct, insights, avg_rt
    )

    # Chart data — BigQuery (full deck only; support_highlights has no chart slides)
    import hex_client as _hex
    import bigquery_client as _bq
    hex_charts: dict[str, bytes] = {}
    _bq_chart_data: dict | None = None
    _maturity_data: list[dict] | None = None
    _account = pylon_client.get_account(account_id)

    if is_full_deck:
        _metronome_id = pylon_client._get_custom_field(_account, _METRONOME_ID_SLUG) if _account else ""
        if _metronome_id:
            try:
                _bq_chart_data = await asyncio.to_thread(_bq.fetch_chart_data, _metronome_id)
                if _bq_chart_data:
                    _en = _bq_chart_data.get("enablement_stats") or {}
                    if _en:
                        slide14["academy_enrolled"] = _en.get("academy_enrolled", 0)
                        slide14["billable_seats"] = _en.get("billable_seats", 0)
                        slide14["est_engineering_headcount"] = _en.get("est_engineering_headcount", 0)
                    _raw_sign_ups = _bq_chart_data.get("sign_ups_by_course", [])
                    slide14["total_sign_ups"] = sum(int(r.get("sign_ups") or 0) for r in _raw_sign_ups)
                    slide14["num_courses"] = len(_raw_sign_ups)
                    # Usage score inputs
                    _cm = _bq_chart_data.get("contract_metrics") or {}
                    slide14["pct_commit_used"]   = _cm.get("pct_commit_used")
                    slide14["pct_into_contract"] = _cm.get("pct_into_contract")
                    slide14["usage_feature_count"] = _compute_usage_feature_count(_bq_chart_data)
            except Exception:
                _log.exception("BigQuery chart fetch failed for %s — continuing without usage charts", account_name)
            try:
                _maturity_data = await asyncio.to_thread(_bq.fetch_maturity_data, _metronome_id)
                if _maturity_data:
                    _radar = await asyncio.to_thread(
                        _hex.generate_maturity_radar_from_bq, _maturity_data, account_name,
                    )
                    if _radar:
                        hex_charts[_hex.MATURITY_CHART_CELL_ID] = _radar
                    _bar = await asyncio.to_thread(
                        _hex.generate_maturity_bar_from_bq, _maturity_data, account_name,
                    )
                    if _bar:
                        hex_charts["_maturity_bar"] = _bar
            except Exception:
                _log.exception("Maturity chart generation failed for %s — continuing without it", account_name)
        else:
            _log.info("No Metronome ID for %s — skipping chart fetch", account_name)

        if _bq_chart_data:
            try:
                _usage_ins = await generate_usage_insights(
                    account_name, quarter_label, _bq_chart_data, _maturity_data, model=model
                )
                slide14.update(_usage_ins)
            except Exception:
                _log.exception("Usage insights generation failed for %s — continuing", account_name)

    # Roadmap items (non-fatal)
    roadmap_items: list[dict] = []
    try:
        _today = _date.today()
        _slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
        _shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None

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
            _drive = _build("drive", "v3", credentials=_creds, cache_discovery=False)
            found: list[tuple[str, _date]] = []
            seen_ids: set[str] = set()
            for month in _months:
                pres_id = roadmap_mod.find_roadmap_in_drive(_drive, month)
                if not pres_id and _slack_token and month == _months[0]:
                    url = roadmap_mod.fetch_roadmap_link_from_slack(_slack_token, month)
                    if url:
                        try:
                            pres_id = roadmap_mod.copy_roadmap_to_drive(_drive, url, month, _shared_drive_id)
                        except Exception as _copy_exc:
                            _log.warning("Could not copy roadmap for %s (%s) — skipping", month, _copy_exc)
                            pres_id = None
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
            _slides = _build("slides", "v1", credentials=_creds, cache_discovery=False)
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
            selected = await roadmap_mod.select_roadmap_items(raw_items, open_issues, account_name, _today, model=model)

            def _month_key(item: dict) -> tuple:
                try:
                    return (_dt.strptime(item["month"], "%B %Y").year,
                            _dt.strptime(item["month"], "%B %Y").month)
                except (ValueError, KeyError):
                    return (9999, 99)
            roadmap_items = sorted(selected, key=_month_key)
    except Exception:
        _log.exception("Roadmap lookup failed in scheduled QBR — continuing without roadmap items")

    _today = _date.today()
    _quarter = (_today.month - 1) // 3 + 1
    _month_label = f"Q{_quarter} {_today.strftime('%B %Y')}"
    _ym = _today.strftime("%Y-%m")
    _shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None

    try:
        existing = await asyncio.to_thread(slides_mod.list_qbr_slides, account_name, _shared_drive_id)
        for _s in existing:
            if _s["month"] == _ym:
                try:
                    await asyncio.to_thread(slides_mod.delete_file, _s["pres_id"])
                except Exception:
                    _log.warning("Could not delete QBR slide %s — skipping", _s["pres_id"])
    except Exception:
        _log.exception("QBR slide listing failed for %s — continuing", account_name)

    pres_id, url, customer_folder_id = await asyncio.to_thread(
        slides_mod.create_slide_deck, account_name, slide14, slide15, quarter_label, _month_label,
    )

    _domain = ((_account.get("primary_domain") or _account.get("domain") or "") if _account else "").strip()
    if _domain:
        try:
            _logo_token = os.environ.get("LOGO_DEV_TOKEN", "").strip()
            _logo_url = f"https://img.logo.dev/{_domain}?token={_logo_token}" if _logo_token else f"https://img.logo.dev/{_domain}"
            await asyncio.to_thread(slides_mod.add_customer_logo, pres_id, _logo_url, customer_folder_id)
        except Exception:
            _log.exception("Customer logo insertion failed for %s — continuing", account_name)

    if roadmap_items:
        try:
            await asyncio.to_thread(slides_mod.add_roadmap_items, pres_id, roadmap_items)
        except Exception:
            _log.exception("Roadmap items insertion failed in scheduled QBR — continuing")

    if hex_charts:
        try:
            maturity_bytes = hex_charts.get(_hex.MATURITY_CHART_CELL_ID)
            if maturity_bytes:
                await asyncio.to_thread(
                    slides_mod.add_hex_chart, pres_id, customer_folder_id, maturity_bytes,
                )
            bar_bytes = hex_charts.get("_maturity_bar")
            if bar_bytes:
                await asyncio.to_thread(
                    slides_mod.add_maturity_bar_chart, pres_id, customer_folder_id, bar_bytes,
                )
        except Exception:
            _log.exception("Hex chart insertion failed in scheduled QBR — continuing")

    if _maturity_data:
        try:
            await asyncio.to_thread(slides_mod.update_maturity_journey_slide, pres_id, _maturity_data, account_name, _month_label)
        except Exception:
            _log.exception("Maturity journey slide update failed in scheduled QBR — continuing")

    if _bq_chart_data:
        try:
            commit_bytes = await asyncio.to_thread(_hex.build_commit_usage_from_bq, _bq_chart_data)
            if commit_bytes:
                await asyncio.to_thread(
                    slides_mod.add_commit_usage_chart, pres_id, customer_folder_id, commit_bytes,
                )
        except Exception:
            _log.exception("Commit usage chart insertion failed in scheduled QBR — continuing")

        try:
            _composite = await asyncio.to_thread(_hex.create_usage_composite_from_bq, _bq_chart_data)
            if _composite:
                await asyncio.to_thread(
                    slides_mod.add_usage_chart, pres_id, customer_folder_id, _composite,
                )
        except Exception:
            _log.exception("Usage chart composite failed in scheduled QBR — continuing")

        try:
            _feature_composite = await asyncio.to_thread(_hex.create_feature_usage_composite_from_bq, _bq_chart_data)
            if _feature_composite:
                await asyncio.to_thread(
                    slides_mod.add_feature_usage_chart, pres_id, customer_folder_id, _feature_composite,
                )
        except Exception:
            _log.exception("Feature usage chart composite failed in scheduled QBR — continuing")

    try:
        _sign_ups = (_bq_chart_data or {}).get("sign_ups_by_course", [])
        if _sign_ups:
            _academy_table = await asyncio.to_thread(_hex.build_sign_ups_table_from_bq, _sign_ups)
            if _academy_table:
                await asyncio.to_thread(
                    slides_mod.add_academy_table, pres_id, customer_folder_id, _academy_table,
                )
    except Exception:
        _log.exception("Academy table generation failed in scheduled QBR — continuing")

    try:
        await asyncio.to_thread(
            slides_mod.add_metrics_chart,
            pres_id, customer_folder_id, quarter_issues, chart_issues, chart_start, quarter_label,
        )
    except Exception:
        _log.exception("Chart generation failed in scheduled QBR — continuing")

    # Share with the whole langchain.dev domain so any team member can open the link
    await asyncio.to_thread(slides_mod.share_with_domain, pres_id, "langchain.dev")

    await asyncio.to_thread(
        cache_mod.set_qbr_slide, account_id, _ym, url, pres_id, _month_label,
    )

    slides_mod._template_ctx.reset(_ctx_token)
    return pres_id, url, _month_label


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

    template_type = (body.get("template_type") or "full_deck").strip()
    if template_type not in ("support_highlights", "full_deck"):
        template_type = "full_deck"
    is_full_deck = template_type == "full_deck"
    qbr_model = (body.get("model") or "").strip() or None

    import slides_client as slides_mod
    import roadmap_client as roadmap_mod

    resolved_template_id = await asyncio.to_thread(slides_mod.resolve_template_id, template_type)

    quarter_start, quarter_end, quarter_label = slides_mod.get_last_quarter()
    chart_start = slides_mod.get_chart_start(6)

    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    async def _stream():
        _ctx_token = slides_mod._template_ctx.set(resolved_template_id)
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
                    model=qbr_model,
                )
            except Exception as exc:
                yield _sse("error", {"detail": f"AI insights failed: {exc}"})
                return
            yield _sse("progress", {"step": "insights", "label": "Generating AI insights", "status": "done"})

            slide14, slide15 = _compute_qbr_data(open_issues, closed_issues, quarter_issues, sla_pct, insights, avg_rt)

            # Step 3: Chart data — BigQuery (full deck only; support_highlights has no chart slides)
            import hex_client as _hex
            import bigquery_client as _bq
            hex_charts: dict[str, bytes] = {}
            _bq_chart_data: dict | None = None
            _maturity_data: list[dict] | None = None
            _account = pylon_client.get_account(account_id)

            if is_full_deck:
                yield _sse("progress", {"step": "hex", "label": "Fetching chart data", "status": "running"})
                _metronome_id = pylon_client._get_custom_field(_account, _METRONOME_ID_SLUG) if _account else ""
                if _metronome_id:
                    try:
                        _bq_chart_data = await asyncio.to_thread(_bq.fetch_chart_data, _metronome_id)
                        if _bq_chart_data:
                            _en = _bq_chart_data.get("enablement_stats") or {}
                            if _en:
                                slide14["academy_enrolled"] = _en.get("academy_enrolled", 0)
                                slide14["billable_seats"] = _en.get("billable_seats", 0)
                                slide14["est_engineering_headcount"] = _en.get("est_engineering_headcount", 0)
                            _raw_sign_ups = _bq_chart_data.get("sign_ups_by_course", [])
                            slide14["total_sign_ups"] = sum(int(r.get("sign_ups") or 0) for r in _raw_sign_ups)
                            slide14["num_courses"] = len(_raw_sign_ups)
                            # Usage score inputs
                            _cm = _bq_chart_data.get("contract_metrics") or {}
                            slide14["pct_commit_used"]   = _cm.get("pct_commit_used")
                            slide14["pct_into_contract"] = _cm.get("pct_into_contract")
                            slide14["usage_feature_count"] = _compute_usage_feature_count(_bq_chart_data)
                    except Exception:
                        _log.exception("BigQuery chart fetch failed for %s — continuing without usage charts", account_name)
                    try:
                        _maturity_data = await asyncio.to_thread(_bq.fetch_maturity_data, _metronome_id)
                        if _maturity_data:
                            _radar = await asyncio.to_thread(
                                _hex.generate_maturity_radar_from_bq, _maturity_data, account_name,
                            )
                            if _radar:
                                hex_charts[_hex.MATURITY_CHART_CELL_ID] = _radar
                            _bar = await asyncio.to_thread(
                                _hex.generate_maturity_bar_from_bq, _maturity_data, account_name,
                            )
                            if _bar:
                                hex_charts["_maturity_bar"] = _bar
                    except Exception:
                        _log.exception("Maturity chart generation failed for %s — continuing without it", account_name)
                else:
                    _log.info("No Metronome ID for %s — skipping chart fetch", account_name)

                if _bq_chart_data:
                    try:
                        _usage_ins = await generate_usage_insights(
                            account_name, quarter_label, _bq_chart_data, _maturity_data, model=qbr_model
                        )
                        slide14.update(_usage_ins)
                    except Exception:
                        _log.exception("Usage insights generation failed for %s — continuing", account_name)

                yield _sse("progress", {"step": "hex", "label": "Fetching chart data", "status": "done"})
            else:
                yield _sse("progress", {"step": "hex", "label": "Fetching chart data", "status": "done"})

            # Step 4: roadmap lookup and AI item selection (non-fatal)
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
                    _drive = _build("drive", "v3", credentials=_creds, cache_discovery=False)
                    found: list[tuple[str, _date]] = []
                    seen_ids: set[str] = set()
                    for month in _months:
                        pres_id = roadmap_mod.find_roadmap_in_drive(_drive, month)
                        if not pres_id and _slack_token and month == _months[0]:
                            url = roadmap_mod.fetch_roadmap_link_from_slack(_slack_token, month)
                            if url:
                                try:
                                    pres_id = roadmap_mod.copy_roadmap_to_drive(_drive, url, month, _shared_drive_id)
                                except Exception as _copy_exc:
                                    _log.warning("Could not copy roadmap for %s (%s) — skipping", month, _copy_exc)
                                    pres_id = None
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
                    _slides = _build("slides", "v1", credentials=_creds, cache_discovery=False)
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
                        raw_items, open_issues, account_name, _today, model=qbr_model
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

            # Step 5: create slide deck (copy template + text replacements)
            yield _sse("progress", {"step": "slides", "label": "Creating slide deck", "status": "running"})
            _quarter = (_today.month - 1) // 3 + 1
            _month_label = f"Q{_quarter} {_today.strftime('%B %Y')}"
            _ym = _today.strftime("%Y-%m")
            _shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None
            try:
                existing = await asyncio.to_thread(
                    slides_mod.list_qbr_slides, account_name, _shared_drive_id
                )
                for _s in existing:
                    if _s["month"] == _ym:
                        try:
                            await asyncio.to_thread(slides_mod.delete_file, _s["pres_id"])
                        except Exception:
                            _log.warning("Could not delete QBR slide %s — skipping", _s["pres_id"])
            except Exception:
                _log.exception("QBR slide listing failed for %s — continuing", account_name)
            try:
                pres_id, url, customer_folder_id = await asyncio.to_thread(
                    slides_mod.create_slide_deck,
                    account_name, slide14, slide15, quarter_label, _month_label,
                )
            except Exception as exc:
                yield _sse("error", {"detail": f"Slides creation failed: {exc}"})
                return
            _domain = ((_account.get("primary_domain") or _account.get("domain") or "") if _account else "").strip()
            if _domain:
                try:
                    _logo_token = os.environ.get("LOGO_DEV_TOKEN", "").strip()
                    _logo_url = f"https://img.logo.dev/{_domain}?token={_logo_token}" if _logo_token else f"https://img.logo.dev/{_domain}"
                    await asyncio.to_thread(slides_mod.add_customer_logo, pres_id, _logo_url, customer_folder_id)
                except Exception:
                    _log.exception("Customer logo insertion failed for %s — continuing", account_name)
            if roadmap_items:
                try:
                    await asyncio.to_thread(slides_mod.add_roadmap_items, pres_id, roadmap_items)
                except Exception:
                    _log.exception("Roadmap items insertion failed — continuing")
            if hex_charts:
                try:
                    maturity_bytes = hex_charts.get(_hex.MATURITY_CHART_CELL_ID)
                    if maturity_bytes:
                        await asyncio.to_thread(
                            slides_mod.add_hex_chart, pres_id, customer_folder_id, maturity_bytes,
                        )
                    bar_bytes = hex_charts.get("_maturity_bar")
                    if bar_bytes:
                        await asyncio.to_thread(
                            slides_mod.add_maturity_bar_chart, pres_id, customer_folder_id, bar_bytes,
                        )
                except Exception:
                    _log.exception("Hex chart insertion failed — continuing")

            if _maturity_data:
                try:
                    await asyncio.to_thread(slides_mod.update_maturity_journey_slide, pres_id, _maturity_data, account_name, _month_label)
                except Exception:
                    _log.exception("Maturity journey slide update failed — continuing")

            if _bq_chart_data:
                try:
                    commit_bytes = await asyncio.to_thread(_hex.build_commit_usage_from_bq, _bq_chart_data)
                    if commit_bytes:
                        await asyncio.to_thread(
                            slides_mod.add_commit_usage_chart, pres_id, customer_folder_id, commit_bytes,
                        )
                except Exception:
                    _log.exception("Commit usage chart insertion failed — continuing")

                try:
                    _composite = await asyncio.to_thread(_hex.create_usage_composite_from_bq, _bq_chart_data)
                    if _composite:
                        await asyncio.to_thread(
                            slides_mod.add_usage_chart, pres_id, customer_folder_id, _composite,
                        )
                except Exception:
                    _log.exception("Usage chart composite failed — continuing")

                try:
                    _feature_composite = await asyncio.to_thread(_hex.create_feature_usage_composite_from_bq, _bq_chart_data)
                    if _feature_composite:
                        await asyncio.to_thread(
                            slides_mod.add_feature_usage_chart, pres_id, customer_folder_id, _feature_composite,
                        )
                except Exception:
                    _log.exception("Feature usage chart composite failed — continuing")
                try:
                    _sign_ups = (_bq_chart_data or {}).get("sign_ups_by_course", [])
                    if _sign_ups:
                        _academy_table = await asyncio.to_thread(_hex.build_sign_ups_table_from_bq, _sign_ups)
                        if _academy_table:
                            await asyncio.to_thread(
                                slides_mod.add_academy_table, pres_id, customer_folder_id, _academy_table,
                            )
                except Exception:
                    _log.exception("Academy table generation failed — continuing")
            yield _sse("progress", {"step": "slides", "label": "Creating slide deck", "status": "done"})

            # Step 6: metrics chart (non-fatal — slide keeps original image on failure)
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

            # Step 7: share with the requesting user
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
        finally:
            slides_mod._template_ctx.reset(_ctx_token)

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.post("/api/accounts/{account_id}/qbr-slides/{pres_id}/share")
async def share_qbr_slide(account_id: str, pres_id: str, user_email: str = Depends(require_auth)):
    """Grant the requesting user writer access to a previously generated QBR slide."""
    import slides_client as slides_mod
    await asyncio.to_thread(slides_mod.share_presentation, pres_id, user_email)
    return {}


@app.delete("/api/accounts/{account_id}/qbr-slides/{year_month}")
async def delete_qbr_slide(account_id: str, year_month: str, _email: str = Depends(require_auth)):
    """Remove a QBR slide record from the cache by year-month (YYYY-MM)."""
    import re
    if not re.fullmatch(r"\d{4}-\d{2}", year_month):
        raise HTTPException(status_code=400, detail="year_month must be YYYY-MM")
    cache_mod.delete_qbr_slide(account_id, year_month)
    return {}


@app.get("/api/accounts/{account_id}/qbr-slides/history")
async def get_qbr_history(account_id: str, account_name: str = "", _email: str = Depends(require_auth)):
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
    if account_name and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        try:
            import slides_client as slides_mod
            shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None
            drive_slides = await asyncio.to_thread(
                slides_mod.list_qbr_slides, account_name, shared_drive_id
            )
            for s in drive_slides:
                # drive_slides is ordered newest-first; first match per month wins
                if s["month"] in month_keys and s["month"] not in slides_by_month:
                    slides_by_month[s["month"]] = s
        except Exception:
            _log.exception("Drive QBR history lookup failed, falling back to local cache")

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
