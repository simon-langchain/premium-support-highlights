"""Chart image generation for QBR slides.

Two rendering paths:
  BQ path (production): fetch_chart_data() from bigquery_client → *_from_bq() functions
    render matplotlib charts directly from BigQuery row dicts with no Hex dependency.
  Hex path (utility): fetch_chart_images() triggers a Hex notebook run and downloads
    cell PNG images. Used by check_hex_cells.py for visual validation only.

Required env vars (Hex path only):
  HEX_API_TOKEN   — Personal access token (hxtp_...) or workspace token
  HEX_PROJECT_ID  — UUID of the Enterprise Customer 360 notebook
"""

import base64
import json
import logging
import os
import time

import httpx

_log = logging.getLogger(__name__)

_HEX_BASE = "https://app.hex.tech/api/v1"

# Must be EXPLORE (native Hex chart) cells — CODE/Plotly cells return 422 from the image API.
MATURITY_CHART_CELL_ID = "019cec77-7b63-7331-8f40-3fd7cd8b26f3"

COMMIT_USAGE_CELL_ID           = "019c4d4d-e24a-7003-a30a-5a665348a7c2"  # Total Usage in Contract Period (Running Total)
TRACES_BY_MONTH_CELL_ID        = "019c4d4d-e249-7003-a309-f8f279de1302"
MONTHLY_PAGE_VIEWS_CELL_ID     = "019c4d4d-e24a-7003-a30a-63cb6162a3e0"
AGENT_RUNS_MONTHLY_CELL_ID     = "019c4d4d-e24a-7003-a30a-0db9872ff29b"
AGENT_BUILDER_RUNS_CELL_ID     = "019cb570-9d69-7008-a56c-26d3e3ac1b04"

# Feature usage cells (slide 25) — EXPLORE cells, all with staticValue colours
EXPERIMENTS_RUN_CELL_ID        = "019c4d4d-e24a-7003-a30a-1ef8d370a56a"
PROMPT_COMMITS_CELL_ID         = "019c4d4d-e24a-7003-a30a-22775b6e0f5c"
PROMPT_PULLS_CELL_ID           = "019c4d4d-e24a-7003-a30a-392ccb41bc56"
DATASETS_CREATED_CELL_ID       = "019c4d4d-e24a-7003-a30a-31c929295b02"
EVALUATOR_RULES_CELL_ID        = "019ccb0c-1d37-7004-9329-8f559335d0e2"

# Hex default Vega-Lite categorical palette (used when colorMappings is empty)
_HEX_DEFAULT_PALETTE = [
    "#4C78A8", "#F58518", "#E45756", "#72B7B2",
    "#54A24B", "#EECA3B", "#B279A2", "#FF9DA6",
]
# Series-name → colour from each chart's colorMappings in the YAML
_HEX_SERIES_COLORS: dict[str, str] = {
    # Agent Runs Monthly
    "actual_agent_runs":   "#F58518",
    "billable_agent_runs": "#4C78A8",
    # Monthly Page Views (by product area)
    "Observability":       "#54A24B",
    "Agent Builder":       "#E45756",
    "Datasets":            "#F58518",
    "Prompts":             "#4C78A8",
    "Deployments":         "#72B7B2",
    "Other":               "#EECA3B",
    # Commit Usage Running Total (colorMappings from YAML)
    "running_total_traces":                   "#4C78A8",
    "running_total_nodes":                    "#F58518",
    "running_total_agent_runs":               "#E45756",
    "billable_agent_builder_runs":            "#54A24B",
    "running_billable_agent_builder_runs":    "#EECA3B",
    "running_billable_longlived_trace_count": "#72B7B2",
    # Human-readable display names (after _HEX_SERIES_LABELS mapping)
    "Running Total Traces":                   "#4C78A8",
    "Running Total Nodes":                    "#F58518",
    "Running Total Agent Runs":               "#E45756",
    "Running Billable Agent Builder Runs":    "#EECA3B",
    "Running Billable Long-lived Traces":     "#72B7B2",
    # Compact forms Vision may extract from a truncated legend
    "Total Traces":        "#4C78A8",
    "Total Nodes":         "#F58518",
    "Agent Runs":          "#E45756",
    "Agent Builder Runs":  "#EECA3B",
    "Long-lived Traces":   "#72B7B2",
    # Evaluator Rules by Month (colorMappings from YAML)
    "LLM":                 "#F58518",
    "Code":                "#4C78A8",
    "LLM - Online":        "#E45756",
    "Code - Online":       "#54A24B",
    "LLM - Offline":       "#72B7B2",
    "Code - Offline":      "#EECA3B",
    "Self-hosted total":   "#B279A2",
}
# Cell-level static colours from chartConfig.series[].color.staticValue in the YAML.
# These override colorMappings and series-name lookups — they are the true rendered colours.
_HEX_CELL_STATIC_COLORS: dict[str, str] = {
    TRACES_BY_MONTH_CELL_ID:    "#2CA58D",  # teal
    MONTHLY_PAGE_VIEWS_CELL_ID: "#F58518",  # orange
    AGENT_RUNS_MONTHLY_CELL_ID: "#E11D48",  # red
    AGENT_BUILDER_RUNS_CELL_ID: "#8B5CF6",  # purple
    # Feature usage charts (slide 25)
    EXPERIMENTS_RUN_CELL_ID:    "#7B61FF",  # purple
    PROMPT_COMMITS_CELL_ID:     "#F97316",  # orange
    PROMPT_PULLS_CELL_ID:       "#0EA5E9",  # sky blue
    DATASETS_CREATED_CELL_ID:   "#D97706",  # amber
    # EVALUATOR_RULES_CELL_ID uses colorMappings (no staticValue)
}
# Y-axis labels from each chart's cross-axis field title in the YAML.
_HEX_CHART_Y_LABELS: dict[str, str] = {
    TRACES_BY_MONTH_CELL_ID:    "Billable Traces",
    MONTHLY_PAGE_VIEWS_CELL_ID: "Page Views",
    AGENT_RUNS_MONTHLY_CELL_ID: "Agent Runs",
    AGENT_BUILDER_RUNS_CELL_ID: "Agent Builder Runs",
    # Feature usage charts (slide 25)
    EXPERIMENTS_RUN_CELL_ID:    "Experiments Run",
    PROMPT_COMMITS_CELL_ID:     "Prompt Commits",
    PROMPT_PULLS_CELL_ID:       "Prompt Pulls",
    DATASETS_CREATED_CELL_ID:   "Datasets Created",
    EVALUATOR_RULES_CELL_ID:    "Evaluator Rules Created",
}
# Raw column name → human-readable legend label for multi-series line charts.
# Applied when rendering so the legend shows "Running Total Traces" not "running_total_traces".
_HEX_SERIES_LABELS: dict[str, str] = {
    "running_total_traces":                   "Running Total Traces",
    "running_total_nodes":                    "Running Total Nodes",
    "running_total_agent_runs":               "Running Total Agent Runs",
    "running_billable_agent_builder_runs":    "Running Billable Agent Builder Runs",
    "running_billable_longlived_trace_count": "Running Billable Long-lived Traces",
    "billable_agent_builder_runs":            "Billable Agent Builder Runs",
    # Compact forms Vision may extract from a truncated legend
    "Total Traces":       "Running Total Traces",
    "Total Nodes":        "Running Total Nodes",
    "Agent Runs":         "Running Total Agent Runs",
    "Agent Builder Runs": "Running Billable Agent Builder Runs",
    "Long-lived Traces":  "Running Billable Long-lived Traces",
}


