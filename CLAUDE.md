# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Premium Support Highlights — a dashboard that surfaces monthly support metrics and open-issue summaries for premium customer accounts. Data comes from the Pylon REST API; AI summaries are generated via Claude through `deepagents`.

Built with **Next.js** (frontend) and **FastAPI** (backend).

## Commands

```bash
# Run both backend and frontend (recommended)
./start.sh

# Backend only (from project root, uses backend venv)
uv run --project ./backend langgraph dev --port 8000 --no-browser

# Frontend only
cd frontend && npm run dev

# Test Pylon API connectivity
cd backend && uv run python pylon_client.py
```

## Environment Setup

Requires Python >=3.11 and Node.js 18+. Uses `uv` for Python dependency management.

```bash
cd backend && uv sync
cd frontend && npm install
```

Required `.env` variables (at project root):
- `PYLON_API_TOKEN` — Pylon REST API token
- `ANTHROPIC_API_KEY` — For Claude model used for summaries

## Architecture

### Backend (`backend/`)

**`main.py`** — FastAPI app. Wraps the Python modules below and exposes four routes:
- `GET /api/accounts` — returns sorted list of premium accounts
- `GET /api/accounts/{id}/data?account_name=...&period=` — fetches open issues and period issues, runs all metrics functions, returns full payload. Has a 5-minute in-memory TTL cache.
- `GET /api/accounts/{id}/cached-ticket-summaries` — returns per-ticket AI summaries from the disk cache, keyed by ticket number. Polled by the frontend during summary generation.
- `POST /api/accounts/{id}/summary` — body `{account_name, model, period, force}`, streams SSE keepalive pings while Claude generates, sends final `result` event with `{summary}`. Caches the result for the report endpoint.
- `GET /api/accounts/{id}/report?account_name=...&period=&sort_by=&sort_order=` — returns a self-contained HTML report suitable for printing to PDF or sending as email.

**`pylon_client.py`** — Pylon REST API client. Shared `httpx.Client`, `_get`/`_post`/`_patch` helpers with 429 retry, in-memory TTL cache. Key functions:
- `get_premium_accounts()` — GET /accounts
- `search_issues_for_account(account_id, ...)` — POST /issues/search with account filter, auto-paginates
- `search_issues_all(...)` — POST /issues/search (no account filter), used as fallback for account discovery
- `make_6month_date_range()` — returns (created_after, created_before) for the last 6 months

**`metrics.py`** — Pure-Python metric computation from raw issue lists. No API calls. Functions:
- `compute_monthly_metrics(issues, months=6)` — bucketed ticket counts for the last N months
- `compute_avg_response_time(issues)` — hours from created_at to latest_message_activity_at for closed tickets
- `compute_csat_from_issues(issues)` — extracts CSAT from custom_fields
- `get_priority_breakdown(issues)` / `get_state_breakdown(issues)` — count dicts

**`summary_agent.py`** — AI summary generation. `generate_account_summary(...)` formats all account metrics as compact text and invokes a `deepagents` agent asynchronously. `make_summarise_tickets_tool(open_issues, force)` returns the tool used by the agent to generate and cache per-ticket summaries in parallel.

**`cache.py`** — JSON file cache (`.cache/analysis_cache.json`). Stores per-ticket summaries keyed by `sha256(issue_id:latest_message_time)`, and account-level AI summaries keyed by `as:{account_id}:{period}`.

**`report.py`** — Self-contained HTML report generator. `generate_report_html(account_name, period, payload, ticket_summaries, account_summary, sort_by, sort_order)` produces a single HTML page with inline CSS, an SVG bar chart, breakdown tables, rendered markdown for the AI summary, and a sortable open-ticket list. Suitable for browser print-to-PDF or HTML email.

**`audit.py`** — JSONL audit log (`.cache/audit.jsonl`) recording all actions (account loads, summary generation).

### Frontend (`frontend/`)

Next.js 15 app with Tailwind CSS. All `/api/*` requests are proxied to `localhost:8000` via `next.config.ts` rewrites.

**`src/app/page.tsx`** — Main page. Fetches accounts on mount, fetches account data on selection. Manages filtering/sorting of open issues client-side.

**`src/components/Sidebar.tsx`** — Fixed left sidebar with LangChain logo, account selector, refresh button, model selector, and period selector.

**`src/components/MetricCard.tsx`** — Stat card with label, value, and optional unit.

**`src/components/TrendChart.tsx`** — Recharts `AreaChart` for the selected period's ticket trend.

**`src/components/TicketCard.tsx`** — Individual ticket row with state/priority/age badges and Pylon link.

**`src/components/SummaryPanel.tsx`** — AI summary section with generate/regenerate button and rendered output.

**`src/components/DownloadMenu.tsx`** — Dropdown button with PDF and CSV export options.

**`src/lib/api.ts`** — TypeScript fetch functions and types for all backend endpoints.

**`src/lib/downloads.ts`** — `downloadPdf(accountId, accountName, period, sortBy, sortOrder)` opens the `/report` endpoint in a new tab. `downloadCsv(accountName, period, data, issues, ticketSummaries)` builds and downloads a CSV blob client-side.

## Key Patterns

- **Account discovery**: tries `GET /accounts` first; falls back to extracting unique `account` objects from open issues if the endpoint is empty
- **"Open" states**: `new`, `waiting_on_you`, `on_hold`, `waiting_on_customer` (not `closed`)
- **Pylon base URL**: `https://api.usepylon.com`
- **Auth**: `Authorization: Bearer {PYLON_API_TOKEN}` header
- **Rate limit handling**: exponential backoff on 429 (`_MAX_RETRIES = 2`, `_RETRY_BACKOFF = 1.0s`)
- **Caching**: two layers — in-memory TTL cache in `pylon_client.py` (120s) and in `main.py` (300s)
- **All persistent data** lives in `.cache/` (gitignored)
- **Design tokens**: LangSmith-inspired dark theme — bg `#09090f`, secondary `#111521`, border `#1b2030`, accent `#006ddd`

## File Structure

```
premium-support-highlights/
├── backend/
│   ├── main.py             # FastAPI app + all routes
│   ├── pylon_client.py     # Pylon REST API client
│   ├── metrics.py          # Metric computation (pure Python)
│   ├── summary_agent.py    # AI summary + per-ticket tool via deepagents
│   ├── report.py           # Self-contained HTML report generator
│   ├── cache.py            # JSON file cache (ticket + account summaries)
│   ├── audit.py            # JSONL audit log
│   └── pyproject.toml      # Python dependencies (uv); langgraph-cli in [dependency-groups].dev
├── frontend/
│   ├── src/
│   │   ├── app/            # Next.js app router (layout, page, globals.css)
│   │   ├── app/api/        # Route handlers: catch-all proxy + summary SSE handler
│   │   ├── components/     # UI components (Sidebar, TicketCard, DownloadMenu, ...)
│   │   └── lib/            # api.ts (fetch helpers) + downloads.ts (PDF/CSV export)
│   ├── package.json
│   └── next.config.ts      # Next.js config
├── langgraph.json          # LSD deployment config (graphs, http app, PYTHONPATH)
├── start.sh                # Launches backend (langgraph dev) + frontend
├── .env                    # API keys (gitignored)
├── .env.example            # Template env file
└── .cache/                 # Runtime data (gitignored)
    ├── analysis_cache.json
    └── audit.jsonl
```
