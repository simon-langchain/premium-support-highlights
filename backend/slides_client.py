"""Google Slides client for generating QBR slide decks.

Copies a template presentation, populates it with live Pylon data via
replaceAllText and targeted shape updates, then returns the URL.

Active template is controlled by QBR_TEMPLATE_ID env var.

Production template (2-slide support deck):
  https://docs.google.com/presentation/d/1cPn6jsC4Sc3HcRJEOAaYcuo8nDVozRe5c8PStgnm-WM
Full QBR deck template (testing):
  https://docs.google.com/presentation/d/1REIEBgXCje0glCCzxuRMVyfDbps9kG9h92ld4N9sEaQ
"""

import calendar
import contextvars
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

_log = logging.getLogger(__name__)

# Fallback template IDs (used when Drive lookup fails or no shared drive is configured).
_PRODUCTION_TEMPLATE_ID = "1cPn6jsC4Sc3HcRJEOAaYcuo8nDVozRe5c8PStgnm-WM"
_FULL_DECK_TEMPLATE_ID  = "1REIEBgXCje0glCCzxuRMVyfDbps9kG9h92ld4N9sEaQ"

# Display names of the two templates in the shared drive's Template folder.
TEMPLATE_NAMES = {
    "support_highlights": "LangChain QBR Template - Support Highlights",
    "full_deck":          "LangChain QBR Template",
}
# Fallback IDs when Drive lookup fails.
_TEMPLATE_FALLBACK_IDS = {
    "support_highlights": _PRODUCTION_TEMPLATE_ID,
    "full_deck":          _FULL_DECK_TEMPLATE_ID,
}
# In-process cache: template_type → presentation ID discovered from Drive.
_discovered_template_ids: dict[str, str] = {}

# Per-request context var so each QBR generation can use a different template
# without mutating global state.  Set via resolve_template_id() in main.py.
_template_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "qbr_template_id", default=None
)


def _template_id() -> str:
    """Return the active template ID: context var > env > production fallback."""
    return (
        _template_ctx.get()
        or os.environ.get("QBR_TEMPLATE_ID", "").strip()
        or _PRODUCTION_TEMPLATE_ID
    )


def resolve_template_id(template_type: str) -> str:
    """Look up the presentation ID for the given template_type from the shared Drive.

    Falls back to hardcoded IDs if Drive is unavailable or the file isn't found.
    Results are cached in-process.
    """
    if template_type in _discovered_template_ids:
        return _discovered_template_ids[template_type]

    name = TEMPLATE_NAMES.get(template_type)
    shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None

    if name and shared_drive_id:
        try:
            drive, _ = _services()
            common = {
                "corpora": "drive",
                "driveId": shared_drive_id,
                "includeItemsFromAllDrives": True,
                "supportsAllDrives": True,
            }
            # Find the Template folder in the shared drive.
            folders = drive.files().list(
                q="name = 'Template' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                fields="files(id)",
                **common,
            ).execute().get("files", [])

            for folder in folders:
                results = drive.files().list(
                    q=(
                        f"name = '{name}' and '{folder['id']}' in parents"
                        " and mimeType = 'application/vnd.google-apps.presentation'"
                        " and trashed = false"
                    ),
                    fields="files(id)",
                    **common,
                ).execute().get("files", [])
                if results:
                    pres_id = results[0]["id"]
                    _discovered_template_ids[template_type] = pres_id
                    _log.info("resolve_template_id: found %s → %s", template_type, pres_id)
                    return pres_id
        except Exception as exc:
            _log.warning("resolve_template_id: Drive lookup failed for %s: %s", template_type, exc)

    fallback = _TEMPLATE_FALLBACK_IDS.get(template_type, _PRODUCTION_TEMPLATE_ID)
    _log.info("resolve_template_id: using fallback for %s → %s", template_type, fallback)
    return fallback

# ---------------------------------------------------------------------------
# Per-template object ID configs
# Object IDs are presentation-specific; run scripts/discover_qbr_template_ids.py
# against a new template to populate its entry here.
# ---------------------------------------------------------------------------

