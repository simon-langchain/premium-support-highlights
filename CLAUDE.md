# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Premium Support Highlights — a dashboard that surfaces monthly support metrics and open-issue summaries for premium customer accounts. Data comes from the Pylon REST API; AI summaries are generated via Claude through `deepagents`.

Built with **Next.js** (frontend) and **FastAPI** (backend).

## Commands

```bash
# Run both backend and frontend (recommended)
./start.sh

# Backend only
cd backend && uv run uvicorn main:app --reload --port 8000

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

**`main.py`** — FastAPI app. Wraps the Python modules below and exposes three routes:
- `GET /api/accounts` — returns sorted list of premium accounts
- `GET /api/accounts/{id}/data?account_name=...` — fetches open issues and 6-month issues, runs all metrics functions, returns full payload. Has a 5-minute in-memory TTL cache.
- `POST /api/accounts/{id}/summary` — body `{account_name, model}`, calls `generate_account_summary`, returns `{summary}`.

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

**`summary_agent.py`** — AI summary generation. `generate_account_summary(account_name, open_tickets, monthly_metrics)` formats ticket data as compact text and invokes a `deepagents` agent asynchronously.

**`cache.py`** — JSON file cache (`.cache/analysis_cache.json`) keyed by `sha256(issue_id:latest_message_time)`.

**`audit.py`** — JSONL audit log (`.cache/audit.jsonl`) recording all actions (account loads, summary generation).

### Frontend (`frontend/`)

Next.js 15 app with Tailwind CSS. All `/api/*` requests are proxied to `localhost:8000` via `next.config.ts` rewrites.

**`src/app/page.tsx`** — Main page. Fetches accounts on mount, fetches account data on selection. Manages filtering/sorting of open issues client-side.

**`src/components/Sidebar.tsx`** — Fixed left sidebar with LangChain logo, account selector, refresh button, model selector.

**`src/components/MetricCard.tsx`** — Stat card with label, value, and optional delta.

**`src/components/TrendChart.tsx`** — Recharts `AreaChart` for 6-month ticket trend.

**`src/components/TicketCard.tsx`** — Individual ticket row with state/priority/age badges and Pylon link.

**`src/components/SummaryPanel.tsx`** — AI summary section with generate button and rendered output.

**`src/lib/api.ts`** — TypeScript fetch functions and types for all backend endpoints.

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
│   ├── main.py             # FastAPI app
│   ├── pylon_client.py     # Pylon REST API client
│   ├── metrics.py          # Metric computation (pure Python)
│   ├── summary_agent.py    # AI summary via deepagents
│   ├── cache.py            # JSON file cache
│   ├── audit.py            # JSONL audit log
│   └── pyproject.toml      # Python dependencies (uv)
├── frontend/
│   ├── src/
│   │   ├── app/            # Next.js app router (layout, page, globals.css)
│   │   ├── components/     # UI components
│   │   └── lib/api.ts      # API client + TypeScript types
│   ├── package.json
│   └── next.config.ts      # Proxies /api/* to localhost:8000
├── start.sh                # Launches backend + frontend
├── .env                    # API keys (gitignored)
├── .env.example            # Template env file
└── .cache/                 # Runtime data (gitignored)
    ├── analysis_cache.json
    └── audit.jsonl
```
