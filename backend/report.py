"""HTML report generator for Premium Support Highlights.

Generates a self-contained, print-optimised HTML report that can be:
  - Opened in a browser tab and printed to PDF via Cmd+P / Ctrl+P
  - Sent as an HTML email body (all styles are inlined)

Usage:
    from report import generate_report_html
    html = generate_report_html(account_name, period, payload, ticket_summaries)
"""

from __future__ import annotations

import html as _html
import math
from datetime import datetime, timezone
from typing import Any

import markdown as _markdown


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

_PERIOD_LABELS: dict[str, str] = {
    "7d": "Last 7 Days",
    "1m": "Last Month",
    "3m": "Last 3 Months",
    "6m": "Last 6 Months",
    "1y": "Last Year",
}

_PRIORITY_LABELS: dict[str, str] = {
    "urgent": "Sev 1",
    "high":   "Sev 2",
    "medium": "Sev 3",
    "low":    "Sev 4",
    "none":   "None",
}

_PRIORITY_COLORS: dict[str, tuple[str, str]] = {
    # (background, text)
    "urgent": ("#fee2e2", "#b91c1c"),
    "high":   ("#ffedd5", "#c2410c"),
    "medium": ("#fef9c3", "#a16207"),
    "low":    ("#f1f5f9", "#475569"),
    "none":   ("#f1f5f9", "#475569"),
}

_STATE_LABELS: dict[str, str] = {
    "new":                  "New",
    "waiting_on_you":       "Waiting on LangChain",
    "on_hold":              "On Hold",
    "waiting_on_customer":  "Waiting on Customer",
    "closed":               "Closed",
    "resolved":             "Resolved",
}

