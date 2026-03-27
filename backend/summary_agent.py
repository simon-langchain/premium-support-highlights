"""AI summary pipeline for the Premium Support Highlights dashboard.

Pipeline (orchestrated by main.py's POST /summary route):
  1. generate_account_summary() formats ticket and metric data into a structured prompt
  2. A deepagents agent (Claude Sonnet) receives the prompt and calls summarise_tickets()
  3. summarise_tickets() runs per-ticket Claude Haiku calls in parallel, caching to disk
  4. The agent writes a 2-paragraph customer-facing executive summary

The summarise_tickets tool is built by make_summarise_tickets_tool(), a factory that
captures the per-request open_issues list and force flag as a closure. This keeps tool
definition in the agent layer (here) rather than in the HTTP route handler (main.py).
"""

import asyncio
from datetime import datetime, timezone

import pylon_client
import cache as cache_mod
from langchain_core.tools import tool
from ticket_summarizer import summarize_ticket

DEFAULT_SUMMARY_MODEL = "claude-sonnet-4-6"

SUMMARY_SYSTEM_PROMPT = """You are preparing a monthly support highlights report to share directly with a premium customer.

Write a concise summary in exactly 2 paragraphs:

1. Trend overview: summarise ticket volume and closure rate over the period. Note that the most recent month will always appear to have a lower closure rate because tickets take time to resolve — do not treat this as a negative signal. Comment on overall health and any meaningful patterns across the full period.
2. Open ticket overview: call the summarise_tickets tool first to get the current state of every open ticket, then summarise them by category and severity focusing on what is actively being worked on. Only call out Sev 1 or Sev 2 bug fixes that have been open a long time. Feature requests are expected to remain open — acknowledge them positively as part of the product roadmap conversation.

Priority levels:
- Sev 1: Total outage or complete halt to production
- Sev 2: Severe degradation in production
- Sev 3: Partial or non-blocking issue
- Sev 4: Minor issues, questions, feature requests

Guidelines:
- Tone should be collaborative and customer-facing — written as if LangChain is updating the customer, not an internal team review
- Be concise — each paragraph should be 2-4 sentences
- Group tickets by category, not by raw tags
- Use markdown for formatting: **bold** for ticket titles or key terms, bullet points where listing multiple items aids readability
- Do not use headers — the summary is two paragraphs, not a structured document"""


def make_summarise_tickets_tool(open_issues: list[dict], force: bool):
    """Return the summarise_tickets tool bound to this request's open issues.

    Defined as a factory so the tool (and its captured context) lives in the
    agent layer rather than in the HTTP route handler.
    """
    @tool
    async def summarise_tickets() -> str:
        """Generate 1-2 sentence summaries for all open tickets in parallel.
        Call this before writing the account summary to have full context on each ticket.
        Returns a list of ticket summaries.
        """
        async def _one(issue: dict) -> tuple[int, str]:
            issue_id = issue.get("id", "")
            number = issue.get("number")
            latest_msg_time = issue.get("latest_message_time") or issue.get("updated_at") or ""
            if not force:
                cached = cache_mod.get_ticket_summary(issue_id, latest_msg_time)
                if cached:
                    return number, cached
            try:
                messages = await asyncio.to_thread(pylon_client.get_issue_messages, issue_id)
                summary = await summarize_ticket(
                    title=issue.get("title", ""),
                    body_html=issue.get("body_html", ""),
                    messages=messages,
                )
                if issue_id and latest_msg_time:
                    cache_mod.set_ticket_summary(issue_id, latest_msg_time, summary)
                return number, summary
            except Exception:
                return number, ""

        results = await asyncio.gather(*[_one(i) for i in open_issues])
        lines = [f"#{n}: {s}" for n, s in results if s]
        return "\n".join(lines) if lines else "No summaries available."

    return summarise_tickets


def create_summary_agent(model: str | None = None, tools: list | None = None):
    """Create a deepagent for account summary generation."""
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=model or DEFAULT_SUMMARY_MODEL,
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        name="support-highlights-summarizer",
        tools=tools or [],
    )


_STATE_LABELS = {
    "new": "New",
    "waiting_on_you": "Waiting on LangChain",
    "waiting_on_customer": "Waiting on Customer",
    "on_hold": "On Hold",
    "closed": "Closed",
    "resolved": "Resolved",
}

_PRIORITY_LABELS = {
    "urgent": "Sev 1",
    "high": "Sev 2",
    "medium": "Sev 3",
    "low": "Sev 4",
}