def _snake_to_title(name: str) -> str:
    """'running_total_traces' → 'Running Total Traces' (fallback for unknown names)."""
    return name.replace("_", " ").title()


# Fixed display titles and order for the 4 usage charts
LANGSMITH_USAGE_CHART_META: dict[str, str] = {
    TRACES_BY_MONTH_CELL_ID:    "Traces by Month",
    MONTHLY_PAGE_VIEWS_CELL_ID: "Monthly Page Views",
    AGENT_RUNS_MONTHLY_CELL_ID: "Agent Runs Monthly",
    AGENT_BUILDER_RUNS_CELL_ID: "Agent Builder Runs Monthly",
}

# Fixed display titles for the 5 feature usage charts (slide 25)
FEATURE_USAGE_CHART_META: dict[str, str] = {
    EXPERIMENTS_RUN_CELL_ID:  "Experiments Run Monthly",
    PROMPT_COMMITS_CELL_ID:   "Prompt Commits Monthly",
    PROMPT_PULLS_CELL_ID:     "Prompt Pulls Monthly",
    DATASETS_CREATED_CELL_ID: "Datasets Created Monthly",
    EVALUATOR_RULES_CELL_ID:  "Evaluator Rules by Month",
}
def _headers() -> dict:
    token = os.environ.get("HEX_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HEX_API_TOKEN is not configured")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _trigger_run(project_id: str, metronome_id: str) -> str | None:
    """Start a Hex run and return the runId, or None on failure."""
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{_HEX_BASE}/projects/{project_id}/runs",
                headers=_headers(),
                json={
                    "inputParams": {"metronome_id_text_input": metronome_id},
                    "dryRun": False,
                    "updatePublishedResults": False,
                    "useCachedSqlResults": True,
                },
            )
            if not resp.is_success:
                _log.warning("Hex run trigger failed: %s %s — %s", resp.status_code, resp.url, resp.text)
                return None
            resp.raise_for_status()
            run_id = resp.json().get("runId")
    except Exception as exc:
        _log.warning("Hex run trigger failed: %s", exc)
        return None

    if not run_id:
        _log.warning("Hex response missing runId")
        return None

    _log.info("Hex run started: runId=%s project=%s metronome=%s", run_id, project_id, metronome_id)
    return run_id


def _poll_until_complete(project_id: str, run_id: str, timeout: float = 300.0) -> bool:
    """Poll until the run reaches a terminal state. Returns True if COMPLETED."""
    terminal = {"COMPLETED", "FAILED", "KILLED", "ERROR_REPORTED"}
    deadline = time.monotonic() + timeout
    run_status = ""

    while time.monotonic() < deadline:
        time.sleep(5)
        try:
            with httpx.Client(timeout=15) as client:
                r = client.get(
                    f"{_HEX_BASE}/projects/{project_id}/runs/{run_id}",
                    headers=_headers(),
                )
                r.raise_for_status()
                run_status = r.json().get("status", "")
        except Exception as exc:
            _log.warning("Hex status poll error: %s", exc)
            continue
        _log.debug("Hex run %s status: %s", run_id, run_status)
        if run_status in terminal:
            break
    else:
        _log.warning("Hex run %s timed out after %.0fs", run_id, timeout)
        return False

    if run_status != "COMPLETED":
        _log.warning("Hex run %s ended with status: %s", run_id, run_status)
        return False

    return True


