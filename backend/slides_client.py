"""Google Slides client for generating QBR support slide decks.

Copies a fixed template presentation, populates it with live Pylon data via
replaceAllText, shares the copy with the requesting user, and returns the URL.

Template: https://docs.google.com/presentation/d/1cPn6jsC4Sc3HcRJEOAaYcuo8nDVozRe5c8PStgnm-WM
Slide 1 — Enterprise Support (sample text strings are the replacement keys)
Slide 2 — Product Feedback ({{FEATURE_REQUEST_LIST}} is the only non-sample key)
"""

import calendar
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

_log = logging.getLogger(__name__)

TEMPLATE_PRESENTATION_ID = "1cPn6jsC4Sc3HcRJEOAaYcuo8nDVozRe5c8PStgnm-WM"
_SLIDE2_ID = "g3d006d5f096_0_141"
_FR_BOX_ID = "g3d006d5f096_0_147"
_FR_COL_MAX = 4  # max items per column before spawning a second column
_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
]


def _creds():
    from google.oauth2 import service_account

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured")
    return service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=_SCOPES
    )


def _services():
    from googleapiclient.discovery import build

    creds = _creds()
    return build("drive", "v3", credentials=creds), build("slides", "v1", credentials=creds)


def get_chart_start(months: int = 6) -> str:
    """Return ISO timestamp for the 1st day of the month that starts an N-month window ending now."""
    today = date.today()
    m = today.month - (months - 1)
    y = today.year
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}-01T00:00:00Z"


def get_last_quarter() -> tuple[str, str, str]:
    """Return (start_iso, end_iso, label) for the most recently completed quarter."""
    today = date.today()
    current_q = (today.month - 1) // 3 + 1
    year = today.year
    if current_q == 1:
        last_q, year = 4, year - 1
    else:
        last_q = current_q - 1
    start_month = (last_q - 1) * 3 + 1
    end_month = start_month + 2
    end_day = calendar.monthrange(year, end_month)[1]
    start = date(year, start_month, 1)
    end = date(year, end_month, end_day)
    return (
        f"{start.isoformat()}T00:00:00Z",
        f"{end.isoformat()}T23:59:59Z",
        f"Q{last_q} {year}",
    )


def _parse_dt_local(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _format_duration(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)}m"
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def _catmull_rom_smooth(x: list, y: list, n_points: int = 300):
    """Return smoothed (xs, ys) arrays via Catmull-Rom spline interpolation."""
    import numpy as np
    if len(x) < 2:
        return np.array(x), np.array(y)
    # Pad with phantom endpoints so the curve passes through the first and last points
    pts = list(zip(x, y))
    padded = [
        (2*pts[0][0] - pts[1][0],   2*pts[0][1] - pts[1][1]),
        *pts,
        (2*pts[-1][0] - pts[-2][0], 2*pts[-1][1] - pts[-2][1]),
    ]
    xs, ys = [], []
    n_seg = len(pts) - 1
    spp = max(2, n_points // max(n_seg, 1))
    for i in range(1, len(padded) - 2):
        p0, p1, p2, p3 = (np.array(padded[j]) for j in (i - 1, i, i + 1, i + 2))
        last_seg = i == len(padded) - 3
        for t in np.linspace(0, 1, spp, endpoint=last_seg):
            v = 0.5 * (
                2 * p1
                + (-p0 + p2) * t
                + (2*p0 - 5*p1 + 4*p2 - p3) * t**2
                + (-p0 + 3*p1 - 3*p2 + p3) * t**3
            )
            xs.append(float(v[0]))
            ys.append(float(v[1]))
    return np.array(xs), np.array(ys)


def _percentile(data: list[float], p: float) -> float | None:
    if not data:
        return None
    s = sorted(data)
    k = (p / 100) * (len(s) - 1)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] * (1 - (k - lo)) + s[hi] * (k - lo)


