"""LangGraph graph for scheduled Slack / email report dispatch.

Registered in langgraph.json as "report_dispatcher".
Invoked by LangGraph Platform cron jobs created via POST /api/schedules.

The graph has a single node that:
  1. Evaluates the optional run_condition (e.g. "first Monday of month")
  2. Fetches fresh Pylon data for the account
  3. Posts a Slack metrics block OR sends an HTML email report
"""

import asyncio
import os
import smtplib
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_root, ".env"))

import cache as cache_mod
import pylon_client
import slack_client
from report import generate_report_html
from ticket_summarizer import parse_ticket_output

# Import shared helpers and constants from the FastAPI app.
# main.py defines no circular imports from this module, so this is safe.
from main import (
    ALL_SECTIONS,
    OPEN_STATES,
    VALID_PERIODS,
    _build_metrics_blocks,
    _build_payload,
    _compute_csat,
    _format_field_value,
    _normalise_issue,
)


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class ReportState(TypedDict):
    account_id: str
    account_name: str
    period: str                          # "7d" | "1m" | "3m" | "6m" | "1y"
    destination_type: str                # "slack" | "email"
    channel_id: Optional[str]           # Slack channel ID
    email_addresses: Optional[list[str]]
    sections: Optional[list[str]]        # None = all sections
    run_condition: Optional[dict]        # e.g. {"type": "nth_weekday_of_month", "n": 1, "weekday": 0}
    label: str
    hour_local: Optional[int]            # local hour for display/audit only; cron already encodes UTC
    timezone: Optional[str]             # IANA timezone name for display/audit only
    # Outputs written by the node
    skipped: bool
    result: Optional[str]
    error: Optional[str]


# ---------------------------------------------------------------------------
# Run-condition evaluation
# ---------------------------------------------------------------------------

def _should_run(condition: dict | None) -> bool:
    """Return True if today satisfies the run_condition.

    condition types:
      None                            → always run
      nth_weekday_of_month            → n>0: nth occurrence; n=-1: last occurrence
      nth_weekday_of_month_in_quarter → nth weekday of a specific month within the quarter
                                        (month_in_quarter=1/2/3; n>0 or n=-1)
      nth_weekday_of_quarter          → legacy: nth weekday counting through the whole quarter
    """
    if condition is None:
        return True

    today = datetime.now(timezone.utc).date()
    ctype = condition.get("type", "")
    n = int(condition.get("n", 1))
    target_wd = int(condition.get("weekday", 0))  # 0=Mon, 4=Fri (Python weekday)

    if today.weekday() != target_wd:
        return False

    if ctype == "nth_weekday_of_month":
        if n > 0:
            count = sum(
                1 for d in range(1, today.day + 1)
                if date(today.year, today.month, d).weekday() == target_wd
            )
            return count == n
        else:  # last occurrence: adding 7 days crosses into the next month
            return (today + timedelta(days=7)).month != today.month

    elif ctype == "nth_weekday_of_month_in_quarter":
        q = (today.month - 1) // 3
        target_month = q * 3 + int(condition.get("month_in_quarter", 1))
        if today.month != target_month:
            return False
        if n > 0:
            count = sum(
                1 for d in range(1, today.day + 1)
                if date(today.year, today.month, d).weekday() == target_wd
            )
            return count == n
        else:  # last occurrence in that month
            return (today + timedelta(days=7)).month != today.month

    elif ctype == "nth_weekday_of_quarter":
        # Legacy: nth weekday counting from the start of the quarter
        q = (today.month - 1) // 3
        q_end_month = q * 3 + 3
        q_end = date(today.year, q_end_month, monthrange(today.year, q_end_month)[1])
        if n > 0:
            q_start = date(today.year, q * 3 + 1, 1)
            count, d = 0, q_start
            while d <= today:
                if d.weekday() == target_wd:
                    count += 1
                d += timedelta(days=1)
            return count == n
        else:  # last occurrence: adding 7 days crosses into the next quarter
            return today + timedelta(days=7) > q_end

    return False


# ---------------------------------------------------------------------------
# Ticket-summary reader (inline here to avoid depending on route-level code)
# ---------------------------------------------------------------------------