_TEMPLATE_CONFIGS: dict[str, dict] = {
    _PRODUCTION_TEMPLATE_ID: {
        "slide2_id":    "g3d006d5f096_0_141",
        "fr_box_id":    "g3d006d5f096_0_147",
        "dot_ids": [
            "g3e618779947_0_12",
            "g3e618779947_0_13",
            "g3e618779947_0_14",
            "g3e618779947_0_15",
            "g3e618779947_0_16",
        ],
        "slide2_dot_ids": [
            "g3d006d5f096_0_151",
            "g3d006d5f096_0_152",
            "g3d006d5f096_0_153",
            "g3d006d5f096_0_154",
            "g3d006d5f096_0_155",
        ],
        "roadmap_text_ids": [
            "g3d006d5f096_0_144",
            "g3d006d5f096_0_145",
            "g3d006d5f096_0_149",
        ],
        "roadmap_img_ids": [
            "g3d006d5f096_0_142",
            "g3d006d5f096_0_143",
            "g3d006d5f096_0_148",
        ],
        "scorecard_support_dot_ids": [],
        "scorecard_fr_dot_ids":      [],
        # Production 2-slide deck: Enterprise Support is slide 1 (index 0)
        "metrics_chart_slide_index":  0,
        # Production 2-slide deck has no maturity, commit, usage, or feature slides
        "hex_chart_slide_index":          None,
        "maturity_bar_slide_index":       None,
        "commit_usage_slide_index":       None,
        "usage_chart_slide_index":        None,
        "feature_usage_slide_index":      None,
        "maturity_journey_slide_index":   None,
    },
    # Full QBR deck (test template)
    # IDs discovered by running discover_template_ids.py
    _FULL_DECK_TEMPLATE_ID: {
        "slide2_id":    None,
        "fr_box_id":    "g3db0c53a0fe_0_21",
        # Enterprise Support slide (slide 27) — health score dots
        "dot_ids": [
            "g3db0c53a0fe_0_6",
            "g3db0c53a0fe_0_7",
            "g3db0c53a0fe_0_8",
            "g3db0c53a0fe_0_9",
            "g3db0c53a0fe_0_10",
        ],
        # Product Feedback slide (slide 28) — FR delivery score dots
        "slide2_dot_ids": [
            "g3db0c53a0fe_0_25",
            "g3db0c53a0fe_0_26",
            "g3db0c53a0fe_0_27",
            "g3db0c53a0fe_0_28",
            "g3db0c53a0fe_0_29",
        ],
        # Engagement Scorecard (slide 15) — Technical Support row (row 3)
        "scorecard_support_dot_ids": [
            "g3c5483549f8_0_3331",
            "g3c5483549f8_0_3332",
            "g3c5483549f8_0_3333",
            "g3c5483549f8_0_3334",
            "g3c5483549f8_0_3335",
        ],
        # Engagement Scorecard (slide 15) — Product Feedback row (row 4)
        "scorecard_fr_dot_ids": [
            "g3c5483549f8_0_3337",
            "g3c5483549f8_0_3338",
            "g3c5483549f8_0_3339",
            "g3c5483549f8_0_3340",
            "g3c5483549f8_0_3341",
        ],
        # Roadmap slide (slide 11) — title-only placeholder; items inserted separately
        # Product Feedback slide (slide 28) — 3 roadmap item labels (left→right)
        "roadmap_text_ids": [
            "g3db0c53a0fe_0_18",   # left  label
            "g3db0c53a0fe_0_19",   # center label
            "g3db0c53a0fe_0_23",   # right  label
        ],
        # Product Feedback slide (slide 28) — 3 roadmap screenshot images (left→right)
        "roadmap_img_ids": [
            "g3db0c53a0fe_0_16",   # left  image
            "g3db0c53a0fe_0_17",   # center image
            "g3db0c53a0fe_0_22",   # right  image
        ],
        # Enterprise Support slide (slide 26) — metrics chart image
        "metrics_chart_slide_index": 25,  # 0-based index: slide 26 = index 25
        # Agent Maturity slide (slide 33) — Hex radar chart replaces existing chart image
        "hex_chart_slide_index":     32,  # slide 33 = index 32 (Option 2: radar)
        # Agent Maturity slide (slide 32) — Hex bar chart (Option 1)
        "maturity_bar_slide_index":  31,  # slide 32 = index 31 (Option 1: bar)
        # Contract commit usage slide (slide 22) — single Hex chart
        "commit_usage_slide_index":       21,  # slide 22 = index 21
        # LangSmith Usage composite slide (slide 23) — up to 2×2 tiled charts
        "usage_chart_slide_index":        22,  # slide 23 = index 22
        # Feature usage composite slide (slide 24) — 3+2 tiled charts
        "feature_usage_slide_index":      23,  # slide 24 = index 23
        # Enablement & Training slide (slide 25) — Academy sign-ups table
        "enablement_slide_index":         24,  # slide 25 = index 24
        # Agent Engineering Maturity journey slide (slide 31) — arrow picker
        "maturity_journey_slide_index":   30,  # slide 31 = index 30
        # Product Usage slides (slides 23-25) — one 5-dot row per slide, same score
        "usage_dot_id_rows": [
            ["g3d006d5f096_0_60", "g3d006d5f096_0_61", "g3d006d5f096_0_62", "g3d006d5f096_0_63", "g3d006d5f096_0_64"],  # slide 23
            ["g3d006d5f096_0_75", "g3d006d5f096_0_76", "g3d006d5f096_0_77", "g3d006d5f096_0_78", "g3d006d5f096_0_79"],  # slide 24
            ["g3d006d5f096_0_90", "g3d006d5f096_0_91", "g3d006d5f096_0_92", "g3d006d5f096_0_93", "g3d006d5f096_0_94"],  # slide 25
        ],
        # Customer logo placeholders — slides 2, 7, and 14
        "logo_slide2_shape_id":  "g3c5483549f8_0_2553",  # RECTANGLE "CUSTOMER LOGO" to swap for image
        "logo_slide2_page_id":   "g3bec9bcb565_0_0",     # slide 2 objectId (needed for createImage)
        "logo_slide7_shape_id":  "g3d06298ad88_0_640",   # RECTANGLE "CUSTOMER LOGO" on Meet the Team slide
        "logo_slide7_page_id":   "g3c5483549f8_0_954",   # slide 7 objectId
        "logo_slide14_shape_id": "g3d006d5f096_0_219",   # RECTANGLE "CUSTOMER LOGO" to delete
        "logo_slide14_img_id":   "g3d006d5f096_0_220",   # existing IMAGE element to replace
        # Engagement Scorecard (slide 15) — LangSmith Usage row (1st dot row)
        "scorecard_usage_dot_ids": [
            "g3c5483549f8_0_2999",
            "g3c5483549f8_0_3000",
            "g3c5483549f8_0_3001",
            "g3c5483549f8_0_3002",
            "g3c5483549f8_0_3003",
        ],
    },
}