def _format_ticket(issue: dict) -> str:
    """Format a single issue as a concise text block for the agent."""
    number = issue.get("number", "?")
    title = issue.get("title", "No title")
    raw_state = issue.get("state", "unknown")
    state = _STATE_LABELS.get(raw_state, raw_state.replace("_", " ").title())
    raw_priority = issue.get("priority", "none")
    priority = _PRIORITY_LABELS.get(raw_priority, "No Priority")
    disposition = issue.get("disposition", "")
    tags = issue.get("tags") or []
    created_at = issue.get("created_at", "")
    days_open = ""
    if created_at:
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - created_dt
            days_open = f" ({delta.days}d open)"
        except (ValueError, TypeError):
            pass
    category_str = f" [{', '.join(str(t) for t in tags)}]" if tags else ""
    disposition_str = f" [{disposition}]" if disposition else ""
    return f"#{number} [state:{state}][priority:{priority}]{disposition_str}{days_open}{category_str}: {title}"


def _format_metrics(monthly_metrics: list[dict]) -> str:
    """Format monthly metrics as a compact text table for the agent."""
    if not monthly_metrics:
        return "No monthly metrics available."
    lines = ["Month          | Raised | Closed"]
    lines.append("-" * 38)
    for row in monthly_metrics:
        lines.append(
            f"{row['month']:<14} | {row['tickets_raised']:>6} | {row.get('closed_tickets', 0):>6}"
        )
    return "\n".join(lines)


_PERIOD_LABELS = {
    "7d": "7-Day",
    "1m": "1-Month",
    "3m": "3-Month",
    "6m": "6-Month",
    "1y": "12-Month",
}


_PRIORITY_ORDER = ["urgent", "high", "medium", "low"]
_STATE_ORDER = ["waiting_on_you", "new", "on_hold", "waiting_on_customer"]


def _format_key_metrics(
    avg_response_time: float | None,
    csat: float | None,
    priority_breakdown: dict,
    state_breakdown: dict,
    disposition_breakdown: dict,
) -> str:
    lines = []
    if avg_response_time is not None:
        lines.append(f"Avg first response time: {avg_response_time:.1f} hrs")
    if csat is not None:
        lines.append(f"CSAT score: {csat:.2f} / 5.0")
    if priority_breakdown:
        parts = ", ".join(
            f"{_PRIORITY_LABELS.get(p, p)}: {priority_breakdown[p]}"
            for p in _PRIORITY_ORDER
            if p in priority_breakdown
        )
        lines.append(f"Priority breakdown: {parts}")
    if state_breakdown:
        parts = ", ".join(
            f"{_STATE_LABELS.get(s, s)}: {state_breakdown[s]}"
            for s in _STATE_ORDER
            if s in state_breakdown
        )
        lines.append(f"State breakdown: {parts}")
    if disposition_breakdown:
        parts = ", ".join(f"{k}: {v}" for k, v in disposition_breakdown.items())
        lines.append(f"Disposition breakdown: {parts}")
    return "\n".join(lines) if lines else "No metrics available."


async def generate_account_summary(
    account_name: str,
    open_tickets: list[dict],
    monthly_metrics: list[dict],
    avg_response_time: float | None = None,
    csat: float | None = None,
    priority_breakdown: dict | None = None,
    state_breakdown: dict | None = None,
    disposition_breakdown: dict | None = None,
    model: str | None = None,
    period: str = "6m",
    tools: list | None = None,
) -> str:
    """Invoke the summary agent with formatted ticket and metrics context.

    Builds a structured text prompt from all inputs, then invokes the deepagents
    agent. The agent will call summarise_tickets() before writing its response to
    get fresh per-ticket context. Returns the final AI message as a plain string.
    """
    agent = create_summary_agent(model=model, tools=tools)

    ticket_lines = [_format_ticket(t) for t in open_tickets]
    ticket_section = "\n".join(ticket_lines) if ticket_lines else "No open tickets."
    metrics_section = _format_metrics(monthly_metrics)
    period_label = _PERIOD_LABELS.get(period, "6-Month")
    key_metrics_section = _format_key_metrics(
        avg_response_time,
        csat,
        priority_breakdown or {},
        state_breakdown or {},
        disposition_breakdown or {},
    )

    prompt = f"""Please generate an executive support highlights summary for account: **{account_name}**

## Key Metrics

{key_metrics_section}

## Open Tickets ({len(open_tickets)} total)

{ticket_section}

## {period_label} Ticket Trend

{metrics_section}

Generate a 2-paragraph executive summary following the format in the system prompt."""

    result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})

    # deepagents returns a LangGraph state dict. Extract the final AI message text,
    # which may be a plain string or a list of typed content blocks (text/tool_use).
    if isinstance(result, dict):
        messages = result.get("messages", [])
        if messages:
            last = messages[-1]
            if hasattr(last, "content"):
                content = last.content
                if isinstance(content, list):
                    parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
                    return "\n".join(p for p in parts if p).strip()
                return str(content).strip()
            elif isinstance(last, dict):
                return str(last.get("content", "")).strip()
    return str(result).strip()