_STATE_COLORS: dict[str, tuple[str, str]] = {
    "new":                 ("#dbeafe", "#1d4ed8"),
    "waiting_on_you":      ("#fce7f3", "#9d174d"),
    "on_hold":             ("#f3f4f6", "#374151"),
    "waiting_on_customer": ("#fef9c3", "#a16207"),
    "closed":              ("#f0fdf4", "#15803d"),
    "resolved":            ("#f0fdf4", "#15803d"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _e(text: Any) -> str:
    """HTML-escape a value."""
    return _html.escape(str(text) if text is not None else "")


def _badge(label: str, bg: str, color: str) -> str:
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;'
        f'font-size:11px;font-weight:600;background:{bg};color:{color};">'
        f"{_e(label)}</span>"
    )


def _priority_badge(priority: str) -> str:
    label = _PRIORITY_LABELS.get(priority, priority)
    bg, color = _PRIORITY_COLORS.get(priority, ("#f1f5f9", "#475569"))
    return _badge(label, bg, color)


def _state_badge(state: str) -> str:
    label = _STATE_LABELS.get(state, state.replace("_", " ").title())
    bg, color = _STATE_COLORS.get(state, ("#f1f5f9", "#374151"))
    return _badge(label, bg, color)


def _metric_card(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div style="font-size:12px;color:#6b7280;margin-top:2px;">{_e(sub)}</div>' if sub else ""
    return f"""
    <div style="border:1px solid #e5e7eb;border-radius:8px;padding:16px 20px;">
      <div style="font-size:11px;font-weight:600;letter-spacing:0.05em;
                  text-transform:uppercase;color:#6b7280;margin-bottom:6px;">
        {_e(label)}
      </div>
      <div style="font-size:26px;font-weight:700;color:#111827;">{_e(value)}</div>
      {sub_html}
    </div>"""


def _section_heading(title: str) -> str:
    return (
        f'<h2 style="font-size:13px;font-weight:700;letter-spacing:0.06em;'
        f'text-transform:uppercase;color:#374151;margin:32px 0 12px;">'
        f"{_e(title)}</h2>"
    )


def _days_open(created_at: str) -> str:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - created).days
        return f"{days}d"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_metrics(payload: dict, period_label: str) -> str:
    open_count   = len(payload.get("open_issues", []))
    monthly      = payload.get("monthly_metrics", [])
    total_raised = sum(m["tickets_raised"] for m in monthly)
    total_closed = sum(m["closed_tickets"] for m in monthly)
    avg_rt       = payload.get("avg_response_time")
    csat         = payload.get("csat")

    cards = [
        _metric_card("Open Issues", str(open_count)),
        _metric_card(f"Tickets Raised", str(total_raised), period_label),
        _metric_card(f"Tickets Closed", str(total_closed), period_label),
    ]
    if avg_rt is not None:
        cards.append(_metric_card("Avg Response Time", f"{avg_rt:.1f} hrs"))
    if csat is not None:
        cards.append(_metric_card("CSAT", f"{csat:.2f} / 5.0"))

    cols = min(len(cards), 4)
    grid = (
        f'<div style="display:grid;grid-template-columns:repeat({cols},1fr);gap:12px;">'
        + "".join(cards)
        + "</div>"
    )
    return grid


def _render_trend(monthly: list[dict]) -> str:
    if not monthly:
        return ""

    W, H       = 800, 200
    PAD_L      = 36   # left  (y-axis labels)
    PAD_R      = 16
    PAD_T      = 12
    PAD_B      = 32   # bottom (x-axis labels)
    chart_w    = W - PAD_L - PAD_R
    chart_h    = H - PAD_T - PAD_B

    max_val    = max((max(m["tickets_raised"], m["closed_tickets"]) for m in monthly), default=1)
    max_val    = max(max_val, 1)
    # Round up to a nice ceiling for y-axis
    y_top      = math.ceil(max_val / 5) * 5 or 5

    n          = len(monthly)
    group_w    = chart_w / n
    bar_w      = max(group_w * 0.28, 4)
    gap        = group_w * 0.08

    def y(val: int) -> float:
        return PAD_T + chart_h - (val / y_top) * chart_h

    def x_center(i: int) -> float:
        return PAD_L + i * group_w + group_w / 2

    # Y-axis grid lines and labels (5 steps)
    grid_lines = []
    for step in range(6):
        val    = round(y_top * step / 5)
        cy     = y(val)
        grid_lines.append(
            f'<line x1="{PAD_L}" y1="{cy:.1f}" x2="{W - PAD_R}" y2="{cy:.1f}" '
            f'stroke="#f3f4f6" stroke-width="1"/>'
        )
        grid_lines.append(
            f'<text x="{PAD_L - 6}" y="{cy + 4:.1f}" text-anchor="end" '
            f'font-size="10" fill="#9ca3af">{val}</text>'
        )

    # Bars + x labels
    bars   = []
    labels = []
    for i, m in enumerate(monthly):
        cx    = x_center(i)
        raised = m["tickets_raised"]
        closed = m["closed_tickets"]

        # Raised bar (blue)
        bh_r  = max((raised / y_top) * chart_h, 1) if raised else 0
        bx_r  = cx - bar_w - gap / 2
        bars.append(
            f'<rect x="{bx_r:.1f}" y="{y(raised):.1f}" width="{bar_w:.1f}" '
            f'height="{bh_r:.1f}" rx="2" fill="#006ddd" opacity="0.85"/>'
        )

        # Closed bar (teal)
        bh_c  = max((closed / y_top) * chart_h, 1) if closed else 0
        bx_c  = cx + gap / 2
        bars.append(
            f'<rect x="{bx_c:.1f}" y="{y(closed):.1f}" width="{bar_w:.1f}" '
            f'height="{bh_c:.1f}" rx="2" fill="#10b981" opacity="0.85"/>'
        )

        # X label — abbreviated month (e.g. "Oct 2025" → "Oct")
        short = m["month"].split()[0] if " " in m["month"] else m["month"]
        labels.append(
            f'<text x="{cx:.1f}" y="{H - 6}" text-anchor="middle" '
            f'font-size="10" fill="#9ca3af">{_e(short)}</text>'
        )

    # Legend
    legend = (
        f'<rect x="{PAD_L}" y="{H + 8}" width="10" height="10" rx="2" fill="#006ddd" opacity="0.85"/>'
        f'<text x="{PAD_L + 14}" y="{H + 17}" font-size="11" fill="#6b7280">Raised</text>'
        f'<rect x="{PAD_L + 64}" y="{H + 8}" width="10" height="10" rx="2" fill="#10b981" opacity="0.85"/>'
        f'<text x="{PAD_L + 78}" y="{H + 17}" font-size="11" fill="#6b7280">Closed</text>'
    )

    svg_h = H + 28  # extra room for legend
    return (
        f'<div style="border:1px solid #e5e7eb;border-radius:8px;padding:16px 12px 8px;">'
        f'<svg width="100%" viewBox="0 0 {W} {svg_h}" '
        f'xmlns="http://www.w3.org/2000/svg" style="display:block;">'
        + "".join(grid_lines)
        + "".join(bars)
        + "".join(labels)
        + legend
        + "</svg></div>"
    )


def _render_breakdown(breakdown: dict[str, int], labels: dict[str, str] | None = None) -> str:
    if not breakdown:
        return '<p style="color:#9ca3af;font-size:13px;">No data</p>'
    total = sum(breakdown.values()) or 1
    rows = []
    for key, count in sorted(breakdown.items(), key=lambda x: -x[1]):
        label = (labels or {}).get(key, key.replace("_", " ").title()) if labels else key
        pct = count / total * 100
        rows.append(f"""
        <tr>
          <td style="padding:7px 12px;color:#374151;font-size:13px;">{_e(label)}</td>
          <td style="padding:7px 12px;text-align:right;color:#374151;font-size:13px;
                     font-weight:600;">{count}</td>
          <td style="padding:7px 12px;width:120px;">
            <div style="background:#f3f4f6;border-radius:4px;height:6px;">
              <div style="background:#006ddd;border-radius:4px;height:6px;
                          width:{pct:.0f}%;"></div>
            </div>
          </td>
        </tr>""")
    return f"""
    <table style="width:100%;border-collapse:collapse;">
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def _render_breakdowns(payload: dict) -> str:
    sections = []
    priority_bd = payload.get("priority_breakdown", {})
    state_bd    = payload.get("state_breakdown", {})
    disp_bd     = payload.get("disposition_breakdown", {})

    col_style = "flex:1;min-width:200px;"
    def _col(title: str, content: str) -> str:
        return (
            f'<div style="{col_style}border:1px solid #e5e7eb;border-radius:8px;'
            f'padding:16px;overflow:hidden;">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:0.06em;'
            f'text-transform:uppercase;color:#6b7280;margin-bottom:10px;">{_e(title)}</div>'
            f"{content}</div>"
        )

    cols = []
    if priority_bd:
        cols.append(_col("Priority", _render_breakdown(priority_bd, _PRIORITY_LABELS)))
    if state_bd:
        cols.append(_col("State", _render_breakdown(state_bd, _STATE_LABELS)))
    if disp_bd:
        cols.append(_col("Disposition", _render_breakdown(disp_bd)))

    if not cols:
        return ""
    return f'<div style="display:flex;gap:12px;flex-wrap:wrap;">{"".join(cols)}</div>'


def _md_to_html(text: str) -> str:
    """Convert markdown to HTML using the markdown library."""
    return _markdown.markdown(text, extensions=["nl2br", "sane_lists"])


def _render_summary(summary_text: str | None) -> str:
    if not summary_text:
        return ""
    return f"""
    <div style="background:#f0f7ff;border-left:3px solid #006ddd;
                border-radius:0 8px 8px 0;padding:16px 20px;">
      <div style="font-size:13px;line-height:1.7;color:#1e3a5f;">{_md_to_html(summary_text)}</div>
    </div>"""


_PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
_STATE_ORDER = {"waiting_on_you": 0, "new": 1, "on_hold": 2, "waiting_on_customer": 3}


def _sort_issues(issues: list[dict], sort_by: str, sort_order: str = "asc") -> list[dict]:
    reverse = sort_order == "desc"
    if sort_by == "state":
        return sorted(issues, key=lambda i: _STATE_ORDER.get(i.get("state", ""), 99), reverse=reverse)
    if sort_by == "created":
        return sorted(issues, key=lambda i: i.get("created_at", ""), reverse=reverse)
    # default: priority
    return sorted(issues, key=lambda i: _PRIORITY_ORDER.get(i.get("priority", ""), 99), reverse=reverse)


def _render_tickets(
    issues: list[dict],
    ticket_summaries: dict[int, str],
    sort_by: str = "priority",
    sort_order: str = "asc",
) -> str:
    if not issues:
        return '<p style="color:#9ca3af;font-size:13px;">No open issues.</p>'

    issues = _sort_issues(issues, sort_by, sort_order)

    rows = []
    for issue in issues:
        number   = issue.get("number", "")
        title    = issue.get("title", "")
        state    = issue.get("state", "")
        priority = issue.get("priority", "")
        created  = issue.get("created_at", "")
        disp     = issue.get("disposition", "")
        summary  = ticket_summaries.get(int(number), "") if number else ""

        age = _days_open(created)
        summary_html = (
            f'<div style="font-size:12px;color:#6b7280;margin-top:6px;line-height:1.5;">'
            f"{_e(summary)}</div>"
        ) if summary else ""
        disp_html = (
            f'<span style="font-size:11px;color:#9ca3af;margin-left:8px;">{_e(disp)}</span>'
        ) if disp else ""

        rows.append(f"""
        <tr style="border-top:1px solid #f3f4f6;">
          <td style="padding:12px;vertical-align:top;width:60px;">
            <span style="font-size:12px;font-weight:600;color:#9ca3af;">#{_e(number)}</span>
          </td>
          <td style="padding:12px;vertical-align:top;">
            <div style="font-size:13px;font-weight:600;color:#111827;">
              {_e(title)}{disp_html}
            </div>
            {summary_html}
          </td>
          <td style="padding:12px;vertical-align:top;white-space:nowrap;text-align:right;">
            <div style="margin-bottom:4px;">{_priority_badge(priority)}</div>
            <div style="margin-bottom:4px;">{_state_badge(state)}</div>
            <div style="font-size:11px;color:#9ca3af;">{_e(age)}</div>
          </td>
        </tr>""")

    return f"""
    <table style="width:100%;border-collapse:collapse;">
      <tbody>{"".join(rows)}</tbody>
    </table>"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_report_html(
    account_name: str,
    period: str,
    payload: dict,
    ticket_summaries: dict[int, str],
    account_summary: str | None = None,
    sort_by: str = "priority",
    sort_order: str = "asc",
) -> str:
    period_label = _PERIOD_LABELS.get(period, period)
    generated    = datetime.now().strftime("%-d %B %Y")
    open_issues  = payload.get("open_issues", [])
    monthly      = payload.get("monthly_metrics", [])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{_e(account_name)} — Support Highlights</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      background: #fff;
      color: #111827;
      font-size: 14px;
      line-height: 1.5;
    }}
    @media print {{
      body {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .page-break {{ page-break-before: always; }}
    }}
  </style>
</head>
<body>
  <div style="max-width:900px;margin:0 auto;padding:40px 32px;">

    <!-- Header -->
    <div style="display:flex;align-items:flex-start;justify-content:space-between;
                padding-bottom:24px;border-bottom:2px solid #006ddd;margin-bottom:32px;">
      <div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
          <svg width="22" height="22" viewBox="0 0 128 128" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M40.1024 85.0722C47.6207 77.5537 51.8469 67.3453 51.8469 56.7136C51.8469 46.0818 47.617 35.8734 40.1024 28.355L11.7446 0C4.22995 7.5185 0 17.7269 0 28.3586C0 38.9903 4.22995 49.1987 11.7446 56.7172L40.0987 85.0722H40.1024Z" fill="#006ddd"/>
            <path d="M99.4385 87.698C91.9239 80.1832 81.7121 75.9531 71.0844 75.9531C60.4566 75.9531 50.2448 80.1832 42.7266 87.698L71.0844 116.057C78.599 123.571 88.8107 127.802 99.4421 127.802C110.074 127.802 120.282 123.571 127.8 116.057L99.4421 87.698H99.4385Z" fill="#006ddd"/>
            <path d="M11.8146 115.987C19.3329 123.502 29.541 127.732 40.1724 127.732V87.6289H0.0664062C0.0700559 98.2606 4.29635 108.469 11.8146 115.987Z" fill="#006ddd"/>
            <path d="M110.387 45.7684C102.869 38.2535 92.6608 34.0198 82.0258 34.0234C71.3943 34.0234 61.1863 38.2535 53.668 45.772L82.0258 74.1306L110.387 45.7684Z" fill="#006ddd"/>
          </svg>
          <span style="font-size:11px;font-weight:600;letter-spacing:0.08em;
                       text-transform:uppercase;color:#006ddd;">
            Premium Support Highlights
          </span>
        </div>
        <h1 style="font-size:28px;font-weight:700;color:#111827;line-height:1.2;">
          {_e(account_name)}
        </h1>
      </div>
      <div style="text-align:right;font-size:12px;color:#6b7280;margin-top:4px;">
        <div>{_e(period_label)}</div>
        <div style="margin-top:2px;">Generated {_e(generated)}</div>
      </div>
    </div>

    <!-- Key Metrics -->
    {_section_heading("Key Metrics")}
    {_render_metrics(payload, period_label)}

    <!-- Monthly Trend -->
    {_section_heading("Monthly Trend")}
    {_render_trend(monthly)}

    <!-- Breakdowns -->
    {_section_heading("Breakdowns")}
    {_render_breakdowns(payload)}

    <!-- AI Account Summary -->
    {(_section_heading("AI Account Summary") + _render_summary(account_summary)) if account_summary else ""}

    <!-- Open Tickets -->
    {_section_heading(f"Open Issues ({len(open_issues)})")}
    {_render_tickets(open_issues, ticket_summaries, sort_by, sort_order)}

    <!-- Footer -->
    <div style="margin-top:48px;padding-top:16px;border-top:1px solid #e5e7eb;
                font-size:11px;color:#9ca3af;text-align:center;">
      Generated by Support Highlights &middot; Powered by Pylon + Claude
    </div>

  </div>
</body>
</html>"""