def _generate_metrics_chart(
    quarter_issues: list[dict],
    chart_issues: list[dict],
    chart_start_iso: str,
    quarter_label: str = "",
) -> bytes:
    """Generate a dark-themed metrics PNG for the QBR Enterprise Support slide.

    Metric tiles are computed from quarter_issues (last completed quarter).
    Line charts are computed from chart_issues bucketed monthly from
    chart_start_iso through the current month (covers last + current quarter).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
    from matplotlib.ticker import FuncFormatter

    resp_hrs: list[float] = []
    for i in quarter_issues:
        s = i.get("first_response_seconds")
        if s is None:
            continue
        try:
            s = float(s)
        except (TypeError, ValueError):
            continue
        if s > 0:
            resp_hrs.append(s / 3600)

    res_hrs: list[float] = []
    for i in quarter_issues:
        if i.get("state") not in {"closed", "resolved"}:
            continue
        created = _parse_dt_local(i.get("created_at"))
        updated = _parse_dt_local(i.get("updated_at"))
        if created and updated and updated > created:
            res_hrs.append((updated - created).total_seconds() / 3600)

    # Monthly chart buckets: chart_start_iso → now (spans last + current quarter)
    from calendar import month_abbr as _month_abbr
    now = datetime.now(timezone.utc)
    chart_start = _parse_dt_local(chart_start_iso)
    if chart_start:
        sy, sm = chart_start.year, chart_start.month
    else:
        sy, sm = now.year, (now.month - 5) % 12 or 12

    buckets: list[tuple[int, int]] = []
    y, m = sy, sm
    while (y, m) <= (now.year, now.month):
        buckets.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1

    month_labels = [_month_abbr[bm] for _, bm in buckets]
    bucket_idx = {b: i for i, b in enumerate(buckets)}

    monthly_resp: list[list[float]] = [[] for _ in buckets]
    monthly_res: list[list[float]] = [[] for _ in buckets]
    for issue in chart_issues:
        created = _parse_dt_local(issue.get("created_at"))
        if not created:
            continue
        idx = bucket_idx.get((created.year, created.month))
        if idx is None:
            continue
        s = issue.get("first_response_seconds")
        if s is not None:
            try:
                s = float(s)
                if s > 0:
                    monthly_resp[idx].append(s / 3600)
            except (TypeError, ValueError):
                pass
        if issue.get("state") in {"closed", "resolved"}:
            updated = _parse_dt_local(issue.get("updated_at"))
            if updated and updated > created:
                monthly_res[idx].append((updated - created).total_seconds() / 3600)

    monthly_resp_med = [_percentile(w, 50) for w in monthly_resp]
    monthly_res_med = [_percentile(w, 50) for w in monthly_res]

    BG = "#0c0d1a"
    CARD = "#161729"
    BLUE = "#4fa3ff"
    PURPLE = "#a78bfa"
    TEXT = "#e2e8f0"
    MUTED = "#718096"
    BORDER = "#2d3148"

    fig = plt.figure(figsize=(16, 11), facecolor=BG)

    gs = GridSpec(3, 3, figure=fig,
                  top=0.97, bottom=0.07, left=0.03, right=0.98,
                  hspace=0.35, wspace=0.22,
                  height_ratios=[1, 1, 1.5])

    tiles = [
        ("p50 Response", _percentile(resp_hrs, 50), BLUE),
        ("p90 Response", _percentile(resp_hrs, 90), BLUE),
        ("p99 Response", _percentile(resp_hrs, 99), BLUE),
        ("p50 Resolution", _percentile(res_hrs, 50), PURPLE),
        ("p90 Resolution", _percentile(res_hrs, 90), PURPLE),
        ("p99 Resolution", _percentile(res_hrs, 99), PURPLE),
    ]
    for idx, (label, val, color) in enumerate(tiles):
        row, col = divmod(idx, 3)
        ax = fig.add_subplot(gs[row, col])
        ax.set_facecolor(CARD)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER)
            spine.set_linewidth(0.8)
        ax.set_xticks([])
        ax.set_yticks([])
        display = _format_duration(val) if val is not None else "—"
        ax.text(0.5, 0.60, display, ha="center", va="center",
                fontsize=34, fontweight="bold", color=color,
                transform=ax.transAxes)
        ax.text(0.5, 0.18, label, ha="center", va="center",
                fontsize=14, color="#b8c8e0", transform=ax.transAxes)

    def _draw_line(ax, values, labels, color, title_str):
        ax.set_facecolor(CARD)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER)
            spine.set_linewidth(0.8)
        ax.tick_params(colors=MUTED, labelsize=9)
        ax.set_title(title_str, color=TEXT, fontsize=13, pad=6)
        x = list(range(len(values)))
        valid = [(xi, v) for xi, v in zip(x, values) if v is not None]
        if valid:
            vx, vy = zip(*valid)
            xs, ys = _catmull_rom_smooth(list(vx), list(vy))
            ax.plot(xs, ys, color=color, linewidth=2.5, zorder=3)
            ax.fill_between(xs, ys, alpha=0.12, color=color)
            ax.plot(list(vx), list(vy), "o", color=color, markersize=4, zorder=4)
            ax.yaxis.set_major_formatter(FuncFormatter(lambda h, _: _format_duration(h)))
            ax.tick_params(axis="y", colors=MUTED)
        else:
            ax.text(0.5, 0.5, "No data", ha="center", va="center",
                    color=MUTED, transform=ax.transAxes, fontsize=12)
        step = max(1, len(labels) // 6)
        shown = sorted(set(list(range(0, len(labels), step)) + [len(labels) - 1]))
        ax.set_xticks([i for i in shown if i < len(labels)])
        ax.set_xticklabels([labels[i] for i in shown if i < len(labels)],
                           rotation=30, ha="right", color=MUTED, fontsize=9)
        ax.set_xlim(-0.5, len(labels) - 0.5)

    _draw_line(fig.add_subplot(gs[2, :2]), monthly_resp_med, month_labels,
               BLUE, "Median First Response Time (monthly)")
    _draw_line(fig.add_subplot(gs[2, 2]), monthly_res_med, month_labels,
               PURPLE, "Median Resolution Time (monthly)")

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _get_or_create_folder(drive, name: str, parent_id: str | None) -> str:
    """Return the Drive folder ID for `name` under `parent_id`, creating it if absent."""
    q = f"name = {json.dumps(name)} and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    results = drive.files().list(
        q=q,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id)",
        pageSize=1,
    ).execute()
    existing = results.get("files", [])
    if existing:
        return existing[0]["id"]
    meta: dict = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    return drive.files().create(
        body=meta, supportsAllDrives=True, fields="id",
    ).execute()["id"]


def _upload_chart(drive, chart_bytes: bytes, parent_folder_id: str | None) -> str:
    """Upload chart PNG into `parent_folder_id`, grant anyoneWithLink reader, return URL."""
    from googleapiclient.http import MediaIoBaseUpload

    meta: dict = {"name": "_qbr_metrics_chart.png", "mimeType": "image/png"}
    if parent_folder_id:
        meta["parents"] = [parent_folder_id]

    media = MediaIoBaseUpload(BytesIO(chart_bytes), mimetype="image/png", resumable=False)
    file_id = drive.files().create(
        body=meta, media_body=media, supportsAllDrives=True, fields="id",
    ).execute()["id"]

    drive.permissions().create(
        fileId=file_id,
        supportsAllDrives=True,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return f"https://drive.google.com/uc?id={file_id}"


def _find_image_object_id(slides_svc, pres_id: str) -> str | None:
    """Return the objectId of the first image on slide 0."""
    pres = slides_svc.presentations().get(presentationId=pres_id).execute()
    for elem in pres.get("slides", [{}])[0].get("pageElements", []):
        if "image" in elem:
            return elem["objectId"]
    return None


def _replace_chart_image(slides_svc, pres_id: str, object_id: str, chart_url: str) -> None:
    slides_svc.presentations().batchUpdate(
        presentationId=pres_id,
        body={"requests": [{
            "replaceImage": {
                "imageObjectId": object_id,
                "url": chart_url,
                "imageReplaceMethod": "CENTER_INSIDE",
            }
        }]},
    ).execute()


def _add_fr_second_column(slides_svc, pres_id: str, col2_titles: list[str]) -> None:
    """Resize the FR list box to half-width and duplicate it for a second column.

    Uses duplicateObject so col 2 inherits identical styling, autofit, and
    content-alignment from col 1 — avoiding manual re-application of those
    properties, which don't reliably apply to freshly created TEXT_BOX shapes.
    """
    _NAT   = 3_000_000   # natural size of the FR box (both axes, EMU)
    _ORIG_H = 787_500    # rendered height
    _ORIG_X = 561_500    # left edge (kept symmetric)
    _ORIG_Y = 4_005_275
    _GAP    = 100_000    # gap between columns
    # Make total width symmetric: slide_width - 2 * left_margin = 8,021,000
    _TOTAL_W = 9_144_000 - 2 * _ORIG_X   # 8,021,000

    col_w  = (_TOTAL_W - _GAP) // 2      # 3,960,500
    col2_x = _ORIG_X + col_w + _GAP      # 4,622,000

    col2_obj = "fr_col2_overflow"

    # Cap col 2 and add "+N more" if still overflowing
    visible = col2_titles[:_FR_COL_MAX]
    remainder = len(col2_titles) - len(visible)
    if remainder:
        visible.append(f"+ {remainder} more")

    requests = [
        # 1. Shrink col 1 to half-width (symmetric margins)
        {
            "updatePageElementTransform": {
                "objectId": _FR_BOX_ID,
                "transform": {
                    "scaleX": col_w / _NAT,
                    "scaleY": _ORIG_H / _NAT,
                    "translateX": _ORIG_X,
                    "translateY": _ORIG_Y,
                    "unit": "EMU",
                },
                "applyMode": "ABSOLUTE",
            }
        },
        # 2. Duplicate col 1 — inherits all styling, autofit, alignment
        {
            "duplicateObject": {
                "objectId": _FR_BOX_ID,
                "objectIds": {_FR_BOX_ID: col2_obj},
            }
        },
        # 3. Move duplicate to col 2 position
        {
            "updatePageElementTransform": {
                "objectId": col2_obj,
                "transform": {
                    "scaleX": col_w / _NAT,
                    "scaleY": _ORIG_H / _NAT,
                    "translateX": col2_x,
                    "translateY": _ORIG_Y,
                    "unit": "EMU",
                },
                "applyMode": "ABSOLUTE",
            }
        },
        # 4. Clear the inherited col 1 text
        {
            "deleteText": {
                "objectId": col2_obj,
                "textRange": {"type": "ALL"},
            }
        },
        # 5. Insert col 2 items — inherits paragraph/bullet style from trailing marker
        {
            "insertText": {
                "objectId": col2_obj,
                "text": "\n".join(visible),
                "insertionIndex": 0,
            }
        },
    ]

    slides_svc.presentations().batchUpdate(
        presentationId=pres_id,
        body={"requests": requests},
    ).execute()


def create_slide_deck(
    account_name: str,
    slide14: dict,
    slide15: dict,
    quarter_label: str,
    month_label: str = "",
) -> tuple[str, str, str]:
    """Copy the QBR template into the customer folder and apply text replacements.

    Returns (pres_id, url, customer_folder_id).
    """
    drive, slides = _services()
    shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None
    customer_folder_id = _get_or_create_folder(drive, account_name, shared_drive_id)
    deck_label = month_label or quarter_label

    for _attempt in range(3):
        try:
            copy = drive.files().copy(
                fileId=TEMPLATE_PRESENTATION_ID,
                supportsAllDrives=True,
                body={
                    "name": f"{account_name} — QBR {deck_label}",
                    "parents": [customer_folder_id],
                },
            ).execute()
            break
        except Exception as exc:
            if _attempt == 2:
                raise
            _log.warning("Drive copy attempt %d failed (%s), retrying…", _attempt + 1, exc)
            time.sleep(2 ** _attempt)
    pres_id = copy["id"]

    replace_requests = _build_requests(account_name, slide14, slide15)
    if replace_requests:
        slides.presentations().batchUpdate(
            presentationId=pres_id,
            body={"requests": replace_requests},
        ).execute()

    fr_titles = slide15.get("open_feature_request_titles", [])
    if len(fr_titles) > _FR_COL_MAX:
        _add_fr_second_column(slides, pres_id, fr_titles[_FR_COL_MAX:])

    url = f"https://docs.google.com/presentation/d/{pres_id}/edit"
    return pres_id, url, customer_folder_id


def add_roadmap_items(pres_id: str, items: list[dict]) -> None:
    """Replace the 3 roadmap label boxes and screenshot images with selected roadmap items.

    Each item needs: {slide_id, pres_id, title, description, image_url, month}.
    Label format: "Month'YY: " (regular) + "Feature Name" (bold), Inter Tight 12pt white.
    Images are replaced with slide thumbnails from the roadmap presentation.
    """
    from datetime import datetime as _dt

    _, slides_svc = _services()

    text_requests: list[dict] = []
    img_replace_requests: list[dict] = []   # replaceImage — isolated batch
    img_border_requests: list[dict] = []    # outline border — fully isolated
    _replaced_img_ids: set[str] = set()

    for i, item in enumerate(items[:3]):
        text_id = _ROADMAP_TEXT_IDS[i]
        img_id  = _ROADMAP_IMG_IDS[i]

        # Build prefix: "April'26: " using curly apostrophe to match template
        month_str = item.get("month", "")
        try:
            d = _dt.strptime(month_str, "%B %Y")
            prefix = f"{d.strftime('%B')}\u2019{d.strftime('%y')}: "
        except ValueError:
            prefix = ""
        feature = item.get("title", "")

        _STYLE_BASE = {
            "fontFamily": "Inter Tight",
            "fontSize": {"magnitude": 12, "unit": "PT"},
            "foregroundColor": {"opaqueColor": {"themeColor": "LIGHT1"}},
        }

        text_requests += [
            # 1. Clear the template text
            {"deleteText": {"objectId": text_id, "textRange": {"type": "ALL"}}},
            # 2. Insert feature name (bold)
            {"insertText": {"objectId": text_id, "insertionIndex": 0, "text": feature}},
            {
                "updateTextStyle": {
                    "objectId": text_id,
                    "textRange": {"type": "ALL"},
                    "style": {**_STYLE_BASE, "bold": True},
                    "fields": "bold,fontFamily,fontSize,foregroundColor",
                }
            },
            # 3. Insert prefix at index 0 (pushes feature name to the right)
            {"insertText": {"objectId": text_id, "insertionIndex": 0, "text": prefix}},
            {
                "updateTextStyle": {
                    "objectId": text_id,
                    "textRange": {
                        "type": "FIXED_RANGE",
                        "startIndex": 0,
                        "endIndex": len(prefix),
                    },
                    "style": {**_STYLE_BASE, "bold": False},
                    "fields": "bold,fontFamily,fontSize,foregroundColor",
                }
            },
        ]

        # Prefer the product screenshot embedded in the roadmap slide (image_url);
        # fall back to the full slide thumbnail if none is available.
        thumb_url = item.get("image_url")
        if not thumb_url:
            _log.info("No image_url for '%s', falling back to getThumbnail", item.get("title"))
            try:
                thumb = slides_svc.presentations().pages().getThumbnail(
                    presentationId=item["pres_id"],
                    pageObjectId=item["slide_id"],
                    thumbnailProperties_mimeType="PNG",
                    thumbnailProperties_thumbnailSize="LARGE",
                ).execute()
                thumb_url = thumb.get("contentUrl")
            except Exception as exc:
                _log.warning("getThumbnail failed for %s/%s: %s", item.get("pres_id"), item.get("slide_id"), exc)
        if not thumb_url:
            _log.warning("No image available for roadmap slide %s", item.get("slide_id"))
            continue

        img_replace_requests.append({
            "replaceImage": {
                "imageObjectId": img_id,
                "url": thumb_url,
                "imageReplaceMethod": "CENTER_CROP",
            }
        })
        _replaced_img_ids.add(img_id)

        # White 2pt border so dark screenshots pop on the dark background.
        img_border_requests.append({
            "updateImageProperties": {
                "objectId": img_id,
                "imageProperties": {
                    "outline": {
                        "outlineFill": {
                            "solidFill": {
                                "color": {"rgbColor": {"red": 1.0, "green": 1.0, "blue": 1.0}},
                                "alpha": 1.0,
                            }
                        },
                        "weight": {"magnitude": 2.0, "unit": "PT"},
                        "dashStyle": "SOLID",
                        "propertyState": "RENDERED",
                    },
                },
                "fields": "outline",
            }
        })


    if text_requests:
        slides_svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={"requests": text_requests},
        ).execute()

    if img_replace_requests:
        slides_svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={"requests": img_replace_requests},
        ).execute()

    if img_border_requests:
        try:
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={"requests": img_border_requests},
            ).execute()
        except Exception as exc:
            _log.warning("Border batch failed: %s", exc)


def add_metrics_chart(
    pres_id: str,
    customer_folder_id: str,
    quarter_issues: list[dict],
    chart_issues: list[dict],
    quarter_start_iso: str,
    quarter_label: str,
) -> None:
    """Generate the metrics chart, upload to the cache subfolder, and replace the slide image."""
    drive, slides = _services()
    cache_folder_id = _get_or_create_folder(drive, "cache", customer_folder_id)
    chart_bytes = _generate_metrics_chart(quarter_issues, chart_issues, quarter_start_iso, quarter_label)
    chart_url = _upload_chart(drive, chart_bytes, cache_folder_id)
    img_id = _find_image_object_id(slides, pres_id)
    if img_id:
        _replace_chart_image(slides, pres_id, img_id, chart_url)


def share_presentation(pres_id: str, user_email: str) -> None:
    """Grant the requesting user writer access to the presentation."""
    drive, _ = _services()
    # Files in a Shared Drive have no individual owner, so no transferOwnership
    # needed — writer access is sufficient.
    drive.permissions().create(
        fileId=pres_id,
        supportsAllDrives=True,
        sendNotificationEmail=False,
        body={"type": "user", "role": "writer", "emailAddress": user_email},
    ).execute()


# Object IDs of the 5 indicator dots, left to right.
_DOT_IDS = [          # slide 1 — health score
    "g3e618779947_0_12",
    "g3e618779947_0_13",
    "g3e618779947_0_14",
    "g3e618779947_0_15",
    "g3e618779947_0_16",
]
_ROADMAP_TEXT_IDS = [  # slide 2 — 3 roadmap label boxes, left to right
    "g3d006d5f096_0_144",
    "g3d006d5f096_0_145",
    "g3d006d5f096_0_149",
]
_ROADMAP_IMG_IDS = [   # slide 2 — 3 roadmap screenshot images, left to right
    "g3d006d5f096_0_142",
    "g3d006d5f096_0_143",
    "g3d006d5f096_0_148",
]
_SLIDE2_DOT_IDS = [   # slide 2 — FR delivery score
    "g3d006d5f096_0_151",
    "g3d006d5f096_0_152",
    "g3d006d5f096_0_153",
    "g3d006d5f096_0_154",
    "g3d006d5f096_0_155",
]
_DOT_LIT   = {"red": 0.502, "green": 0.784, "blue": 1.0}   # bright blue
_DOT_UNLIT = {"themeColor": "LIGHT2"}                       # matches Product Feedback slide circles


def _health_score(s14: dict) -> int:
    """Return a 1-5 score: 5 = excellent, 1 = critical.

    Scoring uses a strikes model — each negative signal adds 1-2 strikes,
    and the total maps to a score. Hard floor: any Sev 1 open → 1.
    """
    sev1             = s14.get("sev1_open", 0)
    sev2             = s14.get("sev2_open", 0)
    sla_pct          = s14.get("sla_pct")
    avg_resp_hours   = s14.get("avg_response_hours")
    median_resolution_days = s14.get("median_resolution_days")

    if sev1 > 0:
        return 1

    strikes = 0

    # Severity signals
    if sev2 > 2:   strikes += 2
    elif sev2 > 0: strikes += 1

    # SLA compliance
    if sla_pct is not None:
        if sla_pct < 60:   strikes += 2
        elif sla_pct < 80: strikes += 1

    # Average first response time
    if avg_resp_hours is not None:
        if avg_resp_hours > 24:  strikes += 2
        elif avg_resp_hours > 8: strikes += 1

    # Median resolution time of closed quarter tickets
    if median_resolution_days is not None:
        if median_resolution_days > 30:   strikes += 2
        elif median_resolution_days > 14: strikes += 1

    if strikes >= 4: return 2
    if strikes >= 2: return 3
    if strikes >= 1: return 4
    return 5


def _fr_score(s15: dict) -> int:
    """Return 1-5 score based on delivered vs open feature requests.

    5 = ≥80% of total FRs delivered this quarter; 1 = <20%.
    Returns 3 (neutral) when there are no FRs at all.
    """
    delivered = s15.get("feature_requests_delivered", 0)
    open_count = s15.get("feature_requests_open", 0)
    total = delivered + open_count
    if total == 0:
        return 3
    ratio = delivered / total
    if ratio >= 0.8: return 5
    if ratio >= 0.6: return 4
    if ratio >= 0.4: return 3
    if ratio >= 0.2: return 2
    return 1


def _build_requests(account_name: str, s14: dict, s15: dict) -> list[dict]:
    """Build replaceAllText API requests for both slides."""
    sev1 = s14.get("sev1_open", 0)
    sev2 = s14.get("sev2_open", 0)
    sev3 = s14.get("sev3_open", 0)
    sev4 = s14.get("sev4_open", 0)
    waiting = s14.get("waiting_on_you", 0)
    fr_open_14 = s14.get("feature_requests_open", 0)
    sla_pct = s14.get("sla_pct")
    observations = s14.get("observations", [])
    opportunities = s14.get("opportunities", [])

    sla_clause = f" {sla_pct}% of tickets within response time SLA." if sla_pct is not None else ""
    sev1_text = (
        f"No pending Sev 1 support tickets.{sla_clause}"
        if sev1 == 0
        else f"{sev1} pending Sev 1 ticket{'s' if sev1 > 1 else ''}.{sla_clause}"
    )
    waiting_text = (
        f"{waiting} open ticket{'s' if waiting != 1 else ''} pending LangChain's action"
        if waiting > 0
        else "No open tickets pending LangChain's action"
    )
    sev2_text = (
        f"{sev2} Sev 2 open ticket{'s' if sev2 != 1 else ''} with SE"
        if sev2 > 0
        else "No Sev 2 open tickets"
    )
    sev_parts = [
        f"{c} {lbl}"
        for lbl, c in [("Sev 1", sev1), ("Sev 2", sev2), ("Sev 3", sev3), ("Sev 4", sev4)]
        if c > 0
    ]
    sev_breakdown = (
        "By Severity — " + ", ".join(sev_parts) if sev_parts else "No open support tickets"
    )
    fr14_text = (
        f"{fr_open_14} feature request{'s' if fr_open_14 != 1 else ''} actively being worked on"
        if fr_open_14 > 0
        else "No open feature requests"
    )

    fr_open_15 = s15.get("feature_requests_open", 0)
    fr_delivered = s15.get("feature_requests_delivered", 0)
    fr_titles = s15.get("open_feature_request_titles", [])
    summary_line = (
        f"{fr_open_15} open feature request{'s' if fr_open_15 != 1 else ''}; "
        f"{fr_delivered} delivered {'capability' if fr_delivered == 1 else 'capabilities'} "
        f"sought by {account_name}"
    )
    fr_list = "\n".join(fr_titles[:_FR_COL_MAX]) if fr_titles else "No open feature requests"

    # Keys are the exact strings in the template; values are the live replacements.
    # matchCase: false handles minor capitalisation differences in the template.
    obs_text = "\n".join(observations) if observations else "…"
    opp_text = "\n".join(opportunities) if opportunities else "…"

    replacements = {
        "No pending Sev 1 support tickets. Uptime and response time SLAs within agreed terms": sev1_text,
        "7 open tickets - pending LangChain's action": waiting_text,
        "1 Sev 2 open ticket with SE": sev2_text,
        "By Severity - 1 Sev 2, 1 Sev 3, 1  Sev 4": sev_breakdown,
        "Four feature requests for Agent Builder actively being worked on": fr14_text,
        "{{OBSERVATIONS}}": obs_text,
        "{{OPPORTUNITIES}}": opp_text,
        "{{FEATURE_REQUEST_LIST}}": fr_list,
        "5 open feature requests; 12 delivered capabilities sought by [Customer]": summary_line,
        "[Customer]": account_name,
    }

    score = _health_score(s14)
    fr_score = _fr_score(s15)
    dot_requests = [
        {
            "updateShapeProperties": {
                "objectId": obj_id,
                "shapeProperties": {
                    "shapeBackgroundFill": {
                        "solidFill": {"color": {"rgbColor": _DOT_LIT} if i < score else _DOT_UNLIT}
                    }
                },
                "fields": "shapeBackgroundFill",
            }
        }
        for i, obj_id in enumerate(_DOT_IDS)
    ] + [
        {
            "updateShapeProperties": {
                "objectId": obj_id,
                "shapeProperties": {
                    "shapeBackgroundFill": {
                        "solidFill": {"color": {"rgbColor": _DOT_LIT} if i < fr_score else _DOT_UNLIT}
                    }
                },
                "fields": "shapeBackgroundFill",
            }
        }
        for i, obj_id in enumerate(_SLIDE2_DOT_IDS)
    ]

    return [
        {
            "replaceAllText": {
                "containsText": {"text": old, "matchCase": False},
                "replaceText": new,
            }
        }
        for old, new in replacements.items()
    ] + dot_requests
