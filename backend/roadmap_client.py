"""Roadmap lookup, Drive caching, and customer-relevant item selection for QBR slides."""

import logging
import os
import re
from datetime import date, datetime, timezone

_log = logging.getLogger(__name__)

_ROADMAPS_FOLDER_NAME = "Roadmaps"
_TEAM_GTM_CHANNEL = "team-gtm"
_SLIDES_URL_RE = re.compile(r"docs\.google\.com/presentation/d/([\w-]+)")
_RSQM = "\u2019"  # RIGHT SINGLE QUOTATION MARK — Google Slides apostrophe


# ---------------------------------------------------------------------------
# Month label helpers
# ---------------------------------------------------------------------------

def _month_label(d: date) -> str:
    """'Apr \u201926' — matches Drive filenames written by the team."""
    return f"{d.strftime('%b')} {_RSQM}{d.strftime('%y')}"


def month_prefix(d: date) -> str:
    """'April\u201926: ' — used as the regular-weight prefix in QBR label boxes."""
    return f"{d.strftime('%B')}{_RSQM}{d.strftime('%y')}: "


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------

def _find_roadmaps_folder(drive) -> str | None:
    res = drive.files().list(
        q=(
            f"name='{_ROADMAPS_FOLDER_NAME}'"
            " and mimeType='application/vnd.google-apps.folder'"
            " and trashed=false"
        ),
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora="allDrives",
        fields="files(id)",
    ).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def find_roadmap_in_drive(drive, month: date) -> str | None:
    """Return the pres_id if this month's roadmap is already in the Roadmaps folder."""
    folder_id = _find_roadmaps_folder(drive)
    if not folder_id:
        return None
    label = _month_label(month)
    q = (
        f"'{folder_id}' in parents"
        f" and name contains '{label}'"
        " and mimeType='application/vnd.google-apps.presentation'"
        " and trashed=false"
    )
    res = drive.files().list(
        q=q,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id,name)",
    ).execute()
    files = res.get("files", [])
    if files:
        _log.info("Found roadmap in Drive: %s", files[0]["name"])
        return files[0]["id"]
    return None


