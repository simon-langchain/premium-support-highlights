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
import logging
import re
from datetime import datetime, timezone

from langsmith import traceable
import pylon_client
import cache as cache_mod
from langchain_core.tools import tool
from ticket_summarizer import summarize_ticket

_log = logging.getLogger(__name__)

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


def make_summarise_tickets_tool(open_issues: list[dict], force: bool, account_name: str = ""):
    """Return the summarise_tickets tool bound to this request's open issues.

    Defined as a factory so the tool (and its captured context) lives in the
    agent layer rather than in the HTTP route handler.
    """
    @tool
    async def summarise_tickets() -> str:
        """Generate next-steps actions for all open tickets in parallel.
        Call this before writing the account summary to have full context on each ticket.
        Returns a list of per-ticket next steps.
        """
        async def _one(issue: dict) -> tuple[int, str]:
            issue_id = issue.get("id", "")
            number = issue.get("number")
            latest_msg_time = issue.get("latest_message_time") or issue.get("updated_at") or ""
            if not force:
                cached = await asyncio.to_thread(cache_mod.get_ticket_summary, issue_id, latest_msg_time)
                if cached:
                    return number, cached
            try:
                messages = await asyncio.to_thread(pylon_client.get_issue_messages, issue_id)
                summary = await summarize_ticket(
                    title=issue.get("title", ""),
                    body_html=issue.get("body_html", ""),
                    messages=messages,
                    state=issue.get("state", ""),
                    account_name=account_name,
                )
                if issue_id:
                    await asyncio.to_thread(cache_mod.set_ticket_summary, issue_id, latest_msg_time, summary)
                return number, summary
            except Exception:
                _log.exception("Failed to summarise ticket #%s (id=%s)", number, issue_id)
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


