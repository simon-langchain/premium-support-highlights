"""Slack API client for posting Block Kit messages."""

import httpx

_SLACK_API = "https://slack.com/api"
_TIMEOUT = 15
_channel_name_cache: dict[str, str] = {}

_EXTERNAL_PREFIXES = ("customer-", "eval-", "external-", "ext-", "partner-")


def get_channels(token: str) -> list[dict]:
    """Return all channels the bot can post to, as [{id, name}] sorted by name.

    Fetches public channels (types=public_channel) and private channels the bot
    is a member of (types=private_channel). Requires channels:read + groups:read.
    """
    results: list[dict] = []
    for types in ("public_channel", "private_channel"):
        cursor = None
        while True:
            try:
                params: dict = {"types": types, "exclude_archived": "true", "limit": "200"}
                if cursor:
                    params["cursor"] = cursor
                resp = httpx.get(
                    f"{_SLACK_API}/conversations.list",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    break
                for ch in data.get("channels", []):
                    if ch.get("is_ext_shared") or ch.get("is_shared"):
                        continue  # Skip Slack Connect / cross-workspace channels
                    name = ch.get("name", "")
                    if any(name.startswith(p) for p in _EXTERNAL_PREFIXES):
                        continue  # Skip channels with external-facing naming conventions
                    if ch.get("is_member") or types == "public_channel":
                        results.append({"id": ch["id"], "name": name or ch["id"]})
                        _channel_name_cache[ch["id"]] = ch.get("name", ch["id"])
                cursor = (data.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
            except Exception:
                break
    seen: set[str] = set()
    unique = [c for c in results if not (c["id"] in seen or seen.add(c["id"]))]  # type: ignore[func-returns-value]
    return sorted(unique, key=lambda c: c["name"].lower())


def get_channel_name(token: str, channel_id: str) -> str | None:
    """Resolve a Slack channel ID to its human-readable name.

    Returns the name (without #), or None if lookup fails (e.g. missing
    channels:read scope). Results are cached in-process.

    Requires channels:read scope for public channels, groups:read for private.
    """
    if channel_id in _channel_name_cache:
        return _channel_name_cache[channel_id]
    try:
        resp = httpx.get(
            f"{_SLACK_API}/conversations.info",
            headers={"Authorization": f"Bearer {token}"},
            params={"channel": channel_id},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            name = data.get("channel", {}).get("name")
            if name:
                _channel_name_cache[channel_id] = name
                return name
    except Exception:
        pass
    return None


def post_message(token: str, channel: str, fallback_text: str, blocks: list[dict], thread_ts: str | None = None, attachments: list[dict] | None = None) -> dict:
    """Post a Block Kit message to a Slack channel.

    Args:
        token: Slack bot token (xoxb-...).
        channel: Slack channel ID (e.g. C0AR0JE2873).
        fallback_text: Plain-text fallback shown in notifications where blocks aren't rendered.
        blocks: Slack Block Kit payload.

    Returns:
        Parsed Slack API response dict.

    Raises:
        RuntimeError: If the Slack API returns ok=false.
        httpx.HTTPStatusError: On HTTP-level failures.
    """
    resp = httpx.post(
        f"{_SLACK_API}/chat.postMessage",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "channel": channel,
            "text": fallback_text,
            "blocks": blocks,
            "unfurl_links": False,
            "unfurl_media": False,
            **({"thread_ts": thread_ts} if thread_ts else {}),
            **({"attachments": attachments} if attachments else {}),
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
    return data
