"""Quick sanity-check: fetch every named Hex chart cell for a given Metronome customer
and save the raw PNG images to /tmp/hex_check/.  Run with:

    cd backend
    set -a && source ../.env && set +a
    uv run python check_hex_cells.py <metronome_id>

Then open /tmp/hex_check/ and compare against the Hex dashboard.
"""

import os
import sys
from pathlib import Path

import hex_client as _hex

CELLS: dict[str, str] = {
    # Slide 23 — commit usage line chart (the one in question)
    "slide23_commit_usage":               _hex.COMMIT_USAGE_CELL_ID,
    # Slide 24 — LangSmith usage bar charts
    "slide24_traces_by_month":            _hex.TRACES_BY_MONTH_CELL_ID,
    "slide24_monthly_page_views":         _hex.MONTHLY_PAGE_VIEWS_CELL_ID,
    "slide24_agent_runs_monthly":         _hex.AGENT_RUNS_MONTHLY_CELL_ID,
    "slide24_agent_builder_runs_monthly": _hex.AGENT_BUILDER_RUNS_CELL_ID,
    # Slide 25 — feature usage bar charts
    "slide25_experiments_run":            _hex.EXPERIMENTS_RUN_CELL_ID,
    "slide25_prompt_commits":             _hex.PROMPT_COMMITS_CELL_ID,
    "slide25_prompt_pulls":               _hex.PROMPT_PULLS_CELL_ID,
    "slide25_datasets_created":           _hex.DATASETS_CREATED_CELL_ID,
    "slide25_evaluator_rules":            _hex.EVALUATOR_RULES_CELL_ID,
    # Slide 33/34 — maturity
    "slide33_maturity_bar":               _hex.MATURITY_CHART_CELL_ID,
}

def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python check_hex_cells.py <metronome_id>")
        sys.exit(1)

    metronome_id = sys.argv[1]
    out_dir = Path("/tmp/hex_check")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Triggering Hex run for metronome_id={metronome_id} ...")
    images = _hex.fetch_chart_images(metronome_id, list(CELLS.values()))

    if not images:
        print("ERROR: no images returned — check HEX_API_TOKEN / HEX_PROJECT_ID")
        sys.exit(1)

    print(f"\nFetched {len(images)}/{len(CELLS)} cells:\n")
    for label, cell_id in CELLS.items():
        if cell_id in images:
            path = out_dir / f"{label}.png"
            path.write_bytes(images[cell_id])
            print(f"  OK   {label:45s}  ({len(images[cell_id]):,} bytes)  → {path}")
        else:
            print(f"  MISS {label:45s}  cell_id={cell_id}")

    print(f"\nImages saved to {out_dir}/")
    print("Open them to compare against the Hex dashboard.")


if __name__ == "__main__":
    main()