def _state_labels(account_name: str = "") -> dict[str, str]:
    return {
        "new": "New",
        "waiting_on_you": "Waiting on LangChain",
        "waiting_on_customer": f"Waiting on {account_name}" if account_name else "Waiting on Customer",
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
    state = _state_labels().get(raw_state, raw_state.replace("_", " ").title())
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
    account_name: str = "",
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
        labels = _state_labels(account_name)
        parts = ", ".join(
            f"{labels.get(s, s)}: {state_breakdown[s]}"
            for s in _STATE_ORDER
            if s in state_breakdown
        )
        lines.append(f"State breakdown: {parts}")
    if disposition_breakdown:
        parts = ", ".join(f"{k}: {v}" for k, v in disposition_breakdown.items())
        lines.append(f"Disposition breakdown: {parts}")
    return "\n".join(lines) if lines else "No metrics available."


@traceable(name="generate_qbr_insights", run_type="llm")
async def generate_qbr_insights(
    account_name: str,
    quarter_label: str,
    open_issues: list[dict],
    sev1: int,
    sev2: int,
    waiting_on_you: int,
    total_open: int,
    fr_open: int,
    sla_pct: int | None,
    avg_response_hours: float | None,
    tickets_raised_qtr: int,
    tickets_closed_qtr: int,
) -> dict[str, list[str]]:
    """Generate QBR Observations and Opportunities bullet points using Claude."""
    import json
    from anthropic import AsyncAnthropic

    _PRIORITY_MAP = {"urgent": "Sev1", "high": "Sev2", "medium": "Sev3", "low": "Sev4"}

    ticket_lines = []
    for i in open_issues[:20]:
        cf = i.get("custom_fields") or {}
        raw_p = (cf.get("priority") or {}).get("value", "low") if isinstance(cf, dict) else "low"
        p = _PRIORITY_MAP.get(raw_p, "Sev4")
        title = (i.get("title") or "")[:80]
        ticket_lines.append(f"  #{i.get('number', '?')} [{p}][{i.get('state', '')}]: {title}")

    metrics_lines = [
        f"Quarter: {quarter_label}",
        f"Tickets raised this quarter: {tickets_raised_qtr}",
        f"Tickets closed this quarter: {tickets_closed_qtr}",
        f"Currently open: {total_open} total ({sev1} Sev 1, {sev2} Sev 2)",
        f"Pending LangChain action: {waiting_on_you}",
        f"Open feature requests: {fr_open}",
    ]
    if sla_pct is not None:
        metrics_lines.append(f"Response time SLA compliance (within 24h): {sla_pct}%")
    if avg_response_hours is not None:
        metrics_lines.append(f"Avg first response time: {avg_response_hours:.1f}h")

    prompt = f"""QBR slide bullets for {account_name} {quarter_label} Enterprise Support.

Metrics:
{chr(10).join(metrics_lines)}

Open tickets:
{chr(10).join(ticket_lines) if ticket_lines else "  (none)"}

Return ONLY valid JSON (no markdown, no code block):
{{"observations": ["...", "...", "..."], "opportunities": ["..."]}}

Rules:
- observations: exactly 3 bullets — factual snapshot, specific numbers, highlight what's going well
- opportunities: 1–3 bullets — meaningful ways to deepen the support relationship or unlock more value for {account_name}; think things like: expanding usage, unblocking a strategic initiative, reducing a recurring pain point, making integrations more robust. NOT generic operational tasks like "schedule follow-ups" or "clear backlog"
- Each bullet: 6-12 words MAX, terse slide-style fragment (not a full sentence)
- Tone: customer-facing, positively framed — this is read by {account_name} in a QBR. Frame as a partnership
- Only flag serious issues (Sev 1 open, SLA breach) directly; everything else should be constructive and forward-looking
- No filler words, no "LangChain should", no em dashes"""

    client = AsyncAnthropic()
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if Claude wraps the JSON
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
    try:
        data = json.loads(raw)
        return {
            "observations": data.get("observations", []),
            "opportunities": data.get("opportunities", []),
        }
    except Exception:
        _log.warning("Failed to parse QBR insights JSON: %s", raw[:200])
        return {"observations": [], "opportunities": []}


@traceable(name="generate_usage_insights", run_type="llm")
async def generate_usage_insights(
    account_name: str,
    quarter_label: str,
    chart_data: dict,
    maturity_data: list[dict] | None,
) -> dict:
    """Generate per-slide LangSmith usage observations and opportunities from BQ chart data.

    Generates tailored bullets for 3 LangSmith Usage slides in a single call so the
    model can keep them aligned:
      Slide 22 — Commit usage (contract KPIs: % into contract, % commit used)
      Slide 23 — LangSmith usage (traces, agent runs, page views, evaluator totals)
      Slide 24 — Feature adoption (experiments, Prompt Hub, datasets, evaluator rules)
    """
    import json
    from anthropic import AsyncAnthropic

    monthly = chart_data.get("monthly_usage", [])
    page_views_data = chart_data.get("page_views", [])
    evaluators = chart_data.get("evaluator_usage", [])
    contract = chart_data.get("contract_metrics") or {}
    enablement = chart_data.get("enablement_stats") or {}

    recent = monthly[-3:] if monthly else []
    seats = int(enablement.get("billable_seats") or 0)

    # Slide 22 — Commit Usage: contract KPI tiles + cumulative trace chart
    s22_lines = []
    if contract.get("pct_into_contract") is not None:
        pct_into = float(contract["pct_into_contract"]) * 100
        pct_used = float(contract.get("pct_commit_used") or 0) * 100
        s22_lines.append(f"  {pct_into:.0f}% through contract period, {pct_used:.0f}% of commit used")
        pace = pct_used - pct_into
        if abs(pace) >= 5:
            direction = "ahead of" if pace > 0 else "behind"
            s22_lines.append(f"  Commit pace: {abs(pace):.0f}pp {direction} contract timeline")
        if contract.get("contract_end_date"):
            s22_lines.append(f"  Contract ends: {contract['contract_end_date']}")
    if recent:
        last = recent[-1]
        total_traces = int(last.get("actual_traces") or 0)
        s22_lines.append(f"  Most recent month traces: {total_traces:,}")
    slide22_block = "Contract & commit usage:\n" + "\n".join(s22_lines) if s22_lines else "(no contract data)"

    # Slide 23 — LangSmith Usage: traces, agent runs, page views, evaluator totals
    s23_lines = []
    for r in recent:
        month = str(r.get("month_start", "?"))[:7]
        traces = int(r.get("actual_traces") or 0)
        agents = int(r.get("actual_agent_runs") or 0)
        s23_lines.append(f"  {month}: traces={traces:,}, agent_runs={agents:,}")
    pv_lines = []
    for r in page_views_data[-3:]:
        month = str(r.get("event_month", "?"))[:7]
        pv = int(r.get("total_page_views") or 0)
        pv_lines.append(f"  {month}: {pv:,} page views")
    eval_totals: dict[str, int] = {}
    for r in evaluators:
        cat = r.get("eval_category", "Unknown")
        eval_totals[cat] = eval_totals.get(cat, 0) + int(r.get("rules") or 0)
    total_eval_rules = sum(eval_totals.values())
    s23_parts = []
    if s23_lines:
        s23_parts.append("Monthly traces & agent runs (last 3 months):\n" + "\n".join(s23_lines))
    if pv_lines:
        s23_parts.append("LangSmith page views (last 3 months):\n" + "\n".join(pv_lines))
    if total_eval_rules:
        s23_parts.append(f"Total evaluator rules (12-month): {total_eval_rules:,}")
    if seats:
        s23_parts.append(f"Active LangSmith users (MAU): {seats}")
    slide23_block = "\n\n".join(s23_parts) if s23_parts else "(no usage data)"

    # Slide 24 — Feature Adoption: experiments, Prompt Hub, datasets, evaluator rules by category
    s24_lines = []
    for r in recent:
        month = str(r.get("month_start", "?"))[:7]
        experiments = int(r.get("total_experiments") or 0)
        commits = int(r.get("total_prompt_commits") or 0)
        pulls = int(r.get("total_prompt_pulls") or 0)
        datasets = int(r.get("total_datasets") or 0)
        s24_lines.append(
            f"  {month}: experiments={experiments}, prompt_commits={commits},"
            f" prompt_pulls={pulls}, datasets={datasets}"
        )
    eval_lines = [f"  {cat}: {cnt:,} rules" for cat, cnt in sorted(eval_totals.items())]
    s24_parts = []
    if s24_lines:
        s24_parts.append("Feature usage (last 3 months):\n" + "\n".join(s24_lines))
    if eval_lines:
        s24_parts.append("Evaluator rules by type (12-month totals):\n" + "\n".join(eval_lines))
    slide24_block = "\n\n".join(s24_parts) if s24_parts else "(no feature adoption data)"

    prompt = f"""You are writing content for 3 LangSmith Usage slides in a QBR with {account_name} ({quarter_label}).

Each slide shows different charts. Write bullets tailored to each slide's specific data.
Bullets across slides must be aligned: no contradictions, no repeating the same point on multiple slides.

---
SLIDE 22 — Commit Usage
Charts: cumulative trace count over contract period, 4 KPI tiles (contract end date, % into contract period, % commit used, total traces)
{slide22_block}

---
SLIDE 23 — LangSmith Usage
Charts: monthly traces, monthly agent runs, LangSmith page views, total evaluator rule count
{slide23_block}

---
SLIDE 24 — Feature Adoption
Charts: monthly experiments, Prompt Hub commits/pulls, datasets, evaluator rules broken down by type
{slide24_block}

---
Generate:

1. headline: ONE sentence, max 15 words, summarising overall LangSmith usage. Lead with what's working; note the biggest gap or opportunity.

2. For EACH slide, write observations (1-3 bullets) and opportunities (1-3 bullets):
   - observations: factual snapshot of what the data shows on that specific slide only
   - opportunities: how to deepen value — specific to each slide's feature area:
       Slide 22: commit pacing risk, contract value realisation, usage trajectory vs renewal
       Slide 23: expanding tracing coverage, agent observability, growing active user base
       Slide 24: unused features (Playground, Online Evals, Experiments, Prompt Hub, Datasets), evaluator adoption

Rules:
- STRICT 8-word max per bullet — count every word
- Terse slide fragments only — no full sentences
- No em dashes, no "LangChain", no filler words
- Customer-facing, constructive, positively framed
- Only flag serious issues (Sev 1, SLA breach, commit severely off-pace) directly; everything else forward-looking
- Academy/training topics belong on the Enablement slide, not here

Return ONLY valid JSON (no markdown, no code block):
{{"headline": "...", "slide22": {{"observations": ["...", "..."], "opportunities": ["..."]}}, "slide23": {{"observations": ["...", "..."], "opportunities": ["..."]}}, "slide24": {{"observations": ["...", "..."], "opportunities": ["..."]}}}}"""

    client = AsyncAnthropic()
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
    try:
        data = json.loads(raw)
        s22 = data.get("slide22", {})
        s23 = data.get("slide23", {})
        s24 = data.get("slide24", {})
        return {
            "usage_headline": data.get("headline", ""),
            "commit_observations": s22.get("observations", []),
            "commit_opportunities": s22.get("opportunities", []),
            "tracing_observations": s23.get("observations", []),
            "tracing_opportunities": s23.get("opportunities", []),
            "feature_observations": s24.get("observations", []),
            "feature_opportunities": s24.get("opportunities", []),
        }
    except Exception:
        _log.warning("Failed to parse usage insights JSON: %s", raw[:200])
        return {
            "usage_headline": "",
            "commit_observations": [], "commit_opportunities": [],
            "tracing_observations": [], "tracing_opportunities": [],
            "feature_observations": [], "feature_opportunities": [],
        }


# Module-level compiled graph registered with LSD via langgraph.json.
# Tools (summarise_tickets) are bound per-request via the HTTP route in main.py;
# this default instance has no tools but satisfies LSD's required `graphs` entry.
summary_graph = create_summary_agent()


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
        account_name=account_name,
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