def _read_ticket_summaries(open_issues: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for issue in open_issues:
        issue_id = issue.get("id", "")
        number = issue.get("number")
        if number is None:
            continue
        latest = issue.get("latest_message_time") or issue.get("updated_at") or ""
        raw = cache_mod.get_ticket_summary(issue_id, latest)
        if raw:
            s, ns = parse_ticket_output(raw)
            if s or ns:
                out[number] = {"summary": s, "next_steps": ns}
    return out


# ---------------------------------------------------------------------------
# Main graph node
# ---------------------------------------------------------------------------

async def send_report(state: ReportState) -> dict:
    account_id = state["account_id"]
    account_name = state["account_name"]
    period = state.get("period") or "6m"
    if period not in VALID_PERIODS:
        period = "6m"
    destination_type = state.get("destination_type", "slack")
    channel_id = state.get("channel_id")
    email_addresses = state.get("email_addresses") or []
    sections = state.get("sections")
    run_condition = state.get("run_condition")

    if not _should_run(run_condition):
        return {"skipped": True, "result": "Skipped: run condition not met for today"}

    # Fetch data from Pylon
    try:
        created_after, created_before = pylon_client.make_date_range(period)
        field_labels, open_issues, period_issues, csat_responses = await asyncio.gather(
            asyncio.to_thread(pylon_client.get_issue_field_labels),
            asyncio.to_thread(pylon_client.search_issues_for_account, account_id, OPEN_STATES),
            asyncio.to_thread(
                pylon_client.search_issues_for_account,
                account_id, None, created_after, created_before,
            ),
            asyncio.to_thread(pylon_client.get_csat_responses_for_account, account_id),
        )
    except Exception as exc:
        return {"skipped": False, "result": None, "error": f"Failed to fetch Pylon data: {exc}"}

    payload = _build_payload(field_labels, open_issues, period_issues, csat_responses, period, account_id)
    ticket_summaries = await asyncio.to_thread(_read_ticket_summaries, open_issues)
    account_summary = await asyncio.to_thread(cache_mod.get_account_summary, account_id, period)
    sections_set = set(sections) if sections is not None else None

    # --- Slack ---
    if destination_type == "slack":
        slack_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
        if not slack_token:
            return {"skipped": False, "result": None, "error": "SLACK_BOT_TOKEN not configured"}

        override_channel = os.environ.get("SLACK_OVERRIDE_CHANNEL", "").strip()
        target_channel = override_channel or channel_id
        if not target_channel:
            account = await asyncio.to_thread(pylon_client.get_account, account_id)
            if account:
                target_channel = pylon_client.get_slack_channel_id(account)
        if not target_channel:
            return {"skipped": False, "result": None, "error": "No Slack channel configured"}

        fallback_text, blocks = _build_metrics_blocks(
            account_id, account_name, payload, period, sections=sections_set
        )
        try:
            await asyncio.to_thread(
                slack_client.post_message, slack_token, target_channel, fallback_text, blocks
            )
        except Exception as exc:
            return {"skipped": False, "result": None, "error": f"Slack post failed: {exc}"}
        return {"skipped": False, "result": f"Sent to Slack channel {target_channel}", "error": None}

    # --- Email ---
    elif destination_type == "email":
        if not email_addresses:
            return {"skipped": False, "result": None, "error": "No email addresses configured"}

        smtp_host = os.environ.get("SMTP_HOST", "")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_password = os.environ.get("SMTP_PASSWORD", "")
        smtp_from = os.environ.get("SMTP_FROM", smtp_user)
        if not smtp_host or not smtp_user or not smtp_password:
            return {"skipped": False, "result": None, "error": "SMTP not configured"}

        html = generate_report_html(
            account_name=account_name,
            period=period,
            payload=payload,
            ticket_summaries=ticket_summaries,
            account_summary=account_summary,
            sort_by="priority",
            sort_order="asc",
            logo_url=os.environ.get("REPORT_LOGO_URL") or None,
            is_email=True,
            banner_url=os.environ.get("REPORT_BANNER_URL") or None,
            sections=sections_set,
        )

        def _send():
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Support Highlights: {account_name}"
            msg["From"] = smtp_from
            msg["To"] = ", ".join(email_addresses)
            msg["X-PM-Message-Stream"] = "support-highlights"
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_from, email_addresses, msg.as_string())

        try:
            await asyncio.to_thread(_send)
        except Exception as exc:
            return {"skipped": False, "result": None, "error": f"Email send failed: {exc}"}
        return {
            "skipped": False,
            "result": f"Sent to {', '.join(email_addresses)}",
            "error": None,
        }

    return {"skipped": False, "result": None, "error": f"Unknown destination type: {destination_type}"}


# ---------------------------------------------------------------------------
# Graph definition
# ---------------------------------------------------------------------------

_builder = StateGraph(ReportState)
_builder.add_node("send_report", send_report)
_builder.set_entry_point("send_report")
_builder.add_edge("send_report", END)
report_dispatcher = _builder.compile()
