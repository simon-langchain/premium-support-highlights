"""Per-ticket AI next-steps generation.

Called by make_summarise_tickets_tool() in summary_agent.py. Each open ticket gets a
1-2 sentence "next steps" output that the summary agent uses as context before
writing its account-level report, and that appears directly on each ticket card in the UI.

All calls route through the LangSmith LLM Gateway via the centralized model factory
in llm.py. Results are cached by the tool in cache.py so unchanged tickets skip the
API call. The cache key includes the model, so switching models triggers a refresh.
"""

import re
from bs4 import BeautifulSoup

from llm import get_chat_model, DEFAULT_MODEL_ID

DEFAULT_TICKET_SUMMARY_MODEL = "anthropic:claude-haiku-4-5-20251001"


def _strip_html(html: str) -> str:
    if not html:
        return ""
    try:
        return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", "", html).strip()


def _build_message_context(messages: list[dict], max_messages: int = 5) -> str:
    # Sort oldest-to-newest by timestamp so [-N:] always gives the most recent.
    # Pylon messages use "timestamp", not "created_at".
    try:
        sorted_msgs = sorted(messages, key=lambda m: m.get("timestamp") or m.get("created_at") or "")
    except Exception:
        sorted_msgs = list(messages)
    recent = sorted_msgs[-max_messages:] if len(sorted_msgs) > max_messages else sorted_msgs
    parts = []
    for msg in recent:
        # Pylon messages use "message_html"; fall back to other common field names
        raw_body = (
            msg.get("message_html")
            or msg.get("body_html")
            or msg.get("body")
            or msg.get("content")
            or msg.get("text")
            or ""
        )
        body = _strip_html(raw_body)
        if not body:
            continue
        # Author name lives at author.name; fall back to author_type for legacy
        author_name = (msg.get("author") or {}).get("name") or msg.get("author_type") or "Unknown"
        parts.append(f"{author_name}: {body[:400]}")
    return "\n\n".join(parts)


def _state_label(state: str, account_name: str) -> str:
    customer = account_name or "the customer"
    labels = {
        "new": "New (not yet picked up by LangChain)",
        "waiting_on_you": f"Waiting on LangChain — {customer} is waiting, LangChain needs to act or respond",
        "waiting_on_customer": f"Waiting on {customer} — LangChain has responded and is waiting for {customer} to reply or act",
        "on_hold": "On hold — LangChain support is waiting on another internal LangChain team (e.g. Engineering or Product) to take action",
    }
    return labels.get(state, state.replace("_", " ").title())


async def summarize_ticket(
    title: str,
    body_html: str,
    messages: list[dict],
    state: str = "",
    account_name: str = "",
    model: str | None = None,
) -> str:
    """Generate a 1-2 sentence next-steps action for an open ticket."""
    chat_model = get_chat_model(model or DEFAULT_TICKET_SUMMARY_MODEL)

    body_text = _strip_html(body_html)[:600]
    messages_text = _build_message_context(messages)

    state_label = _state_label(state, account_name) if state else ""
    context = f"Title: {title}"
    if state_label:
        context += f"\nStatus: {state_label}"
    if body_text:
        context += f"\n\nOriginal request: {body_text}"
    if messages_text:
        context += f"\n\nRecent activity:\n{messages_text}"

    prompt = (
        context
        + "\n\nRespond in exactly this format (two lines, no extra text):\n"
        "Summary: <1-2 sentences describing the issue and its current state>\n"
        f"Next steps: <1 concise sentence in present tense — "
        f"if status is 'Waiting on {account_name or 'the customer'}', LangChain has replied and is waiting, "
        f"so write 'LangChain is awaiting a response from {account_name or 'the customer'}...'; "
        f"if status is 'Waiting on LangChain', write what LangChain is actively doing; "
        f"use 'LangChain' for the LangChain team and '{account_name or 'the customer'}' for the customer, "
        f"never individual names, never use 'should' or prescriptive language>"
    )

    response = await chat_model.ainvoke(prompt)

    content = response.content
    if isinstance(content, list):
        parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()


def parse_ticket_output(text: str) -> tuple[str, str]:
    """Parse 'Summary: ...\\nNext steps: ...' into (summary, next_steps).

    For old-format cache entries (plain text without labelled fields), the whole
    text is returned as next_steps so cards stay populated until regenerated.
    """
    summary = ""
    next_steps = ""
    for line in text.splitlines():
        if line.startswith("Summary:"):
            summary = line[len("Summary:"):].strip()
        elif line.startswith("Next steps:"):
            next_steps = line[len("Next steps:"):].strip()
    if not summary and not next_steps and text.strip():
        return "", text.strip()
    return summary, next_steps
