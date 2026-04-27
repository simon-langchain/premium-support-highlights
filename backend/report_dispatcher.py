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
    _do_qbr_generation,
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
    destination_type: str                # "slack" | "email" | "qbr"
    channel_id: Optional[str]           # Slack channel ID
    email_addresses: Optional[list[str]]
    # QBR notification fields
    qbr_notify_type: Optional[str]       # "slack" | "email"
    qbr_notify_channel_id: Optional[str]
    qbr_notify_emails: Optional[list[str]]
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

    # --- QBR Slides ---
    elif destination_type == "qbr":
        if not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
            return {"skipped": False, "result": None, "error": "QBR slides not configured (GOOGLE_SERVICE_ACCOUNT_JSON missing)"}

        qbr_notify_type = state.get("qbr_notify_type")
        qbr_notify_channel_id = state.get("qbr_notify_channel_id")
        qbr_notify_emails = state.get("qbr_notify_emails") or []

        try:
            _pres_id, slide_url, month_label = await _do_qbr_generation(account_id, account_name)
        except Exception as exc:
            return {"skipped": False, "result": None, "error": f"QBR generation failed: {exc}"}

        # Send notification with slide link
        if qbr_notify_type == "slack":
            slack_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
            if not slack_token:
                return {"skipped": False, "result": f"QBR slides generated: {slide_url}", "error": "SLACK_BOT_TOKEN not configured for notification"}
            override_channel = os.environ.get("SLACK_OVERRIDE_CHANNEL", "").strip()
            notify_channel = override_channel or qbr_notify_channel_id
            if not notify_channel:
                return {"skipped": False, "result": f"QBR slides generated: {slide_url}", "error": "No Slack channel configured for QBR notification"}
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*QBR Slides ready — {account_name}*\n{month_label} · <{slide_url}|Open slides>"}},
            ]
            try:
                await asyncio.to_thread(
                    slack_client.post_message, slack_token, notify_channel,
                    f"QBR Slides ready for {account_name}: {slide_url}", blocks,
                )
            except Exception as exc:
                return {"skipped": False, "result": f"QBR slides generated: {slide_url}", "error": f"Slack notification failed: {exc}"}
            return {"skipped": False, "result": f"QBR slides generated and notification sent to #{notify_channel}: {slide_url}", "error": None}

        elif qbr_notify_type == "email":
            if not qbr_notify_emails:
                return {"skipped": False, "result": f"QBR slides generated: {slide_url}", "error": "No email addresses configured for QBR notification"}
            smtp_host = os.environ.get("SMTP_HOST", "")
            smtp_port = int(os.environ.get("SMTP_PORT", "587"))
            smtp_user = os.environ.get("SMTP_USER", "")
            smtp_password = os.environ.get("SMTP_PASSWORD", "")
            smtp_from = os.environ.get("SMTP_FROM", smtp_user)
            if not smtp_host or not smtp_user or not smtp_password:
                return {"skipped": False, "result": f"QBR slides generated: {slide_url}", "error": "SMTP not configured for QBR notification"}

            banner_url = os.environ.get("REPORT_BANNER_URL") or None
            logo_url   = os.environ.get("REPORT_LOGO_URL") or None
            year       = date.today().year
            logo_html  = (
                f'<img src="{logo_url}" width="22" height="22" alt="LangChain" style="display:block;">'
                if logo_url else
                '<svg width="22" height="22" viewBox="0 0 128 128" fill="none" xmlns="http://www.w3.org/2000/svg">'
                '<path d="M40.1024 85.0722C47.6207 77.5537 51.8469 67.3453 51.8469 56.7136C51.8469 46.0818 47.617 35.8734 40.1024 28.355L11.7446 0C4.22995 7.5185 0 17.7269 0 28.3586C0 38.9903 4.22995 49.1987 11.7446 56.7172L40.0987 85.0722H40.1024Z" fill="#006ddd"/>'
                '<path d="M99.4385 87.698C91.9239 80.1832 81.7121 75.9531 71.0844 75.9531C60.4566 75.9531 50.2448 80.1832 42.7266 87.698L71.0844 116.057C78.599 123.571 88.8107 127.802 99.4421 127.802C110.074 127.802 120.282 123.571 127.8 116.057L99.4421 87.698H99.4385Z" fill="#006ddd"/></svg>'
            )
            banner_html = (
                f'<tr><td style="padding:0;"><img src="{banner_url}" alt="LangChain" width="660"'
                f' style="display:block;width:100%;max-width:660px;height:auto;border:0;"></td></tr>'
                if banner_url else ""
            )
            html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>QBR Slides ready: {account_name}</title></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background-color:#f8f7ff;">
  <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color:#f8f7ff;">
    <tr><td align="center">
      <table border="0" cellpadding="0" cellspacing="0" width="660" style="max-width:660px;width:100%;background:#ffffff;">
        {banner_html}
        <tr>
          <td style="padding:24px 28px;font-size:14px;line-height:1.5;color:#111827;">
            <!-- Header -->
            <table style="width:100%;border-collapse:collapse;padding-bottom:24px;border-bottom:2px solid #006ddd;margin-bottom:28px;">
              <tr>
                <td style="vertical-align:middle;padding-bottom:20px;">
                  <table style="border-collapse:collapse;margin-bottom:10px;">
                    <tr>
                      <td style="vertical-align:middle;padding-right:8px;">{logo_html}</td>
                      <td style="vertical-align:middle;font-size:11px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:#006ddd;">QBR Slides</td>
                    </tr>
                  </table>
                  <div style="font-size:24px;font-weight:700;color:#111827;line-height:1.2;">{account_name}</div>
                  <div style="font-size:13px;color:#6b7280;margin-top:4px;">{month_label}</div>
                </td>
              </tr>
            </table>
            <!-- Body -->
            <p style="font-size:14px;color:#374151;margin:0 0 24px;">Your QBR slides are ready. Click below to open them in Google Slides.</p>
            <table border="0" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
              <tr>
                <td style="border-radius:6px;background:#006ddd;">
                  <a href="{slide_url}" style="display:inline-block;padding:10px 22px;font-size:14px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:6px;">Open slides &rarr;</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr><td style="padding:10px 20px;"><hr style="border:0;border-top:2px solid #000;"></td></tr>
        <tr>
          <td align="center" style="padding:10px;font-size:12px;background-color:#f8f7ff;color:#333;">
            <em>Copyright &copy; {year} LangChain. All rights reserved.</em>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

            def _send_qbr_email():
                msg = MIMEMultipart("alternative")
                msg["Subject"] = f"QBR Slides ready: {account_name} — {month_label}"
                msg["From"] = smtp_from
                msg["To"] = ", ".join(qbr_notify_emails)
                msg["X-PM-Message-Stream"] = "support-highlights"
                msg.attach(MIMEText(html, "html"))
                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.ehlo()
                    server.starttls()
                    server.login(smtp_user, smtp_password)
                    server.sendmail(smtp_from, qbr_notify_emails, msg.as_string())

            try:
                await asyncio.to_thread(_send_qbr_email)
            except Exception as exc:
                return {"skipped": False, "result": f"QBR slides generated: {slide_url}", "error": f"Email notification failed: {exc}"}
            return {"skipped": False, "result": f"QBR slides generated and notification sent to {', '.join(qbr_notify_emails)}: {slide_url}", "error": None}

        # No notification configured — slides generated, no notification sent
        return {"skipped": False, "result": f"QBR slides generated: {slide_url}", "error": None}

    return {"skipped": False, "result": None, "error": f"Unknown destination type: {destination_type}"}


# ---------------------------------------------------------------------------
# Graph definition
# ---------------------------------------------------------------------------

_builder = StateGraph(ReportState)
_builder.add_node("send_report", send_report)
_builder.set_entry_point("send_report")
_builder.add_edge("send_report", END)
report_dispatcher = _builder.compile()