def _cfg() -> dict:
    """Return the object-ID config for the active template."""
    return _TEMPLATE_CONFIGS.get(_template_id(), _TEMPLATE_CONFIGS[_PRODUCTION_TEMPLATE_ID])

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
    return (
        build("drive", "v3", credentials=creds, cache_discovery=False),
        build("slides", "v1", credentials=creds, cache_discovery=False),
    )


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
        s = i.get("business_hours_first_response_seconds")
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
        s = i.get("business_hours_resolution_seconds")
        if s is None:
            continue
        try:
            s = float(s)
        except (TypeError, ValueError):
            continue
        if s > 0:
            res_hrs.append(s / 3600)

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
        s = issue.get("business_hours_first_response_seconds")
        if s is not None:
            try:
                s = float(s)
                if s > 0:
                    monthly_resp[idx].append(s / 3600)
            except (TypeError, ValueError):
                pass
        if issue.get("state") in {"closed", "resolved"}:
            s = issue.get("business_hours_resolution_seconds")
            if s is not None:
                try:
                    s = float(s)
                    if s > 0:
                        monthly_res[idx].append(s / 3600)
                except (TypeError, ValueError):
                    pass

    monthly_resp_med = [_percentile(w, 50) for w in monthly_resp]
    monthly_res_med = [_percentile(w, 50) for w in monthly_res]

    BG = "#0c0d1a"
    CARD = "#161729"
    BLUE = "#4fa3ff"
    PURPLE = "#a78bfa"
    TEXT = "#e2e8f0"
    MUTED = "#94a3b8"
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
        ax.tick_params(colors=MUTED, labelsize=11)
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
                    color=MUTED, transform=ax.transAxes, fontsize=14)
        step = max(1, len(labels) // 6)
        shown = sorted(set(list(range(0, len(labels), step)) + [len(labels) - 1]))
        ax.set_xticks([i for i in shown if i < len(labels)])
        ax.set_xticklabels([labels[i] for i in shown if i < len(labels)],
                           rotation=30, ha="right", color=MUTED, fontsize=11)
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


def _find_folder(
    drive,
    name: str,
    parent_id: str | None,
    shared_drive_id: str | None = None,
) -> str | None:
    """Return the Drive folder ID for `name`, or None if not found.

    shared_drive_id must be the shared drive root ID (not a subfolder) when
    searching within a shared drive.  driveId scopes the search to that drive;
    the in-parents clause narrows to the specific parent within it.
    """
    q = f"name = {json.dumps(name)} and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    kwargs: dict = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}
    if parent_id:
        q += f" and '{parent_id}' in parents"
    if shared_drive_id:
        kwargs["corpora"] = "drive"
        kwargs["driveId"] = shared_drive_id
    else:
        kwargs["corpora"] = "allDrives"
    results = drive.files().list(q=q, fields="files(id)", pageSize=1, **kwargs).execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def _get_or_create_folder(
    drive,
    name: str,
    parent_id: str | None,
    shared_drive_id: str | None = None,
) -> str:
    """Return the Drive folder ID for `name` under `parent_id`, creating it if absent."""
    folder_id = _find_folder(drive, name, parent_id, shared_drive_id)
    if folder_id:
        return folder_id
    meta: dict = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    return drive.files().create(
        body=meta, supportsAllDrives=True, fields="id",
    ).execute()["id"]


def _parse_deck_label(deck_label: str) -> str | None:
    """Parse 'Q2 April 2026' or legacy 'April 2026' → '2026-04', or None if unparseable."""
    import re
    from calendar import month_name as _mn
    # New format: "Q2 April 2026"
    m = re.match(r"Q\d\s+(\w+)\s+(\d{4})$", deck_label.strip())
    if not m:
        # Legacy format: "April 2026"
        m = re.match(r"(\w+)\s+(\d{4})$", deck_label.strip())
    if not m:
        return None
    for i, name in enumerate(_mn):
        if name == m.group(1):
            return f"{m.group(2)}-{i:02d}"
    return None


def list_qbr_slides(account_name: str, shared_drive_id: str | None = None) -> list[dict]:
    """Return all QBR slides for account_name from Drive as [{url, pres_id, created_at, month_label, month}]."""
    drive, _ = _services()
    folder_id = _find_folder(drive, account_name, shared_drive_id, shared_drive_id)
    if not folder_id:
        return []
    prefix = f"{account_name} — QBR "
    q = (
        f"'{folder_id}' in parents"
        " and mimeType = 'application/vnd.google-apps.presentation'"
        " and trashed = false"
    )
    list_kwargs: dict = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}
    if shared_drive_id:
        list_kwargs["corpora"] = "drive"
        list_kwargs["driveId"] = shared_drive_id
    else:
        list_kwargs["corpora"] = "allDrives"
    results = drive.files().list(
        q=q, fields="files(id, name, createdTime)", orderBy="createdTime desc",
        **list_kwargs,
    ).execute()
    slides = []
    for f in results.get("files", []):
        if not f["name"].startswith(prefix):
            continue
        deck_label = f["name"][len(prefix):]
        year_month = _parse_deck_label(deck_label)
        if not year_month:
            _log.warning("list_qbr_slides could not parse deck_label=%r", deck_label)
            continue
        slides.append({
            "url": f"https://docs.google.com/presentation/d/{f['id']}/edit",
            "pres_id": f["id"],
            "created_at": f["createdTime"],
            "month_label": deck_label,
            "month": year_month,
        })
    return slides


def delete_file(file_id: str) -> None:
    """Move a Drive file to the trash.

    Uses trash rather than permanent delete — shared drives require Manager
    (organizer) role for permanent deletion but only Content Manager
    (fileOrganizer) for trashing.  Trashed shared-drive files are purged
    automatically after 30 days.
    """
    drive, _ = _services()
    drive.files().update(
        fileId=file_id,
        supportsAllDrives=True,
        body={"trashed": True},
    ).execute()


def _upload_chart(drive, chart_bytes: bytes, parent_folder_id: str | None) -> str:
    """Upload chart PNG into `parent_folder_id` in the shared drive, grant public reader, return URL."""
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

    return f"https://drive.google.com/uc?export=download&id={file_id}"


