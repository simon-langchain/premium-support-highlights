"""FastAPI backend for Premium Support Highlights dashboard.

API surface:
  GET  /api/accounts                              — sorted list of premium accounts
  GET  /api/accounts/{id}/data                    — metrics + open issues for an account
  GET  /api/accounts/{id}/cached-ticket-summaries — per-ticket AI summaries from disk cache
  POST /api/accounts/{id}/summary                 — stream an AI-generated account summary

The /summary endpoint uses SSE (server-sent events) with keepalive pings while Claude
generates. The Next.js route handler at frontend/.../summary/route.ts converts this
stream to plain JSON before it reaches the browser.

Caching strategy (all in-memory, 5-minute TTL):
  raw:{id}:{period}     — raw Pylon API responses, shared by /data and /summary so
                          they never make duplicate API calls within the same window
  payload:{id}:{period} — computed metrics payload, served directly by /data
  open:{id}             — current open issues for the polled /cached-ticket-summaries
"""

import os
import time
import asyncio
import json
from typing import Any

from dotenv import load_dotenv

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_root, ".env"))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import pylon_client
import metrics as metrics_mod
import audit
import cache as cache_mod
from summary_agent import generate_account_summary, make_summarise_tickets_tool

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
    return {
        "number": issue.get("number"),
        "title": issue.get("title", ""),
        "state": issue.get("state", ""),
        "priority": metrics_mod.get_priority(issue),
        "created_at": issue.get("created_at", ""),
        "tags": tags,
        "disposition": disposition,
        "external_issues": external_issues,
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
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/accounts")
def get_accounts():
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
async def get_cached_ticket_summaries(account_id: str):
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
async def get_account_summary(account_id: str, body: SummaryRequest):
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
            audit.log(
                "summary_generated",
                {"account_id": account_id, "account_name": body.account_name, "model": body.model},
            )
        except Exception:
            pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")
