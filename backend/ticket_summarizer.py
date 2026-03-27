"""Per-ticket AI summarization using Claude Haiku.

Called by make_summarise_tickets_tool() in summary_agent.py. Each open ticket gets a
1-2 sentence "current state" summary that the summary agent uses as context before
writing its account-level report.

Haiku is used here (rather than Sonnet) because we make one call per open ticket,
often 20-40 in parallel. It's significantly cheaper and fast enough for short summaries.
Results are cached by the tool in cache.py so unchanged tickets skip the API call.
"""

import re
from bs4 import BeautifulSoup

DEFAULT_TICKET_SUMMARY_MODEL = "claude-haiku-4-5-20251001"


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


async def summarize_ticket(
    title: str,
    body_html: str,
    messages: list[dict],
    model: str | None = None,
) -> str:
    """Generate a 1-2 sentence summary of the ticket's current state."""
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()

    body_text = _strip_html(body_html)[:600]
    messages_text = _build_message_context(messages)

    context = f"Title: {title}"
    if body_text:
        context += f"\n\nOriginal request: {body_text}"
    if messages_text:
        context += f"\n\nRecent activity:\n{messages_text}"

    prompt = (
        context
        + "\n\nIn 1-2 sentences, describe where this ticket stands right now. "
        "Focus on current status and any blocking factors. "
        "No markdown, no intro phrase like 'The ticket' or 'This ticket'."
    )

    response = await client.messages.create(
        model=model or DEFAULT_TICKET_SUMMARY_MODEL,
        max_tokens=120,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text.strip()