def _batch_update_with_image_retry(slides_svc, pres_id: str, requests: list[dict]) -> dict:
    """batchUpdate wrapper with retry for requests that reference a Drive image
    just uploaded by _upload_chart.

    Drive's "anyone: reader" permission can take a moment to propagate, and
    Slides' image fetcher occasionally loses that race — failing with a 400
    "provided image should be publicly accessible" error on a file that's
    correctly public a second later. Retries only that specific error.
    """
    from googleapiclient.errors import HttpError

    for attempt in range(3):
        try:
            return slides_svc.presentations().batchUpdate(
                presentationId=pres_id, body={"requests": requests},
            ).execute()
        except HttpError as exc:
            if attempt == 2 or "problem retrieving the image" not in str(exc):
                raise
            _log.warning("batchUpdate image request failed (attempt %d), retrying: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)


def _find_image_object_id(slides_svc, pres_id: str, slide_index: int = 0) -> str | None:
    """Return the objectId of the first image on the given slide (0-based index)."""
    pres = slides_svc.presentations().get(presentationId=pres_id).execute()
    slides_list = pres.get("slides", [])
    if slide_index >= len(slides_list):
        return None
    for elem in slides_list[slide_index].get("pageElements", []):
        if "image" in elem:
            return elem["objectId"]
    return None


def _replace_chart_image(slides_svc, pres_id: str, object_id: str, chart_url: str) -> None:
    _batch_update_with_image_retry(slides_svc, pres_id, [{
        "replaceImage": {
            "imageObjectId": object_id,
            "url": chart_url,
            "imageReplaceMethod": "CENTER_INSIDE",
        }
    }])


def _add_fr_second_column(slides_svc, pres_id: str, col2_titles: list[str]) -> None:
    """Resize the FR list box to half-width and duplicate it for a second column.

    Uses duplicateObject so col 2 inherits identical styling, autofit, and
    content-alignment from col 1 — avoiding manual re-application of those
    properties, which don't reliably apply to freshly created TEXT_BOX shapes.
    """
    fr_box_id = _cfg()["fr_box_id"]
    if not fr_box_id:
        _log.warning("_add_fr_second_column: fr_box_id not configured for template %s", _template_id())
        return

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
                "objectId": fr_box_id,
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
                "objectId": fr_box_id,
                "objectIds": {fr_box_id: col2_obj},
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
    customer_folder_id = _get_or_create_folder(drive, account_name, shared_drive_id, shared_drive_id)
    deck_label = month_label or quarter_label

    for _attempt in range(3):
        try:
            copy = drive.files().copy(
                fileId=_template_id(),
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
    # Extract "Month YYYY" from deck_label (strips any leading quarter prefix).
    import re as _re
    _m = _re.search(r'([A-Z][a-z]+ \d{4})$', deck_label)
    _date_str = _m.group(1) if _m else deck_label
    # Replace [Customer][date] as a unit (injects newline) and [Date] standalone.
    replace_requests.insert(0, {
        "replaceAllText": {
            "containsText": {"text": "[Date]", "matchCase": False},
            "replaceText": _date_str,
        }
    })
    replace_requests.insert(0, {
        "replaceAllText": {
            "containsText": {"text": "[Customer][date]", "matchCase": False},
            "replaceText": f"{account_name}\n{_date_str}",
        }
    })
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

    cfg = _cfg()
    roadmap_text_ids = cfg["roadmap_text_ids"]
    roadmap_img_ids  = cfg["roadmap_img_ids"]

    if not roadmap_text_ids or not roadmap_img_ids:
        _log.warning("add_roadmap_items: roadmap IDs not configured for template %s", _template_id())
        return

    text_requests: list[dict] = []
    img_replace_requests: list[dict] = []   # replaceImage — isolated batch
    img_border_requests: list[dict] = []    # outline border — fully isolated
    _replaced_img_ids: set[str] = set()

    for i, item in enumerate(items[:3]):
        text_id = roadmap_text_ids[i]
        img_id  = roadmap_img_ids[i]

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
    """Generate the metrics chart, upload to the shared drive cache folder, and replace the slide image."""
    drive, slides = _services()
    shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None
    cache_folder_id = _get_or_create_folder(drive, "cache", customer_folder_id, shared_drive_id)
    chart_bytes = _generate_metrics_chart(quarter_issues, chart_issues, quarter_start_iso, quarter_label)
    chart_url = _upload_chart(drive, chart_bytes, cache_folder_id)
    slide_index = _cfg().get("metrics_chart_slide_index", 0)
    img_id = _find_image_object_id(slides, pres_id, slide_index)
    if img_id:
        _replace_chart_image(slides, pres_id, img_id, chart_url)
    else:
        _log.warning("add_metrics_chart: no image element found on slide %d of %s", slide_index, pres_id)


def _insert_chart_at_slide(
    pres_id: str,
    customer_folder_id: str,
    chart_bytes: bytes,
    slide_index: int,
) -> None:
    """Upload chart PNG to Drive and replace (or create) the image on the given slide (0-based)."""
    drive, slides = _services()
    shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None
    cache_folder_id = _get_or_create_folder(drive, "cache", customer_folder_id, shared_drive_id)
    chart_url = _upload_chart(drive, chart_bytes, cache_folder_id)

    pres = slides.presentations().get(presentationId=pres_id).execute()
    slides_list = pres.get("slides", [])
    if slide_index >= len(slides_list):
        _log.warning("_insert_chart_at_slide: slide index %d not found in %s", slide_index, pres_id)
        return
    slide = slides_list[slide_index]
    slide_id = slide["objectId"]

    existing_img_id = next(
        (e["objectId"] for e in slide.get("pageElements", []) if "image" in e),
        None,
    )
    if existing_img_id:
        _batch_update_with_image_retry(slides, pres_id, [{
            "replaceImage": {
                "imageObjectId": existing_img_id,
                "url": chart_url,
                "imageReplaceMethod": "CENTER_INSIDE",
            }
        }])
    else:
        page_size = pres.get("pageSize", {})
        w = page_size.get("width", {}).get("magnitude", 9_144_000)
        h = page_size.get("height", {}).get("magnitude", 5_143_500)
        img_w, img_h = w * 0.6, h * 0.6
        _batch_update_with_image_retry(slides, pres_id, [{
            "createImage": {
                "url": chart_url,
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width": {"magnitude": img_w, "unit": "EMU"},
                        "height": {"magnitude": img_h, "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1, "scaleY": 1,
                        "translateX": (w - img_w) / 2,
                        "translateY": (h - img_h) / 2,
                        "unit": "EMU",
                    },
                },
            }
        }])
    _log.info("_insert_chart_at_slide: inserted chart into slide %d of %s", slide_index, pres_id)


def add_hex_chart(
    pres_id: str,
    customer_folder_id: str,
    chart_bytes: bytes,
    slide_index: int | None = None,
) -> None:
    """Upload the maturity radar PNG and replace the image on the radar slide (Option 2)."""
    if slide_index is None:
        slide_index = _cfg().get("hex_chart_slide_index")
    if slide_index is None:
        _log.info("add_hex_chart: hex_chart_slide_index not configured for template %s — skipping", _template_id())
        return
    _insert_chart_at_slide(pres_id, customer_folder_id, chart_bytes, slide_index)


def add_maturity_bar_chart(pres_id: str, customer_folder_id: str, chart_bytes: bytes) -> None:
    """Upload the Hex maturity bar chart PNG and replace the image on the bar chart slide (Option 1)."""
    slide_index = _cfg().get("maturity_bar_slide_index")
    if slide_index is None:
        _log.info("add_maturity_bar_chart: maturity_bar_slide_index not configured for template %s — skipping", _template_id())
        return
    _insert_chart_at_slide(pres_id, customer_folder_id, chart_bytes, slide_index)


def add_commit_usage_chart(pres_id: str, customer_folder_id: str, chart_bytes: bytes) -> None:
    """Upload the Hex commit usage PNG and replace the image on the contract usage slide."""
    slide_index = _cfg().get("commit_usage_slide_index")
    if slide_index is None:
        _log.info("add_commit_usage_chart: commit_usage_slide_index not configured for template %s — skipping", _template_id())
        return
    _insert_chart_at_slide(pres_id, customer_folder_id, chart_bytes, slide_index)


def add_usage_chart(pres_id: str, customer_folder_id: str, chart_bytes: bytes) -> None:
    """Upload the LangSmith usage composite PNG and replace the image on the usage slide."""
    slide_index = _cfg().get("usage_chart_slide_index")
    if slide_index is None:
        _log.info("add_usage_chart: usage_chart_slide_index not configured for template %s — skipping", _template_id())
        return
    _insert_chart_at_slide(pres_id, customer_folder_id, chart_bytes, slide_index)


def add_feature_usage_chart(pres_id: str, customer_folder_id: str, chart_bytes: bytes) -> None:
    """Upload the feature usage 3+2 composite PNG and replace the image on the feature usage slide."""
    slide_index = _cfg().get("feature_usage_slide_index")
    if slide_index is None:
        _log.info("add_feature_usage_chart: feature_usage_slide_index not configured for template %s — skipping", _template_id())
        return
    _insert_chart_at_slide(pres_id, customer_folder_id, chart_bytes, slide_index)


def add_academy_table(pres_id: str, customer_folder_id: str, chart_bytes: bytes) -> None:
    """Upload the Academy sign-ups table PNG to the enablement slide.

    Deletes any existing image placeholder on the slide, then creates a new image
    spanning the lower portion of the slide at full width so the table is readable.
    """
    slide_index = _cfg().get("enablement_slide_index")
    if slide_index is None:
        _log.info("add_academy_table: enablement_slide_index not configured for template %s — skipping", _template_id())
        return

    drive, slides = _services()
    shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None
    cache_folder_id = _get_or_create_folder(drive, "cache", customer_folder_id, shared_drive_id)
    chart_url = _upload_chart(drive, chart_bytes, cache_folder_id)

    pres = slides.presentations().get(presentationId=pres_id).execute()
    slides_list = pres.get("slides", [])
    if slide_index >= len(slides_list):
        _log.warning("add_academy_table: slide index %d not found in %s", slide_index, pres_id)
        return
    slide = slides_list[slide_index]
    slide_id = slide["objectId"]

    page_size = pres.get("pageSize", {})
    page_w = page_size.get("width", {}).get("magnitude", 9_144_000)
    page_h = page_size.get("height", {}).get("magnitude", 5_143_500)

    requests = []
    # Delete any existing image placeholders on this slide
    for el in slide.get("pageElements", []):
        if "image" in el:
            requests.append({"deleteObject": {"objectId": el["objectId"]}})

    # Place table in the Academy section, aligned with the Format/Audience/Scope columns
    # and clear of the "Academy" label + sign-ups text on the left.
    margin_x = int(page_w * 0.38)   # right of the Academy label/text (~38% in)
    top_y    = int(page_h * 0.57)   # below the Workshop section (~57% down)
    img_w    = int(page_w * 0.55)   # 55% wide (right-side column area only)
    img_h    = int(page_h * 0.33)   # 33% tall

    requests.append({
        "createImage": {
            "url": chart_url,
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {
                    "width":  {"magnitude": img_w, "unit": "EMU"},
                    "height": {"magnitude": img_h, "unit": "EMU"},
                },
                "transform": {
                    "scaleX": 1, "scaleY": 1,
                    "translateX": margin_x,
                    "translateY": top_y,
                    "unit": "EMU",
                },
            },
        }
    })

    _batch_update_with_image_retry(slides, pres_id, requests)
    _log.info("add_academy_table: inserted table into slide %d of %s", slide_index, pres_id)


