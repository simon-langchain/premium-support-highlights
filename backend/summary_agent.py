"""AI summary pipeline for the Premium Support Highlights dashboard.

Pipeline (orchestrated by main.py's POST /summary route):
  1. generate_account_summary() formats ticket and metric data into a structured prompt
  2. A deepagents agent receives the prompt and calls summarise_tickets()
  3. summarise_tickets() runs per-ticket LLM calls in parallel, caching to disk
  4. The agent writes a 2-paragraph customer-facing executive summary

All LLM calls route through the LangSmith LLM Gateway via the centralized
client and model factory in llm.py.

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
from llm import get_chat_model, DEFAULT_MODEL_ID

_log = logging.getLogger(__name__)

DEFAULT_SUMMARY_MODEL = DEFAULT_MODEL_ID
DEFAULT_QBR_MODEL = "anthropic:claude-haiku-4-5-20251001"

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


def make_summarise_tickets_tool(open_issues: list[dict], force: bool, account_name: str = "", model: str = ""):
    """Return the summarise_tickets tool bound to this request's open issues.

    Defined as a factory so the tool (and its captured context) lives in the
    agent layer rather than in the HTTP route handler.
    """
    ticket_model = model or DEFAULT_SUMMARY_MODEL

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
                cached = await asyncio.to_thread(cache_mod.get_ticket_summary, issue_id, latest_msg_time, ticket_model)
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
                    model=ticket_model,
                )
                if issue_id:
                    await asyncio.to_thread(cache_mod.set_ticket_summary, issue_id, latest_msg_time, summary, ticket_model)
                return number, summary
            except Exception:
                _log.exception("Failed to summarise ticket #%s (id=%s)", number, issue_id)
                return number, ""

        results = await asyncio.gather(*[_one(i) for i in open_issues])
        lines = [f"#{n}: {s}" for n, s in results if s]
        return "\n".join(lines) if lines else "No summaries available."

    return summarise_tickets


def create_summary_agent(model: str | None = None, tools: list | None = None):
    """Create a deepagent for account summary generation.

    The model string (a registry ID like 'anthropic:claude-sonnet-4-6') is
    resolved to a BaseChatModel via the gateway before being passed to
    create_deep_agent, so the agent always routes through the gateway.
    """
    from deepagents import create_deep_agent

    chat_model = get_chat_model(model or DEFAULT_SUMMARY_MODEL)

    return create_deep_agent(
        model=chat_model,
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
    model: str | None = None,
) -> dict[str, list[str]]:
    """Generate QBR Observations and Opportunities bullet points via the gateway."""
    import json

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
- Tone: lean positive — this is read by {account_name} in a QBR, frame as a partnership and lead with genuine wins. Do not sugar-coat real problems though: if something is genuinely bad (long-open Sev 1, SLA breach), state it clearly and directly rather than spinning it. Constructive, not falsely upbeat
- No filler words, no "LangChain should", no em dashes"""

    chat_model = get_chat_model(model or DEFAULT_QBR_MODEL)
    response = await chat_model.ainvoke(prompt)

    content = response.content
    if isinstance(content, list):
        raw = "\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content).strip()
    else:
        raw = str(content).strip()
    # Strip markdown code fences if the model wraps the JSON
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
    model: str | None = None,
) -> dict:
    """Generate per-slide LangSmith usage summaries, observations, and opportunities from BQ chart data.

    Generates tailored content for 3 LangSmith Usage slides plus the Engagement Scorecard
    rollup in a single call so everything stays aligned:
      Slide 22 — Commit usage (contract KPIs: % into contract, % commit used)
      Slide 23 — LangSmith usage (traces, agent runs, page views, evaluator totals)
      Slide 24 — Feature adoption (experiments, Prompt Hub, datasets, evaluator rules)
      Engagement Scorecard — usage_summary synthesises all 3 slides above into one line
    """
    import json

    monthly = chart_data.get("monthly_usage", [])
    page_views_data = chart_data.get("page_views", [])
    evaluators = chart_data.get("evaluator_usage", [])
    contract = chart_data.get("contract_metrics") or {}
    enablement = chart_data.get("enablement_stats") or {}

    recent = monthly[-3:] if monthly else []
    seats = int(enablement.get("billable_seats") or 0)

    def _is_snapshot_only(rows: list[dict], *fields: str) -> bool:
        """True if only the latest row has any nonzero value across the given
        fields — e.g. self-hosted customers whose experiments/prompt hub/dataset
        counts are tracked only as a current snapshot, not historical monthly
        series (see bigquery_client's monthly_usage query). Mirrors the same
        detection hex_client.py uses to decide whether to render a single
        "Current" bar instead of a month-by-month chart."""
        if not rows:
            return False
        nonzero_idxs = {
            i for i, r in enumerate(rows)
            if any(float(r.get(f) or 0) > 0 for f in fields)
        }
        return nonzero_idxs == {len(rows) - 1}

    def _eval_is_snapshot_only(rows: list[dict]) -> bool:
        """Same idea as _is_snapshot_only, but for evaluator_usage rows, which
        are keyed by (month_start, eval_category) rather than one row per month."""
        if not rows:
            return False
        by_month: dict[str, float] = {}
        for r in rows:
            by_month[r["month_start"]] = by_month.get(r["month_start"], 0) + float(r.get("rules") or 0)
        months_sorted = sorted(by_month)
        nonzero = [m for m in months_sorted if by_month[m] > 0]
        return len(nonzero) == 1 and nonzero[0] == months_sorted[-1]

    feature_snapshot_only = _is_snapshot_only(
        monthly, "total_experiments", "total_prompt_commits", "total_prompt_pulls", "total_datasets"
    )
    eval_snapshot_only = _eval_is_snapshot_only(evaluators)

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
    # Note: like slide 23, the slide 22 chart returns no image at all when there's no
    # data (rather than showing a "no data" placeholder), so its absence is never
    # visually misleading — no NO-DATA hedge needed here either.
    slide22_block = (
        "Contract & commit usage:\n" + "\n".join(s22_lines) if s22_lines
        else "(No chart is shown on this slide when there is no data, so its absence is self-evident. Keep the summary/observations/opportunities brief and neutral — do not claim usage or non-usage, and do not mention data availability.)"
    )

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
    # Note: unlike slides 22/24, the slide 23 chart silently skips any panel with no
    # data (and the whole image if all panels are empty) rather than showing a "no
    # data" placeholder — so missing data here is never visually misleading. Only
    # include lines for metrics that actually have data; no NO-DATA hedge needed.
    s23_parts = []
    if s23_lines:
        s23_parts.append("Monthly traces & agent runs (last 3 months):\n" + "\n".join(s23_lines))
    if pv_lines:
        s23_parts.append("LangSmith page views (last 3 months):\n" + "\n".join(pv_lines))
    if evaluators:
        _eval_span = "current" if eval_snapshot_only else "12-month"
        s23_parts.append(f"Total evaluator rules ({_eval_span}): {total_eval_rules:,}")
    if seats:
        s23_parts.append(f"Active LangSmith users (MAU): {seats}")
    slide23_block = (
        "\n\n".join(s23_parts) if s23_parts
        else "(No chart is shown on this slide when there is no data, so its absence is self-evident. Keep the summary/observations/opportunities brief and neutral — do not claim usage or non-usage, and do not mention data availability.)"
    )

    # Slide 24 — Feature Adoption: experiments, Prompt Hub, datasets, evaluator rules by category
    s24_parts = []
    if feature_snapshot_only:
        last = monthly[-1]
        experiments = int(last.get("total_experiments") or 0)
        commits = int(last.get("total_prompt_commits") or 0)
        pulls = int(last.get("total_prompt_pulls") or 0)
        datasets = int(last.get("total_datasets") or 0)
        s24_parts.append(
            "Feature usage (CURRENT SNAPSHOT ONLY — this account's historical monthly "
            "trend for these features is not tracked upstream, only today's totals. Do "
            "NOT describe growth, decline, a surge, or any month-over-month comparison; "
            "describe adoption breadth/depth using only these current totals):\n"
            f"  experiments={experiments}, prompt_commits={commits},"
            f" prompt_pulls={pulls}, datasets={datasets}"
        )
    elif recent:
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
        s24_parts.append("Feature usage (last 3 months):\n" + "\n".join(s24_lines))
    else:
        s24_parts.append("Feature usage: NO DATA RECEIVED (tracking/sync gap, not necessarily zero usage)")

    eval_lines = [f"  {cat}: {cnt:,} rules" for cat, cnt in sorted(eval_totals.items())]
    if eval_lines:
        _eval_header = (
            "Evaluator rules by type (CURRENT SNAPSHOT ONLY — not a 12-month total, do "
            "not describe change over time):"
            if eval_snapshot_only else
            "Evaluator rules by type (12-month totals):"
        )
        s24_parts.append(_eval_header + "\n" + "\n".join(eval_lines))
    else:
        s24_parts.append("Evaluator rules by type: NO DATA RECEIVED (tracking/sync gap, not necessarily zero usage)")
    slide24_block = "\n\n".join(s24_parts)

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