def copy_roadmap_to_drive(
    drive,
    slides_url: str,
    month: date,
    shared_drive_id: str | None,
) -> str:
    """Copy the roadmap presentation into the Roadmaps folder. Returns the new pres_id."""
    m = _SLIDES_URL_RE.search(slides_url)
    if not m:
        raise ValueError(f"Not a valid Slides URL: {slides_url}")
    source_id = m.group(1)

    folder_id = _find_roadmaps_folder(drive)
    if not folder_id:
        body: dict = {
            "name": _ROADMAPS_FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if shared_drive_id:
            body["parents"] = [shared_drive_id]
        result = drive.files().create(body=body, supportsAllDrives=True).execute()
        folder_id = result["id"]

    label = _month_label(month)
    copy = drive.files().copy(
        fileId=source_id,
        supportsAllDrives=True,
        body={"name": f"{label} Product Roadmap", "parents": [folder_id]},
    ).execute()
    _log.info("Copied roadmap '%s Product Roadmap' → %s", label, copy["id"])
    return copy["id"]


# ---------------------------------------------------------------------------
# Slack lookup
# ---------------------------------------------------------------------------

def fetch_roadmap_link_from_slack(token: str, month: date) -> str | None:
    """Search #team-gtm messages in the first 7 days of the month for a Slides URL."""
    import httpx

    def _get(method: str, **params):
        r = httpx.get(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            _log.warning("Slack %s error: %s", method, data.get("error"))
            return None
        return data

    # Find #team-gtm (paginate if needed)
    channel_id: str | None = None
    cursor: str | None = None
    while not channel_id:
        params: dict = {"types": "public_channel", "limit": 200, "exclude_archived": "true"}
        if cursor:
            params["cursor"] = cursor
        data = _get("conversations.list", **params)
        if not data:
            break
        for ch in data.get("channels", []):
            if ch.get("name") == _TEAM_GTM_CHANNEL:
                channel_id = ch["id"]
                break
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    if not channel_id:
        _log.warning("Could not find #%s", _TEAM_GTM_CHANNEL)
        return None

    # Messages from first 7 days of the month
    oldest = datetime(month.year, month.month, 1, tzinfo=timezone.utc).timestamp()
    if month.month == 12:
        latest = datetime(month.year + 1, 1, 8, tzinfo=timezone.utc).timestamp()
    else:
        latest = datetime(month.year, month.month, 8, tzinfo=timezone.utc).timestamp()

    data = _get(
        "conversations.history",
        channel=channel_id,
        oldest=str(oldest),
        latest=str(latest),
        limit=200,
    )
    if not data:
        return None

    # Match "May" (or whatever month) AND "Product Roadmap" appearing anywhere in
    # the message — checked separately because mrkdwn bold formatting may split
    # them across spans (e.g. "*May* *Product Roadmap <url|here>*").
    month_name = month.strftime("%B").lower()

    for msg in data.get("messages", []):
        # Collect all candidate URLs and all text content from this message.
        candidate_urls: list[str] = []
        text_content: list[str] = [msg.get("text", "")]

        # Plain text (Slack mrkdwn — may contain <URL|label> links)
        url = _extract_slides_url(msg.get("text", ""))
        if url:
            candidate_urls.append(url)

        # Rich-text blocks (newer Slack format — URLs live in link elements,
        # not in the plain text field)
        for block in msg.get("blocks", []):
            for section_el in block.get("elements", []):
                if not isinstance(section_el, dict):
                    continue
                for inline in section_el.get("elements", []):
                    if not isinstance(inline, dict):
                        continue
                    if inline.get("type") == "link":
                        url = _extract_slides_url(inline.get("url", ""))
                        if url:
                            candidate_urls.append(url)
                    elif inline.get("type") == "text":
                        text_content.append(inline.get("text", ""))

        # Unfurl attachments
        for att in msg.get("attachments", []):
            url = _extract_slides_url(
                att.get("title_link", "") or att.get("from_url", "")
            )
            if url:
                candidate_urls.append(url)
            # Attachment title may say "May '26 Product Roadmap"
            text_content.append(att.get("title", ""))

        if not candidate_urls:
            continue

        # Only return a URL if this message is the roadmap announcement.
        combined_text = " ".join(text_content).lower()
        if month_name in combined_text and "product roadmap" in combined_text:
            return candidate_urls[0]

    _log.warning("No Slides URL found in #%s for %s", _TEAM_GTM_CHANNEL, _month_label(month))
    return None


def _extract_slides_url(text: str) -> str | None:
    m = _SLIDES_URL_RE.search(text or "")
    if m:
        return f"https://docs.google.com/presentation/d/{m.group(1)}"
    return None


# ---------------------------------------------------------------------------
# Roadmap parsing
# ---------------------------------------------------------------------------

def _is_feature_slide(texts: list[str], has_image: bool) -> bool:
    """Heuristic to skip cover, disclaimer, overview, and section-header slides."""
    if not texts:
        return False
    first = texts[0]
    skip_starts = ("Product Roadmap", "This roadmap", "Q1 ", "Q2 ", "Q3 ", "Q4 ")
    if any(first.startswith(s) for s in skip_starts):
        return False
    if "Roadmap" in first and len(texts) <= 6:
        return False
    # Section-header slides: no image, short text fragments only
    if not has_image and len(texts) <= 3:
        return False
    if not has_image and len(texts) >= 5 and all(len(t) < 50 for t in texts):
        return False
    return True


def extract_roadmap_items(slides_svc, pres_id: str, month: date) -> list[dict]:
    """Parse a roadmap presentation into structured feature items.

    Each item: {slide_id, pres_id, title, description, image_url, month}
    Skips cover (slide 1), disclaimer (slide 2), overview (slide 3), last cover.
    """
    pres = slides_svc.presentations().get(presentationId=pres_id).execute()
    slides = pres["slides"]
    items: list[dict] = []
    month_str = month.strftime("%B %Y")  # "April 2026"

    for slide in slides[3:-1]:
        texts: list[str] = []
        image_url: str | None = None
        image_aspect: float | None = None

        def _walk(elements: list) -> None:
            nonlocal image_url, image_aspect
            for el in elements:
                # Recurse into groups
                for child in el.get("elementGroup", {}).get("children", []):
                    _walk([child])
                # Text runs
                for te in el.get("shape", {}).get("text", {}).get("textElements", []):
                    c = te.get("textRun", {}).get("content", "").strip()
                    if c:
                        texts.append(c)
                # Standalone image elements
                img = el.get("image", {})
                if img and not image_url:
                    image_url = img.get("contentUrl") or img.get("sourceUrl")
                # Shape with image fill (screenshots often use stretchedPictureFill)
                if not image_url:
                    stretched = (
                        el.get("shape", {})
                          .get("shapeProperties", {})
                          .get("shapeBackgroundFill", {})
                          .get("stretchedPictureFill", {})
                    )
                    if stretched:
                        image_url = stretched.get("contentUrl") or stretched.get("sourceUrl")
                        img = stretched  # re-use img for size extraction below
                if image_url and not image_aspect:
                    size = el.get("size", {})
                    transform = el.get("transform", {})
                    ew = size.get("width", {}).get("magnitude", 0) * transform.get("scaleX", 1.0)
                    eh = size.get("height", {}).get("magnitude", 0) * transform.get("scaleY", 1.0)
                    if ew and eh:
                        image_aspect = ew / eh

        _walk(slide.get("pageElements", []))

        if not _is_feature_slide(texts, bool(image_url)):
            continue

        # Multi-feature category slide: "Category" header + feature bullets.
        # Handles two patterns:
        #   A) "Feature Name" fragment followed by ": description" fragment
        #   B) "Feature Name:" fragment (colon at end) followed by description text
        def _is_feature_bullet(t: str) -> bool:
            return len(t) <= 60 and t.rstrip().endswith(":")

        colon_start_frags = [t for t in texts if t.startswith(":")]
        colon_end_frags = [t for t in texts if _is_feature_bullet(t) and t != texts[0]]
        if (len(colon_start_frags) >= 3 or len(colon_end_frags) >= 3) and texts:
            category = texts[0].strip()
            i = 1
            while i < len(texts):
                t = texts[i]
                # Pattern B: "Feature Name:"
                if _is_feature_bullet(t):
                    feature_name = t.rstrip(":").strip()
                    j = i + 1
                    extra: list[str] = []
                    while j < len(texts) and not _is_feature_bullet(texts[j]) and not texts[j].startswith(":"):
                        extra.append(texts[j].strip())
                        j += 1
                    items.append({
                        "slide_id": slide["objectId"],
                        "pres_id": pres_id,
                        "title": f"{category}: {feature_name}",
                        "description": " ".join(extra)[:600],
                        "image_url": image_url,
                        "image_aspect": image_aspect,
                        "month": month_str,
                    })
                    i = j
                # Pattern A: "Feature Name" followed by ": description"
                elif i + 1 < len(texts) and texts[i + 1].startswith(":"):
                    feature_name = t.strip()
                    desc = texts[i + 1].lstrip(": ").strip()
                    j = i + 2
                    extra = []
                    while j < len(texts) and not texts[j].startswith(":") and not _is_feature_bullet(texts[j]):
                        extra.append(texts[j].strip())
                        j += 1
                    if extra:
                        desc = (desc + " " + " ".join(extra)).strip()
                    items.append({
                        "slide_id": slide["objectId"],
                        "pres_id": pres_id,
                        "title": f"{category}: {feature_name}",
                        "description": desc[:600],
                        "image_url": image_url,
                        "image_aspect": image_aspect,
                        "month": month_str,
                    })
                    i = j
                else:
                    i += 1
            continue

        # Title: collect leading short fragments.
        # Strip trailing colons from each part; join category + feature with ": ".
        title_parts: list[str] = []
        desc_start = 0
        for k, t in enumerate(texts):
            clean = t.lstrip(": ").rstrip(":").strip()
            if k == 0:
                title_parts.append(clean)
                desc_start = 1
            elif t.startswith(":"):
                if len(clean) <= 35:
                    # Short colon-fragment is a feature name (e.g. "LangSmith: Feature")
                    # Drop the category prefix, keep just the feature.
                    title_parts = [clean]
                    desc_start = k + 1
                # Long colon-fragment is a description — stop collecting title parts.
                break
            elif len(t) <= 45 and not clean.endswith(".") and len(title_parts) < 3:
                title_parts.append(clean)
                desc_start = k + 1
            else:
                break
        # First part is the section/category; remaining parts are the feature name
        title = (title_parts[0] + ": " + " ".join(title_parts[1:])) if len(title_parts) > 1 else (title_parts[0] if title_parts else "")
        desc_texts = texts[desc_start:]

        items.append({
            "slide_id": slide["objectId"],
            "pres_id": pres_id,
            "title": title,
            "description": " ".join(desc_texts)[:600],
            "image_url": image_url,
            "image_aspect": image_aspect,
            "month": month_str,
        })

    _log.info("Extracted %d roadmap items from %s", len(items), pres_id)
    return items


# ---------------------------------------------------------------------------
# AI item selection
# ---------------------------------------------------------------------------

async def select_roadmap_items(
    items: list[dict],
    open_issues: list[dict],
    account_name: str,
    today: date | None = None,
) -> list[dict]:
    """Pick the 3 roadmap items most relevant to this customer.

    Considers both current-month (upcoming) and past-month (delivered) items.
    Priority order:
    1. Past items matching open feature requests — "we delivered what you asked for".
    2. Current-month items matching open feature requests — "coming soon for you".
    3. Items topically related to other open tickets.
    4. First items in the current roadmap as a last resort.
    """
    import json as _json
    import re as _re
    from anthropic import AsyncAnthropic

    if not items:
        return []
    if len(items) <= 3:
        return items

    current_month_str = (today or date.today()).strftime("%B %Y")

    # Split issues into feature requests vs everything else
    fr_lines: list[str] = []
    other_lines: list[str] = []
    for i in open_issues[:50]:
        cf = i.get("custom_fields") or {}
        disp = (cf.get("disposition") or {}).get("value", "") or "issue"
        title = (i.get("title") or "")[:100]
        if disp == "feature_request":
            fr_lines.append(f"  • {title}")
        else:
            other_lines.append(f"  • [{disp}] {title}")

    fr_section = "\n".join(fr_lines) if fr_lines else "  (none)"
    other_section = "\n".join(other_lines[:20]) if other_lines else "  (none)"

    item_lines = [
        f"  {j} [{item['month']}{'  ← UPCOMING' if item['month'] == current_month_str else '  ← DELIVERED'}]: "
        f"{item['title']} — {item['description'][:200]}"
        for j, item in enumerate(items)
    ]

    prompt = f"""You are selecting which 3 product roadmap items to highlight in a QBR slide for {account_name}.

Items marked UPCOMING are on the current month's roadmap (planned/coming soon).
Items marked DELIVERED are from previous months' roadmaps (already shipped).

PRIORITY 1 — Open feature requests (match these first):
{fr_section}

PRIORITY 2 — Other open tickets (use for topical relevance if no FR matches):
{other_section}

Roadmap items (index [month  status]: title — description):
{chr(10).join(item_lines)}

Instructions:
- First priority: DELIVERED items that directly address the open feature requests — great "we shipped what you asked for" story.
- Second priority: UPCOMING items that directly address the open feature requests.
- Fill remaining slots with items (either status) most topically relevant to the other open tickets.
- If nothing matches, prefer UPCOMING items as a fallback.

Return ONLY a JSON array of exactly 3 distinct integers (indices into the roadmap list above), ordered most to least relevant. Example: [4, 1, 7]"""

    client = AsyncAnthropic()
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=60,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Extract JSON array even if there's surrounding text
    m = _re.search(r"\[[\d\s,]+\]", raw)
    if m:
        try:
            indices = _json.loads(m.group())
            selected = [items[i] for i in indices if 0 <= i < len(items)]
            selected = list({id(x): x for x in selected}.values())[:3]  # dedupe
            if len(selected) == 3:
                return selected
        except Exception:
            pass
    _log.warning("Roadmap selection parse failed, using first 3. Raw: %s", raw[:120])
    return items[:3]