def add_customer_logo(pres_id: str, logo_url: str, customer_folder_id: str | None = None) -> None:
    """Replace the CUSTOMER LOGO placeholders on slides 2 and 14 with the customer's logo.

    Slide 2:  deletes the text rectangle and creates an image at the same bounding box.
    Slide 14: deletes the text rectangle, then replaces the existing image element.

    logo_url is fetched locally and re-uploaded to Drive so the Slides API can access it
    (external URLs like logo.dev are often blocked by Google's image fetcher).
    Silently skips if the config has no logo IDs (production 2-slide template).
    """
    import httpx

    cfg = _cfg()
    shape2_id  = cfg.get("logo_slide2_shape_id")
    page2_id   = cfg.get("logo_slide2_page_id")
    shape7_id  = cfg.get("logo_slide7_shape_id")
    page7_id   = cfg.get("logo_slide7_page_id")
    shape14_id = cfg.get("logo_slide14_shape_id")
    img14_id   = cfg.get("logo_slide14_img_id")

    if not any([shape2_id, shape7_id, shape14_id, img14_id]):
        return

    # Fetch the logo bytes and re-host on Drive so Google Slides can access them.
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        resp = client.get(logo_url)
        resp.raise_for_status()
        logo_bytes = resp.content

    drive, slides_svc = _services()
    shared_drive_id = os.environ.get("QBR_SHARED_DRIVE_ID", "").strip() or None
    if customer_folder_id:
        cache_folder_id = _get_or_create_folder(drive, "cache", customer_folder_id, shared_drive_id)
    else:
        cache_folder_id = None
    drive_logo_url = _upload_chart(drive, logo_bytes, cache_folder_id)

    # Read just enough of the presentation to get the slide 2 shape transform.
    pres = slides_svc.presentations().get(presentationId=pres_id).execute()

    requests: list[dict] = []

    if shape2_id and page2_id:
        slide2 = next((s for s in pres.get("slides", []) if s["objectId"] == page2_id), None)
        if slide2:
            shape2 = next(
                (e for e in slide2.get("pageElements", []) if e["objectId"] == shape2_id),
                None,
            )
            if shape2:
                t  = shape2.get("transform", {})
                sz = shape2.get("size", {})
                eff_w = sz.get("width",  {}).get("magnitude", 3_000_000) * t.get("scaleX", 1)
                eff_h = sz.get("height", {}).get("magnitude", 3_000_000) * t.get("scaleY", 1)

                # Compute aspect-ratio-preserving display size within the placeholder.
                try:
                    from PIL import Image as _PILImage
                    import io as _io
                    _img = _PILImage.open(_io.BytesIO(logo_bytes))
                    img_w_px, img_h_px = _img.size
                except Exception:
                    img_w_px, img_h_px = 1, 1  # fallback: treat as square

                img_ratio = img_w_px / img_h_px
                box_ratio = eff_w / eff_h
                if img_ratio > box_ratio:
                    display_w = eff_w
                    display_h = eff_w / img_ratio
                else:
                    display_h = eff_h
                    display_w = eff_h * img_ratio

                offset_x = t.get("translateX", 0) + (eff_w - display_w) / 2
                offset_y = t.get("translateY", 0) + (eff_h - display_h) / 2

                requests.append({"deleteObject": {"objectId": shape2_id}})
                requests.append({
                    "createImage": {
                        "url": drive_logo_url,
                        "elementProperties": {
                            "pageObjectId": page2_id,
                            "size": {
                                "width":  {"magnitude": display_w, "unit": "EMU"},
                                "height": {"magnitude": display_h, "unit": "EMU"},
                            },
                            "transform": {
                                "scaleX": 1, "scaleY": 1,
                                "translateX": offset_x,
                                "translateY": offset_y,
                                "unit": "EMU",
                            },
                        },
                    }
                })

    if shape7_id and page7_id:
        slide7 = next((s for s in pres.get("slides", []) if s["objectId"] == page7_id), None)
        if slide7:
            shape7 = next(
                (e for e in slide7.get("pageElements", []) if e["objectId"] == shape7_id),
                None,
            )
            if shape7:
                t7  = shape7.get("transform", {})
                sz7 = shape7.get("size", {})
                eff_w7 = sz7.get("width",  {}).get("magnitude", 3_000_000) * t7.get("scaleX", 1)
                eff_h7 = sz7.get("height", {}).get("magnitude", 3_000_000) * t7.get("scaleY", 1)
                try:
                    from PIL import Image as _PILImage
                    import io as _io
                    _img7 = _PILImage.open(_io.BytesIO(logo_bytes))
                    img7_w_px, img7_h_px = _img7.size
                except Exception:
                    img7_w_px, img7_h_px = 1, 1
                img7_ratio = img7_w_px / img7_h_px
                box7_ratio = eff_w7 / eff_h7
                if img7_ratio > box7_ratio:
                    d7_w, d7_h = eff_w7, eff_w7 / img7_ratio
                else:
                    d7_h, d7_w = eff_h7, eff_h7 * img7_ratio
                off7_x = t7.get("translateX", 0) + (eff_w7 - d7_w) / 2
                off7_y = t7.get("translateY", 0) + (eff_h7 - d7_h) / 2
                requests.append({"deleteObject": {"objectId": shape7_id}})
                requests.append({
                    "createImage": {
                        "url": drive_logo_url,
                        "elementProperties": {
                            "pageObjectId": page7_id,
                            "size": {
                                "width":  {"magnitude": d7_w, "unit": "EMU"},
                                "height": {"magnitude": d7_h, "unit": "EMU"},
                            },
                            "transform": {
                                "scaleX": 1, "scaleY": 1,
                                "translateX": off7_x,
                                "translateY": off7_y,
                                "unit": "EMU",
                            },
                        },
                    }
                })

    if shape14_id:
        # img14_id is the LangChain logo on this slide — leave it untouched.
        # Only delete the "CUSTOMER LOGO" text rectangle and create the customer
        # logo image at its bounding box position.
        page14_id: str | None = None
        box14_eff_w = box14_eff_h = box14_tx = box14_ty = None
        for slide in pres.get("slides", []):
            for elem in slide.get("pageElements", []):
                if elem["objectId"] == shape14_id:
                    if page14_id is None:
                        page14_id = slide["objectId"]
                    t14  = elem.get("transform", {})
                    sz14 = elem.get("size", {})
                    box14_eff_w = sz14.get("width",  {}).get("magnitude", 1_200_000) * t14.get("scaleX", 1)
                    box14_eff_h = sz14.get("height", {}).get("magnitude", 1_200_000) * t14.get("scaleY", 1)
                    box14_tx    = t14.get("translateX", 0)
                    box14_ty    = t14.get("translateY", 0)
        requests.append({"deleteObject": {"objectId": shape14_id}})
        if page14_id and box14_eff_w:
            # Fit logo within the text-rect bounding box preserving aspect ratio.
            try:
                from PIL import Image as _PILImage
                import io as _io
                _img14 = _PILImage.open(_io.BytesIO(logo_bytes))
                img14_w_px, img14_h_px = _img14.size
            except Exception:
                img14_w_px, img14_h_px = 1, 1
            ir = img14_w_px / img14_h_px
            br = box14_eff_w / box14_eff_h
            if ir > br:
                d14_w, d14_h = box14_eff_w, box14_eff_w / ir
            else:
                d14_h, d14_w = box14_eff_h, box14_eff_h * ir
            requests.append({
                "createImage": {
                    "url": drive_logo_url,
                    "elementProperties": {
                        "pageObjectId": page14_id,
                        "size": {
                            "width":  {"magnitude": d14_w, "unit": "EMU"},
                            "height": {"magnitude": d14_h, "unit": "EMU"},
                        },
                        "transform": {
                            "scaleX": 1, "scaleY": 1,
                            "translateX": box14_tx + (box14_eff_w - d14_w) / 2,
                            "translateY": box14_ty + (box14_eff_h - d14_h) / 2,
                            "unit": "EMU",
                        },
                    },
                }
            })

    if requests:
        _batch_update_with_image_retry(slides_svc, pres_id, requests)

    _log.info("add_customer_logo: applied logo to %s", pres_id)