def fetch_chart_images(
    metronome_id: str,
    static_ids: list[str] | None = None,
    timeout: float = 300.0,
) -> dict[str, bytes]:
    """Trigger a Hex run and return rendered chart images keyed by staticId.

    Returns an empty dict if Hex is not configured or the run fails.
    static_ids defaults to [MATURITY_CHART_CELL_ID].
    """
    project_id = os.environ.get("HEX_PROJECT_ID", "").strip()
    if not project_id:
        _log.info("HEX_PROJECT_ID not set — skipping Hex chart fetch")
        return {}

    if not metronome_id:
        return {}

    if static_ids is None:
        static_ids = [MATURITY_CHART_CELL_ID]

    run_id = _trigger_run(project_id, metronome_id)
    if not run_id:
        return {}

    if not _poll_until_complete(project_id, run_id, timeout):
        return {}

    images: dict[str, bytes] = {}
    for static_id in static_ids:
        try:
            with httpx.Client(timeout=30) as client:
                r = client.get(
                    f"{_HEX_BASE}/projects/{project_id}/runs/{run_id}/cells/{static_id}/image",
                    headers=_headers(),
                )
                r.raise_for_status()
                data = r.json()
            img_b64 = data.get("imageBase64") or data.get("image")
            if img_b64:
                images[static_id] = base64.b64decode(img_b64)
                _log.info("Hex chart image fetched: staticId=%s bytes=%d", static_id, len(images[static_id]))
            else:
                _log.warning("Hex chart image response missing imageBase64 for %s: %s", static_id, data)
        except Exception as exc:
            _log.warning("Hex chart image fetch failed for %s: %s", static_id, exc)

    return images



# ---------------------------------------------------------------------------
# Radar chart generation — render from BigQuery data via matplotlib.
# ---------------------------------------------------------------------------