1. Per-slide summary headlines — ONE sentence each, max 12 words, terse and specific to that slide's data only:
   - commit_summary (slide 22): commit/contract pacing status
   - tracing_summary (slide 23): tracing & agent run adoption status
   - feature_summary (slide 24): feature adoption breadth status

2. usage_summary: ONE short single-clause sentence, max 10 words, for the LangSmith Engagement
   Scorecard slide. No semicolons, no joining two statements into one — pick the single most
   important point, don't try to cover everything. Weight it towards the commit/contract
   picture (slide 22) as the primary signal — tracing and feature adoption inform it, not
   co-lead it. Must not contradict any of the 3 summaries above — it is the rollup, not a 4th
   opinion.

3. For EACH slide, write observations (1-3 bullets) and opportunities (1-3 bullets):
   - observations: factual snapshot of what the data shows on that specific slide only
   - opportunities: how to deepen value — specific to each slide's feature area:
       Slide 22: commit pacing risk, contract value realisation, usage trajectory vs renewal
       Slide 23: expanding tracing coverage, agent observability, growing active user base
       Slide 24: unused features (Playground, Online Evals, Experiments, Prompt Hub, Datasets), evaluator adoption

Rules:
- Summaries (commit_summary/tracing_summary/feature_summary/usage_summary): lead with what's working, note the biggest gap — full sentences, not fragments
- Bullets (observations/opportunities): STRICT 8-word max per bullet — count every word, terse slide fragments only, no full sentences
- No em dashes, no "LangChain", no filler words
- Tone: lean positive — this is a QBR read by {account_name}, frame as a partnership and lead with genuine wins where the data supports it. Do not sugar-coat real problems though: if something is genuinely bad (Sev 1 open, SLA breach, commit severely off-pace, sustained usage decline), state it clearly and directly rather than spinning it. Constructive, not falsely upbeat
- Academy/training topics belong on the Enablement slide, not here
- CRITICAL — "NO DATA RECEIVED" handling (slide 24 only): slide 24's chart always renders a "No data" placeholder for any empty panel, so a viewer could misread that as "customer doesn't use this feature." When a slide 24 data line is marked "NO DATA RECEIVED", that means LangChain is not currently receiving that data, NOT that the customer has zero usage. Never write or imply "no usage", "not using X", "limited adoption" for that metric. Instead hedge: say we are not receiving that data and suggest confirming tracking/instrumentation is set up correctly. If slide 24's data is entirely "NO DATA RECEIVED", its summary/observations should focus on the data gap itself rather than fabricating a usage narrative, and its opportunities should be about validating the data pipeline, not about feature adoption
- Slides 22 and 23 are different: their charts silently omit whatever has no data (or skip the whole chart) rather than showing a misleading placeholder, so the absence is already self-evident. Never mention missing data, tracking gaps, or data pipeline issues for slides 22 or 23 — if either has little or no underlying data, keep that slide's summary/observations/opportunities brief and neutral instead
- CRITICAL — "CURRENT SNAPSHOT ONLY" handling (slide 24 only): when a slide 24 data line is marked this way, the chart itself now shows a single "Current" bar, not a month-by-month trend — the data source only has today's totals, not history. Never write or imply a trend word for that metric: no "surged", "grew", "declined", "sustain momentum", "up/down from last month", no naming specific months (e.g. "zero in June and July"). Describe only the current level itself (e.g. adoption breadth across current totals, which features are and aren't in use right now)

Return ONLY valid JSON (no markdown, no code block):
{{"commit_summary": "...", "tracing_summary": "...", "feature_summary": "...", "usage_summary": "...", "slide22": {{"observations": ["...", "..."], "opportunities": ["..."]}}, "slide23": {{"observations": ["...", "..."], "opportunities": ["..."]}}, "slide24": {{"observations": ["...", "..."], "opportunities": ["..."]}}}}"""

    chat_model = get_chat_model(model or DEFAULT_QBR_MODEL)
    response = await chat_model.ainvoke(prompt)

    content = response.content
    if isinstance(content, list):
        raw = "\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content).strip()
    else:
        raw = str(content).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
    try:
        data = json.loads(raw)
        s22 = data.get("slide22", {})
        s23 = data.get("slide23", {})
        s24 = data.get("slide24", {})
        return {
            "commit_summary": str(data.get("commit_summary", "")),
            "tracing_summary": str(data.get("tracing_summary", "")),
            "feature_summary": str(data.get("feature_summary", "")),
            "usage_summary": str(data.get("usage_summary", "")),
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
            "commit_summary": "", "tracing_summary": "", "feature_summary": "", "usage_summary": "",
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