def update_maturity_journey_slide(
    pres_id: str, maturity_data: list[dict], account_name: str = "", month_label: str = ""
) -> None:
    """Delete all but one arrow group on the maturity journey slide (slide 32).

    Each arrow + label pair is an elementGroup whose nested RIGHT_ARROW contains
    the score as its text (e.g. "2.4").  We calculate the average maturity score
    from maturity_data (same formula as _render_radar), find the group whose score
    is closest to that value, and delete all other scored groups.  The background
    group and title text box are untouched because they contain no scored arrow.

    Only runs for the full-deck template; skipped when the config has no
    maturity_journey_slide_index.
    """
    slide_index = _cfg().get("maturity_journey_slide_index")
    if slide_index is None:
        return

    dimensions = {
        r["dimension"]: float(r["stage"])
        for r in maturity_data if r.get("stage") is not None
    }
    if len(dimensions) < 3:
        _log.warning("update_maturity_journey_slide: too few dimensions (%d), skipping", len(dimensions))
        return
    avg = round(sum(dimensions.values()) / len(dimensions), 1)

    _, slides_svc = _services()
    pres = slides_svc.presentations().get(presentationId=pres_id).execute()
    slides_list = pres.get("slides", [])
    if slide_index >= len(slides_list):
        _log.warning("update_maturity_journey_slide: slide index %d not found", slide_index)
        return

    scored_groups: list[dict] = []  # {"id": ..., "score": float}

    for elem in slides_list[slide_index].get("pageElements", []):
        if "elementGroup" not in elem:
            continue
        for child in elem["elementGroup"].get("children", []):
            if "shape" not in child:
                continue
            if child["shape"].get("shapeType") != "RIGHT_ARROW":
                continue
            parts = []
            for tr in child["shape"].get("text", {}).get("textElements", []):
                c = tr.get("textRun", {}).get("content", "")
                if c.strip():
                    parts.append(c.strip())
            try:
                scored_groups.append({"id": elem["objectId"], "score": float("".join(parts))})
            except (ValueError, TypeError):
                pass
            break  # one arrow per group is enough

    if not scored_groups:
        _log.warning("update_maturity_journey_slide: no scored groups found on slide %d", slide_index)
        return

    best = min(scored_groups, key=lambda g: abs(g["score"] - avg))
    delete_ids = [g["id"] for g in scored_groups if g["id"] != best["id"]]

    if not delete_ids:
        return

    slides_svc.presentations().batchUpdate(
        presentationId=pres_id,
        body={"requests": [{"deleteObject": {"objectId": oid}} for oid in delete_ids]},
    ).execute()
    _log.info(
        "update_maturity_journey_slide: kept group score=%.1f (avg=%.1f), deleted %d groups",
        best["score"], avg, len(delete_ids),
    )

    # Rebuild label text: "Name\nMMM YYYY" with bold name, plain date, auto-fit.
    # Rebuilding from scratch (deleteText + insertText) avoids relying on paragraph
    # index detection which is unreliable after replaceAllText transforms.
    if not account_name:
        return

    import re as _re_date
    _m = _re_date.search(r'([A-Z][a-z]+ \d{4})$', month_label)
    date_str = _m.group(1) if _m else month_label

    best_elem_data = next(
        (e for e in slides_list[slide_index].get("pageElements", [])
         if e["objectId"] == best["id"]),
        None,
    )
    if not best_elem_data:
        return
    label_child = next(
        (c for c in best_elem_data["elementGroup"].get("children", [])
         if "shape" in c and c["shape"].get("shapeType") != "RIGHT_ARROW"),
        None,
    )
    if not label_child:
        return

    label_id = label_child["objectId"]
    label_text = f"{account_name}\n{date_str}"

    # The label text box has effective width ~106pt (3,000,000 EMU × scaleX 0.4489 / 12,700).
    # IBM Plex Mono occupies ~0.7× char-width per pt, so a long name like
    # "Schneider Electric" (18 chars) at 14pt needs ~151pt — it wraps.  Compute
    # the largest font that fits on one line and apply it explicitly so TEXT_AUTOFIT
    # isn't needed (it only fires when text overflows the box height, not the width).
    _BOX_PT = 106
    font_size = max(7, min(14, int(_BOX_PT / (len(account_name) * 0.7))))

    slides_svc.presentations().batchUpdate(
        presentationId=pres_id,
        body={"requests": [
            # Replace all existing text
            {"deleteText": {"objectId": label_id, "textRange": {"type": "ALL"}}},
            {"insertText": {"objectId": label_id, "insertionIndex": 0, "text": label_text}},
            # Remove bold and set font size on everything first
            {"updateTextStyle": {
                "objectId": label_id,
                "textRange": {"type": "ALL"},
                "style": {"bold": False, "fontSize": {"magnitude": font_size, "unit": "PT"}},
                "fields": "bold,fontSize",
            }},
            # Re-apply bold only to the name (first line)
            {"updateTextStyle": {
                "objectId": label_id,
                "textRange": {
                    "type": "FIXED_RANGE",
                    "startIndex": 0,
                    "endIndex": len(account_name),
                },
                "style": {"bold": True, "fontSize": {"magnitude": font_size, "unit": "PT"}},
                "fields": "bold,fontSize",
            }},
        ]},
    ).execute()