def _render_radar(dimensions: dict[str, float], customer_name: str) -> bytes:
    """Render a radar/spider chart matching the Hex notebook style."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from io import BytesIO

    dim_names = list(dimensions.keys())
    stages = [float(dimensions[d]) for d in dim_names]
    N = len(dim_names)

    avg = round(sum(stages) / N, 1)
    if avg < 1.5:
        overall_label = "Exploring"
    elif avg < 2.5:
        overall_label = "Building"
    elif avg < 3.5:
        overall_label = "Operating"
    else:
        overall_label = "Scaling"

    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles_plot = angles + [angles[0]]
    stages_plot = stages + [stages[0]]

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(polar=True))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f0f2ff")

    ax.fill(angles_plot, stages_plot, alpha=0.30, color="#5c6bc0", zorder=1)
    ax.plot(angles_plot, stages_plot, "o-", linewidth=2.5, color="#5c6bc0", markersize=7, zorder=2)

    ax.set_ylim(0, 4)
    ax.set_yticks([1, 2, 3, 4])
    ax.set_yticklabels(
        ["Exploring", "Building", "Operating", "Scaling"],
        fontsize=8, color="#555555",
    )
    ax.yaxis.set_tick_params(pad=3)

    ax.set_xticks(angles)
    ax.set_xticklabels(dim_names, fontsize=11, color="#1a1a2e")
    for lbl in ax.get_xticklabels():
        lbl.set_zorder(10)

    ax.grid(color="#cccccc", linewidth=0.8)
    ax.spines["polar"].set_color("#cccccc")

    title = (
        f"Agent Engineering Maturity — {customer_name}\n"
        f"Overall: {avg}/4.0 ({overall_label})"
    )
    ax.set_title(title, size=13, color="#1a1a2e", y=1.12)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _month_label(d) -> str:
    """Convert a date/datetime/ISO string to 'Jan 2025' label."""
    from datetime import date, datetime
    if isinstance(d, (date, datetime)):
        return d.strftime("%b %Y")
    try:
        return datetime.fromisoformat(str(d)).strftime("%b %Y")
    except Exception:
        return str(d)


def create_usage_composite_from_bq(chart_data: dict) -> bytes:
    """Build the 4-chart LangSmith usage composite directly from BigQuery row dicts.

    chart_data must contain "monthly_usage" and optionally "page_views" lists
    (as returned by bigquery_client.fetch_chart_data).

    Returns empty bytes if all series have zero values.
    Uses the exact same dark-theme rendering as create_usage_composite.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    import numpy as np
    from io import BytesIO as _BytesIO

    BG     = "#0c0d1a"
    CARD   = "#161729"
    TEXT   = "#e2e8f0"
    MUTED  = "#94a3b8"
    BORDER = "#2d3148"

    def _fmt(v, _=None):
        if abs(v) >= 1e9: return f"{v / 1e9:.1f}B"
        if abs(v) >= 1e6: return f"{v / 1e6:.0f}M"
        if abs(v) >= 1e3: return f"{v / 1e3:.0f}K"
        return str(int(v))

    monthly = chart_data.get("monthly_usage", [])
    page_views = chart_data.get("page_views", [])

    # Build 4 chart dicts in the same shape as _extract_chart_data returns
    def _monthly_series(rows, x_field, y_field, title, cell_id):
        x_labels = [_month_label(r[x_field]) for r in rows]
        values   = [float(r.get(y_field) or 0) for r in rows]
        return {
            "title":    title,
            "x_labels": x_labels,
            "series":   [{"name": y_field, "values": values}],
            "_cell_id": cell_id,
        }

    # Page views has a different x-field name
    pv_labels = [_month_label(r["event_month"]) for r in page_views]
    pv_values = [float(r.get("total_page_views") or 0) for r in page_views]
    page_views_chart = {
        "title":    LANGSMITH_USAGE_CHART_META[MONTHLY_PAGE_VIEWS_CELL_ID],
        "x_labels": pv_labels,
        "series":   [{"name": "total_page_views", "values": pv_values}],
        "_cell_id": MONTHLY_PAGE_VIEWS_CELL_ID,
    }

    candidate_charts = [
        _monthly_series(monthly, "month_start", "billable_traces",
                        LANGSMITH_USAGE_CHART_META[TRACES_BY_MONTH_CELL_ID],
                        TRACES_BY_MONTH_CELL_ID),
        page_views_chart,
        _monthly_series(monthly, "month_start", "billable_agent_runs",
                        LANGSMITH_USAGE_CHART_META[AGENT_RUNS_MONTHLY_CELL_ID],
                        AGENT_RUNS_MONTHLY_CELL_ID),
        _monthly_series(monthly, "month_start", "billable_agent_builder_runs",
                        LANGSMITH_USAGE_CHART_META[AGENT_BUILDER_RUNS_CELL_ID],
                        AGENT_BUILDER_RUNS_CELL_ID),
    ]

    # Skip charts where all values are zero
    charts = [
        c for c in candidate_charts
        if c["x_labels"] and any(v > 0 for s in c["series"] for v in s["values"])
    ]

    n = len(charts)
    if n == 0:
        return b""

    if n == 1:
        fig, axes_flat = plt.subplots(1, 1, figsize=(12, 7))
        axes_flat = [axes_flat]
    elif n == 2:
        fig, axes_flat = plt.subplots(1, 2, figsize=(16, 7))
    else:
        fig, arr = plt.subplots(2, 2, figsize=(16, 12))
        axes_flat = list(arr.flatten())

    fig.patch.set_facecolor(BG)

    for i, data in enumerate(charts):
        ax = axes_flat[i]
        ax.set_facecolor(CARD)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER)
            spine.set_linewidth(0.8)
        ax.tick_params(colors=MUTED, labelsize=11)
        ax.set_title(data.get("title", ""), color=TEXT, fontsize=13, pad=6)
        ax.grid(axis="y", color=BORDER, linewidth=0.5, alpha=0.7, zorder=0)

        cell_id = data.get("_cell_id", "")
        y_label = _HEX_CHART_Y_LABELS.get(cell_id, "")
        if y_label:
            ax.set_ylabel(y_label, color=MUTED, fontsize=11)

        x_labels = data.get("x_labels", [])
        series   = data.get("series", [])
        x   = np.arange(len(x_labels))
        n_s = len(series)
        bar_w = 0.8 / max(n_s, 1)

        all_vals = [v for s in series for v in s.get("values", []) if v is not None]
        max_val  = max(all_vals) if all_vals else 0

        for j, s in enumerate(series):
            vals = (s.get("values", []) + [0] * len(x_labels))[:len(x_labels)]
            # Cell static colour takes priority so single-series charts always use the
            # canonical Hex colour for that slot, regardless of the BQ column name.
            color = (
                _HEX_CELL_STATIC_COLORS.get(cell_id)
                or _HEX_SERIES_COLORS.get(s.get("name", ""))
                or _HEX_DEFAULT_PALETTE[j % len(_HEX_DEFAULT_PALETTE)]
            )
            offset = (j - n_s / 2 + 0.5) * bar_w
            ax.bar(x + offset, vals, width=bar_w, color=color, alpha=0.9, zorder=2,
                   label=s.get("name", "") if n_s > 1 else None)

            threshold = 0.10 * max_val if max_val > 0 else 0
            for xi, vi in zip(x + offset, vals):
                if vi >= threshold and vi > 0:
                    ax.text(
                        xi, vi * 0.96, _fmt(vi),
                        ha="center", va="top",
                        color="white", fontsize=9, fontweight="bold", zorder=3,
                    )

        ax.yaxis.set_major_formatter(FuncFormatter(_fmt))
        step  = max(1, len(x_labels) // 6)
        shown = sorted(set(range(0, len(x_labels), step)) | {len(x_labels) - 1})
        shown = [k for k in shown if k < len(x_labels)]
        ax.set_xticks(shown)
        ax.set_xticklabels([x_labels[k] for k in shown],
                           rotation=30, ha="right", color=MUTED, fontsize=11)
        ax.set_xlim(-0.5, len(x_labels) - 0.5)

        if n_s > 1:
            ax.legend(fontsize=10, labelcolor=TEXT, facecolor=CARD, edgecolor=BORDER)

    if n == 3:
        axes_flat[3].set_visible(False)

    plt.tight_layout(pad=1.2)
    buf = _BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def create_feature_usage_composite_from_bq(chart_data: dict) -> bytes:
    """Build the 5-chart feature usage composite (slide 25) from BigQuery row dicts.

    chart_data must contain "monthly_usage" and optionally "evaluator_usage" lists
    (as returned by bigquery_client.fetch_chart_data).

    All 5 slots are always rendered — charts with no data show a "No data" placeholder.
    Uses the exact same dark-theme 3+2 grid rendering as create_feature_usage_composite.
    """
    import math
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
    from matplotlib.ticker import FuncFormatter
    import numpy as np
    from io import BytesIO as _BytesIO

    BG     = "#0c0d1a"
    CARD   = "#161729"
    TEXT   = "#e2e8f0"
    MUTED  = "#94a3b8"
    BORDER = "#2d3148"

    def _fmt(v, _=None):
        if abs(v) >= 1e9: return f"{v / 1e9:.1f}B"
        if abs(v) >= 1e6: return f"{v / 1e6:.0f}M"
        if abs(v) >= 1e3: return f"{v / 1e3:.0f}K"
        return str(int(v))

    def _nice_ceiling(value: float) -> float:
        """Round up to a "nice" axis max (1/2/5 x a power of 10).

        A single-bar chart's y-axis would otherwise auto-scale tightly to
        that one value, making the bar fill ~95% of the panel regardless of
        whether the number is actually large or small. Rounding to a nice
        ceiling gives real headroom, so fill % roughly tracks magnitude
        instead of always looking "full".
        """
        if value <= 0:
            return 1.0
        exponent = math.floor(math.log10(value))
        base = 10 ** exponent
        for mult in (1, 2, 5, 10):
            if value <= mult * base:
                return mult * base
        return 10 * base

    monthly = chart_data.get("monthly_usage", [])
    evaluator_rows = chart_data.get("evaluator_usage", [])

    def _simple_chart(rows, x_field, y_field, title, cell_id):
        """Return chart dict or None if all values are zero / no rows."""
        if not rows:
            return None
        x_labels = [_month_label(r[x_field]) for r in rows]
        values   = [float(r.get(y_field) or 0) for r in rows]
        if not any(v > 0 for v in values):
            return None
        nonzero = [i for i, v in enumerate(values) if v > 0]
        if len(nonzero) == 1 and nonzero[0] == len(values) - 1:
            # Only the latest month has a real number — e.g. self-hosted
            # customers, whose historical experiments/prompt/dataset counts
            # aren't tracked upstream, only a current-state snapshot (see
            # bigquery_client's monthly_usage query). A month-by-month bar
            # chart here would be almost entirely empty bars implying a
            # trend that doesn't exist; show the one real number as a
            # single current-usage bar instead.
            return {
                "title":         title,
                "snapshot_only": True,
                "value":         values[-1],
                "_cell_id":      cell_id,
            }
        return {
            "title":    title,
            "x_labels": x_labels,
            "series":   [{"name": y_field, "values": values}],
            "_cell_id": cell_id,
        }

    # Build evaluator multi-series chart
    def _evaluator_chart():
        if not evaluator_rows:
            return None
        # Collect sorted unique months and categories
        months_set = sorted({r["month_start"] for r in evaluator_rows})
        cats_set   = sorted({r["eval_category"] for r in evaluator_rows})
        if not months_set or not cats_set:
            return None
        # Build lookup: (month_start, eval_category) → rules
        lookup: dict[tuple, float] = {}
        for r in evaluator_rows:
            lookup[(r["month_start"], r["eval_category"])] = float(r.get("rules") or 0)
        x_labels = [_month_label(m) for m in months_set]
        series = []
        for cat in cats_set:
            values = [lookup.get((m, cat), 0.0) for m in months_set]
            series.append({"name": cat, "values": values})
        # Return None if truly all zeros
        if not any(v > 0 for s in series for v in s["values"]):
            return None
        return {
            "title":    FEATURE_USAGE_CHART_META[EVALUATOR_RULES_CELL_ID],
            "x_labels": x_labels,
            "series":   series,
            "_cell_id": EVALUATOR_RULES_CELL_ID,
        }

    extracted = [
        (_simple_chart(monthly, "month_start", "total_experiments",
                       FEATURE_USAGE_CHART_META[EXPERIMENTS_RUN_CELL_ID],
                       EXPERIMENTS_RUN_CELL_ID),
         EXPERIMENTS_RUN_CELL_ID),
        (_simple_chart(monthly, "month_start", "total_prompt_commits",
                       FEATURE_USAGE_CHART_META[PROMPT_COMMITS_CELL_ID],
                       PROMPT_COMMITS_CELL_ID),
         PROMPT_COMMITS_CELL_ID),
        (_simple_chart(monthly, "month_start", "total_prompt_pulls",
                       FEATURE_USAGE_CHART_META[PROMPT_PULLS_CELL_ID],
                       PROMPT_PULLS_CELL_ID),
         PROMPT_PULLS_CELL_ID),
        (_simple_chart(monthly, "month_start", "total_datasets",
                       FEATURE_USAGE_CHART_META[DATASETS_CREATED_CELL_ID],
                       DATASETS_CREATED_CELL_ID),
         DATASETS_CREATED_CELL_ID),
        (_evaluator_chart(), EVALUATOR_RULES_CELL_ID),
    ]

    # 3+2 layout
    fig = plt.figure(figsize=(18, 12), facecolor=BG)
    gs  = GridSpec(2, 6, figure=fig, hspace=0.35, wspace=0.35,
                   top=0.95, bottom=0.07, left=0.06, right=0.98)

    top_slices    = [slice(0, 2), slice(2, 4), slice(4, 6)]
    bottom_slices = [slice(0, 3), slice(3, 6)]
    ax_specs = [(0, s) for s in top_slices] + [(1, s) for s in bottom_slices]

    for idx, ((data, cell_id), (row, col_slice)) in enumerate(zip(extracted, ax_specs)):
        ax = fig.add_subplot(gs[row, col_slice])
        ax.set_facecolor(CARD)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER)
            spine.set_linewidth(0.8)
        ax.tick_params(colors=MUTED, labelsize=11)

        title   = FEATURE_USAGE_CHART_META.get(cell_id, "")
        y_label = _HEX_CHART_Y_LABELS.get(cell_id, "")
        ax.set_title(title, color=TEXT, fontsize=13, pad=5)
        if y_label:
            ax.set_ylabel(y_label, color=MUTED, fontsize=11)
        ax.grid(axis="y", color=BORDER, linewidth=0.5, alpha=0.7, zorder=0)

        if data is None:
            ax.text(0.5, 0.5, "No data", transform=ax.transAxes,
                    ha="center", va="center", color=MUTED, fontsize=14)
            ax.set_xticks([])
            ax.set_yticks([])
            continue

        if data.get("snapshot_only"):
            value = data["value"]
            color = _HEX_CELL_STATIC_COLORS.get(cell_id) or _HEX_DEFAULT_PALETTE[0]
            ax.bar([0], [value], width=0.4, color=color, alpha=0.9, zorder=2)
            ax.text(0, value * 0.96, _fmt(value), ha="center", va="top",
                    color="white", fontsize=12, fontweight="bold", zorder=3)
            ax.set_xticks([0])
            ax.set_xticklabels(["Current"], color=MUTED, fontsize=11)
            ax.set_xlim(-1, 1)
            ax.set_ylim(0, _nice_ceiling(value))
            ax.yaxis.set_major_formatter(FuncFormatter(_fmt))
            continue

        x_labels = data.get("x_labels", [])
        series   = data.get("series", [])
        x   = np.arange(len(x_labels))
        n_s = len(series)
        stacked = cell_id == EVALUATOR_RULES_CELL_ID and n_s > 1
        bar_w = 0.8

        all_vals = [v for s in series for v in s.get("values", []) if v is not None]
        max_val  = max(all_vals) if all_vals else 0

        bottoms = np.zeros(len(x_labels))
        for j, s in enumerate(series):
            vals = np.array((s.get("values", []) + [0] * len(x_labels))[:len(x_labels)], dtype=float)
            color = (
                _HEX_CELL_STATIC_COLORS.get(cell_id)
                or _HEX_SERIES_COLORS.get(s.get("name", ""))
                or _HEX_DEFAULT_PALETTE[j % len(_HEX_DEFAULT_PALETTE)]
            )
            if stacked:
                ax.bar(x, vals, width=bar_w, bottom=bottoms, color=color, alpha=0.9,
                       zorder=2, label=s.get("name", ""))
                bottoms += vals
            else:
                bar_w_grouped = 0.8 / max(n_s, 1)
                offset = (j - n_s / 2 + 0.5) * bar_w_grouped
                ax.bar(x + offset, vals, width=bar_w_grouped, color=color, alpha=0.9,
                       zorder=2, label=s.get("name", "") if n_s > 1 else None)

                threshold = 0.10 * max_val if max_val > 0 else 0
                for xi, vi in zip(x + offset, vals):
                    if vi >= threshold and vi > 0:
                        ax.text(xi, vi * 0.96, _fmt(vi),
                                ha="center", va="top",
                                color="white", fontsize=9, fontweight="bold", zorder=3)

        ax.yaxis.set_major_formatter(FuncFormatter(_fmt))
        step  = max(1, len(x_labels) // 6)
        shown = sorted(set(range(0, len(x_labels), step)) | {len(x_labels) - 1})
        shown = [k for k in shown if k < len(x_labels)]
        ax.set_xticks(shown)
        ax.set_xticklabels([x_labels[k] for k in shown],
                           rotation=30, ha="right", color=MUTED, fontsize=11)
        ax.set_xlim(-0.5, len(x_labels) - 0.5)

        if n_s > 1:
            ax.legend(fontsize=10, labelcolor=TEXT, facecolor=CARD, edgecolor=BORDER)

    buf = _BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def build_commit_usage_from_bq(chart_data: dict) -> bytes:
    """Render the cumulative commit-usage line chart with KPI tiles from BigQuery row dicts.

    chart_data must contain "cumulative_usage" (list of daily row dicts as returned by
    bigquery_client.fetch_chart_data).  If chart_data also contains "contract_metrics"
    (a dict with contract_end_date, pct_into_contract, pct_commit_used), four KPI tiles
    are rendered above the line chart.

    Series rendered: running_total_traces, running_total_nodes, running_total_agent_runs,
    running_billable_longlived_trace_count, running_billable_agent_builder_runs.

    Returns empty bytes if cumulative_usage has no rows.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
    from matplotlib.ticker import FuncFormatter
    import numpy as np
    from io import BytesIO as _BytesIO
    from datetime import date as _date, datetime as _datetime

    BG     = "#0c0d1a"
    CARD   = "#161729"
    TEXT   = "#e2e8f0"
    MUTED  = "#94a3b8"
    BORDER = "#2d3148"
    ACCENT = "#3b82f6"

    rows = chart_data.get("cumulative_usage", [])
    if not rows:
        return b""

    # Derive total traces from last cumulative row
    total_traces = float(rows[-1].get("running_total_traces") or 0) if rows else 0.0

    # Build KPI tile values from contract_metrics
    cm = chart_data.get("contract_metrics") or {}
    show_tiles = bool(cm)

    def _fmt_date(d) -> str:
        if isinstance(d, (_date, _datetime)):
            return d.strftime("%Y-%m-%d")
        try:
            return _datetime.fromisoformat(str(d)).strftime("%Y-%m-%d")
        except Exception:
            return str(d) if d else "N/A"

    def _fmt_pct(v) -> str:
        if v is None:
            return "N/A"
        return f"{float(v) * 100:.0f}%"

    kpi_tiles = [
        ("Current Contract End Date",  _fmt_date(cm.get("contract_end_date"))),
        ("% into Contract Period",     _fmt_pct(cm.get("pct_into_contract"))),
        ("% Commit Used",              _fmt_pct(cm.get("pct_commit_used"))),
        ("Total Traces",               f"{int(total_traces):,}"),
    ] if show_tiles else []

    def _fmt(v, _=None):
        return f"{int(v):,}"

    def _day_label(d) -> str:
        if isinstance(d, (_date, _datetime)):
            return d.strftime("%b %d")
        try:
            return _datetime.fromisoformat(str(d)).strftime("%b %d")
        except Exception:
            return str(d)

    x_labels = [_day_label(r["usage_date"]) for r in rows]

    # Series columns to plot (same as _COMMIT_COLOR_TO_SERIES targets)
    _SERIES_COLS = [
        "running_total_traces",
        "running_total_nodes",
        "running_total_agent_runs",
        "running_billable_longlived_trace_count",
        "running_billable_agent_builder_runs",
    ]

    series = []
    for col in _SERIES_COLS:
        values = [float(r.get(col) or 0) for r in rows]
        series.append({"name": col, "values": values})

    x = np.arange(len(x_labels))

    fig = plt.figure(figsize=(16, 10), facecolor=BG)
    if kpi_tiles:
        gs = GridSpec(2, 4, figure=fig, height_ratios=[1, 3.5],
                      hspace=0.08, wspace=0.04,
                      top=0.97, bottom=0.08, left=0.06, right=0.99)
        # Render KPI tiles in the top row
        for i, (label, value) in enumerate(kpi_tiles):
            tax = fig.add_subplot(gs[0, i])
            tax.set_facecolor(CARD)
            for spine in tax.spines.values():
                spine.set_edgecolor(BORDER)
                spine.set_linewidth(0.8)
            tax.set_xticks([])
            tax.set_yticks([])
            tax.text(0.5, 0.58, value, transform=tax.transAxes,
                     ha="center", va="center", color=TEXT,
                     fontsize=20, fontweight="bold")
            tax.text(0.5, 0.38, label, transform=tax.transAxes,
                     ha="center", va="center", color=MUTED,
                     fontsize=12)
        ax = fig.add_subplot(gs[1, :])
    else:
        gs  = GridSpec(1, 1, figure=fig, top=0.95, bottom=0.08, left=0.06, right=0.99)
        ax  = fig.add_subplot(gs[0, 0])

    ax.set_facecolor(CARD)
    for spine in ax.spines.values():
        spine.set_edgecolor(BORDER)
        spine.set_linewidth(0.8)
    ax.tick_params(colors=MUTED, labelsize=11)
    ax.set_title("Total Usage in Contract Period", color=TEXT, fontsize=13, pad=6)
    ax.set_ylabel("Billable Metric", color=MUTED, fontsize=11)
    ax.grid(axis="both", color=BORDER, linewidth=0.5, alpha=0.6, zorder=0)

    from slides_client import _catmull_rom_smooth

    all_series_vals = [v for s in series for v in s.get("values", []) if v]
    global_max = max(all_series_vals) if all_series_vals else 1

    for j, s in enumerate(series):
        vals = np.array((s.get("values", []) + [0] * len(x_labels))[:len(x_labels)], dtype=float)
        is_dominant  = float(vals.max()) >= 0.05 * global_max
        raw_name     = s.get("name", "")
        display_name = _HEX_SERIES_LABELS.get(raw_name) or _snake_to_title(raw_name)
        color = (
            _HEX_SERIES_COLORS.get(raw_name)
            or _HEX_SERIES_COLORS.get(display_name)
            or _HEX_DEFAULT_PALETTE[j % len(_HEX_DEFAULT_PALETTE)]
        )
        sx, sy = _catmull_rom_smooth(list(x), list(vals))
        sy = np.maximum.accumulate(sy)
        ax.plot(sx, sy, color=color,
                linewidth=2.5 if is_dominant else 1.0,
                alpha=0.9 if is_dominant else 0.35,
                zorder=2 if is_dominant else 1,
                label=display_name)

    ax.yaxis.set_major_formatter(FuncFormatter(_fmt))
    step  = max(1, len(x_labels) // 12)
    shown = sorted(set(range(0, len(x_labels), step)) | {len(x_labels) - 1})
    shown = [k for k in shown if k < len(x_labels)]
    ax.set_xticks(shown)
    ax.set_xticklabels([x_labels[k] for k in shown],
                       rotation=30, ha="right", color=MUTED, fontsize=11)
    ax.set_xlim(-0.5, len(x_labels) - 0.5)

    ax.legend(fontsize=10, labelcolor=TEXT, facecolor=CARD,
              edgecolor=BORDER, loc="upper left")

    buf = _BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generate_maturity_bar_from_bq(maturity_data: list[dict], customer_name: str) -> bytes | None:
    """Render the horizontal maturity bar chart (Option 1 slide) from BigQuery row dicts.

    Matches the Hex notebook bar chart style: one horizontal bar per dimension,
    bar length = stage value (1–4), coloured by stage level.
    Returns None if maturity_data is empty or render fails.
    """
    if not maturity_data:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from io import BytesIO

    rows = sorted(
        [r for r in maturity_data if r.get("stage") is not None],
        key=lambda r: (r["stage"], r["dimension"]),
    )
    if not rows:
        return None

    dim_names = [r["dimension"] for r in rows]
    stages = [float(r["stage"]) for r in rows]
    stage_labels = [r.get("stage_label", "") for r in rows]

    # Stage colours matching the Hex notebook palette
    _stage_colors = {
        1: "#4C78A8",   # Exploring — steel blue
        2: "#E45756",   # Building  — coral red
        3: "#F58518",   # Operating — amber
        4: "#54A24B",   # Scaling   — green
    }
    colors = [_stage_colors.get(int(s), "#888888") for s in stages]

    fig_h = max(3.5, len(rows) * 1.1)
    fig, ax = plt.subplots(figsize=(9, fig_h))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    y_pos = list(range(len(rows)))
    bars = ax.barh(y_pos, stages, color=colors, height=0.55, edgecolor="none")

    for bar, label in zip(bars, stage_labels):
        ax.text(
            bar.get_width() + 0.06,
            bar.get_y() + bar.get_height() / 2,
            label, va="center", ha="left", fontsize=9, color="#333333",
        )

    ax.set_yticks(y_pos)
    ax.set_yticklabels(dim_names, fontsize=10)
    ax.set_xlim(0, 5.2)
    ax.set_xticks([1, 2, 3, 4])
    ax.set_xticklabels(["Exploring\n(1)", "Building\n(2)", "Operating\n(3)", "Scaling\n(4)"], fontsize=8)
    ax.set_title(f"Agent Engineering Maturity — {customer_name}", fontsize=12, pad=10)
    ax.grid(axis="x", color="#eeeeee", linewidth=0.8, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color("#cccccc")
    ax.spines["left"].set_color("#cccccc")

    plt.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    try:
        return buf.read()
    except Exception as exc:
        _log.warning("generate_maturity_bar_from_bq: render failed: %s", exc)
        return None


def generate_maturity_radar_from_bq(maturity_data: list[dict], customer_name: str) -> bytes | None:
    """Render the maturity radar directly from BigQuery row dicts (no Vision needed).

    maturity_data: list of dicts with 'dimension' and 'stage' keys, as returned by
    bigquery_client.fetch_maturity_data().  Rows with null stages are already filtered
    out by the BigQuery query (WHERE stage_info.stage IS NOT NULL).

    Returns None if maturity_data is empty or render fails.
    """
    if not maturity_data:
        return None

    dimensions = {r["dimension"]: float(r["stage"]) for r in maturity_data if r.get("stage") is not None}
    if len(dimensions) < 3:
        _log.warning("generate_maturity_radar_from_bq: too few dimensions (%d)", len(dimensions))
        return None

    try:
        radar_bytes = _render_radar(dimensions, customer_name)
        _log.info(
            "generate_maturity_radar_from_bq: rendered radar for %r (%d dims, %d bytes)",
            customer_name, len(dimensions), len(radar_bytes),
        )
        return radar_bytes
    except Exception as exc:
        _log.warning("generate_maturity_radar_from_bq: render failed: %s", exc)
        return None


def build_sign_ups_table_from_bq(rows: list[dict]) -> bytes:
    """Render a dark-themed sign-ups by course table from BigQuery row dicts.

    rows must contain dicts with 'course_name' and 'sign_ups' keys.
    Returns empty bytes if rows is empty.
    """
    if not rows:
        return b""

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from io import BytesIO

    total_courses = len(rows)
    rows = rows[:5]  # top 5 courses only
    course_col_label = "Top 5 Courses" if total_courses > 5 else "Course"

    BG         = "#0c0d1a"
    CARD       = "#161729"
    HEADER_BG  = "#1e2235"
    TEXT       = "#e2e8f0"
    MUTED      = "#94a3b8"
    BORDER     = "#2d3148"

    course_names = [r.get("course_name") or "Unknown" for r in rows]
    sign_ups     = [int(r.get("sign_ups") or 0) for r in rows]

    n = len(rows)
    fig_h = max(3.0, n * 0.85 + 1.5)
    fig, ax = plt.subplots(figsize=(14, fig_h))
    fig.patch.set_facecolor("none")
    ax.set_facecolor("none")
    ax.axis("off")

    table = ax.table(
        cellText=[[c, s] for c, s in zip(course_names, sign_ups)],
        colLabels=[course_col_label, "Sign-ups"],
        loc="center",
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(14)
    table.auto_set_column_width([0, 1])

    for (row, col), cell in table.get_celld().items():
        is_header = row == 0
        cell.set_facecolor(HEADER_BG if is_header else CARD)
        cell.set_edgecolor(BORDER)
        cell.set_linewidth(0.8)
        cell.set_text_props(
            color=MUTED if is_header else TEXT,
            fontweight="bold" if is_header else "normal",
        )
        if col == 1:
            cell.set_text_props(ha="center")
        cell.set_height(0.14 if is_header else 0.12)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