def share_presentation(pres_id: str, user_email: str) -> None:
    """Grant the requesting user writer access to the presentation."""
    drive, _ = _services()
    # Files in a Shared Drive have no individual owner, so no transferOwnership
    # needed — writer access is sufficient.
    try:
        drive.permissions().create(
            fileId=pres_id,
            supportsAllDrives=True,
            sendNotificationEmail=False,
            body={"type": "user", "role": "writer", "emailAddress": user_email},
        ).execute()
    except Exception as exc:
        # Ignore duplicate-permission errors (user already has access)
        if "already exists" not in str(exc).lower():
            raise


def share_with_domain(pres_id: str, domain: str = "langchain.dev") -> None:
    """Grant everyone in the domain reader access to the presentation.

    Used when slides are generated via a schedule so any @langchain.dev team
    member who receives the link can open it without needing individual sharing.
    """
    drive, _ = _services()
    try:
        drive.permissions().create(
            fileId=pres_id,
            supportsAllDrives=True,
            sendNotificationEmail=False,
            body={"type": "domain", "role": "reader", "domain": domain},
        ).execute()
    except Exception as exc:
        if "already exists" not in str(exc).lower():
            raise


_DOT_LIT   = {"red": 0.502, "green": 0.784, "blue": 1.0}   # bright blue
_DOT_UNLIT = {"themeColor": "LIGHT2"}                       # matches template dot circles


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


def _usage_score(s14: dict) -> int:
    """Return 1-5 score for the Product Usage slides.

    Two signals, each contributing up to 3 strikes:
      - Commit pacing: pct_commit_used vs pct_into_contract
        (not penalised if contract data is unavailable)
      - Feature breadth: how many distinct LangSmith features were active
        in the last 3 months (traces, agent runs, agent builder, experiments,
        prompts, datasets, page views, evaluators — 8 total)
    """
    pct_commit_used   = s14.get("pct_commit_used")    # fraction 0-1 or None
    pct_into_contract = s14.get("pct_into_contract")  # fraction 0-1 or None
    feature_count     = s14.get("usage_feature_count", 0)

    strikes = 0

    if pct_commit_used is not None and pct_into_contract and pct_into_contract > 0:
        ratio = pct_commit_used / pct_into_contract
        if ratio < 0.45:   strikes += 3
        elif ratio < 0.65: strikes += 2
        elif ratio < 0.85: strikes += 1

    if feature_count == 0:   strikes += 3
    elif feature_count == 1: strikes += 2
    elif feature_count <= 3: strikes += 1

    if strikes >= 5: return 1
    if strikes >= 3: return 2
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
    sla_pct = s14.get("sla_pct")
    observations = s14.get("observations", [])
    opportunities = s14.get("opportunities", [])
    commit_summary = s14.get("commit_summary", "")
    tracing_summary = s14.get("tracing_summary", "")
    feature_summary = s14.get("feature_summary", "")
    usage_summary = s14.get("usage_summary", "")
    commit_observations = s14.get("commit_observations", [])
    commit_opportunities = s14.get("commit_opportunities", [])
    tracing_observations = s14.get("tracing_observations", [])
    tracing_opportunities = s14.get("tracing_opportunities", [])
    feature_observations = s14.get("feature_observations", [])
    feature_opportunities = s14.get("feature_opportunities", [])

    sla_clause = f" {sla_pct}% of tickets within response time SLA." if sla_pct is not None else ""
    sev1_text = (
        f"No pending Sev 1 support tickets.{sla_clause}"
        if sev1 == 0
        else f"{sev1} pending Sev 1 ticket{'s' if sev1 > 1 else ''}.{sla_clause}"
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
    obs_text = "\n".join(observations) if observations else "[TODO]"
    opp_text = "\n".join(opportunities) if opportunities else "[TODO]"
    commit_obs_text = "\n".join(commit_observations) if commit_observations else "[TODO]"
    commit_opp_text = "\n".join(commit_opportunities) if commit_opportunities else "[TODO]"
    tracing_obs_text = "\n".join(tracing_observations) if tracing_observations else "[TODO]"
    tracing_opp_text = "\n".join(tracing_opportunities) if tracing_opportunities else "[TODO]"
    feature_obs_text = "\n".join(feature_observations) if feature_observations else "[TODO]"
    feature_opp_text = "\n".join(feature_opportunities) if feature_opportunities else "[TODO]"

    # Enablement & Training slide (slide 26)
    # Only use billable_seats as denominator — est_engineering_headcount can be
    # tens of thousands for large enterprises, making the fraction meaningless.
    # If seats is 0 (e.g. self-hosted), show just the enrolled count.
    academy_enrolled = s14.get("academy_enrolled", 0)
    billable_seats   = s14.get("billable_seats", 0)
    if academy_enrolled and billable_seats:
        enablement_stat = f"{academy_enrolled}/{billable_seats} Agent Engineers trained on LangSmith"
        raw_pct = academy_enrolled / billable_seats * 100
        pct_str = f"{raw_pct:.1f}%" if raw_pct < 1 else f"{raw_pct:.0f}%"
        enablement_pct  = f"{pct_str} of Engineers are enabled"
    else:
        # Without billable_seats the enrolled count has no denominator context and
        # is not directly visible on the slide — show [TODO] rather than a bare number.
        enablement_stat = "[TODO]"
        total_sign_ups = s14.get("total_sign_ups", 0)
        num_courses    = s14.get("num_courses", 0)
        if total_sign_ups and num_courses:
            enablement_pct = f"{total_sign_ups} sign-ups across {num_courses} courses"
        else:
            enablement_pct = None

    cfg = _cfg()

    replacements = {
        # Enterprise Support slide
        "{sev1 status}": sev1_text,
        "{{OBSERVATIONS}}": obs_text,
        "{{OPPORTUNITIES}}": opp_text,
        # LangSmith Usage slides (22-24) — AI-generated from BQ chart data, per-slide
        "{commit summary}": commit_summary or "[TODO]",
        "{commit observations}": commit_obs_text,
        "{commit opportunities}": commit_opp_text,
        "{tracing summary}": tracing_summary or "[TODO]",
        "{tracing observations}": tracing_obs_text,
        "{tracing opportunities}": tracing_opp_text,
        "{feature summary}": feature_summary or "[TODO]",
        "{feature observations}": feature_obs_text,
        "{feature opportunities}": feature_opp_text,
        # LangSmith Engagement Scorecard — rollup summary across all 3 usage slides
        "{usage summary}": usage_summary or "[TODO]",
        # Product Feedback slide
        "{{FEATURE_REQUEST_LIST}}": fr_list,
        "{feature request summary}": summary_line,
        # Enablement & Training slide — replace template placeholders
        "{enablement session note}": f"Instructor-led in-person session for [X] {account_name} professionals",
        "{enablement stat}": enablement_stat,
        "{enablement pct}": enablement_pct or "[TODO]",
        # Global token — replaces [Customer] / [CUSTOMER] across all slides
        "[Customer]": account_name,
        "[CUSTOMER]": account_name,
    }

    score       = _health_score(s14)
    fr_score    = _fr_score(s15)
    usage_score = _usage_score(s14)

    def _dot_reqs(ids: list, threshold: int) -> list:
        return [
            {
                "updateShapeProperties": {
                    "objectId": obj_id,
                    "shapeProperties": {
                        "shapeBackgroundFill": {
                            "solidFill": {"color": {"rgbColor": _DOT_LIT} if i < threshold else _DOT_UNLIT}
                        }
                    },
                    "fields": "shapeBackgroundFill",
                }
            }
            for i, obj_id in enumerate(ids)
        ]

    dot_requests = (
        _dot_reqs(cfg["dot_ids"], score)
        + _dot_reqs(cfg["slide2_dot_ids"], fr_score)
        # Product Usage slides — one row of dots per slide, all same score
        + [req for row in cfg.get("usage_dot_id_rows", []) for req in _dot_reqs(row, usage_score)]
        # Engagement Scorecard rows — mirror the same scores
        + _dot_reqs(cfg.get("scorecard_support_dot_ids", []), score)
        + _dot_reqs(cfg.get("scorecard_fr_dot_ids", []), fr_score)
        + _dot_reqs(cfg.get("scorecard_usage_dot_ids", []), usage_score)
    )

    return [
        {
            "replaceAllText": {
                "containsText": {"text": old, "matchCase": False},
                "replaceText": new,
            }
        }
        for old, new in replacements.items()
    ] + dot_requests
