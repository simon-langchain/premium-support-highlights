# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Premium Support Highlights — a dashboard that surfaces monthly support metrics and AI-generated summaries for premium customer accounts. Data comes from the Pylon REST API; AI summaries are generated via Claude through `deepagents`. Access is restricted to active Pylon team members with `@langchain.dev` emails via OTP-based login.

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

Copy `.env.example` to `.env`. Required variables:
- `PYLON_API_TOKEN` — Pylon REST API token
- `ANTHROPIC_API_KEY` — For Claude summaries
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` — For OTP login emails and report emails (Postmark recommended)

Optional variables:
- `SMTP_FROM` — From address (defaults to `SMTP_USER`)
- `REPORT_BANNER_URL` — Banner image URL for emailed/PDF reports
- `REPORT_LOGO_URL` — Logo PNG URL for email reports (improves Outlook compatibility)
- `ALLOWED_ORIGINS` — Comma-separated allowed CORS origins (default: `http://localhost:3000`)
- `LOCAL_TEST_MODE` — Set to `true` to bypass login for local development. Automatically disabled when `ALLOWED_ORIGINS` is set.
- `SLACK_BOT_TOKEN` — Slack bot token (`xoxb-...`) with `chat:write`, `channels:read`, `groups:read` scopes
- `SLACK_SIGNING_SECRET` — From Slack app Basic Information page; used to verify interactive button callbacks
- `SLACK_OVERRIDE_CHANNEL` — When set, ALL Slack posts go to this channel ID (use during testing to avoid sending to real customers)
- `DASHBOARD_URL` — Frontend URL linked from the "View Full Report" button in Slack messages

## Architecture

### Authentication

OTP-based login flow:
1. User enters `@langchain.dev` email on `/login`
2. Backend checks they are an active member in Pylon (`GET /users`)
3. A 6-digit code (cryptographically random via `secrets.randbelow`) is emailed, expires in 15 minutes, single-use
4. Verified code creates a session token stored in-memory with 8-hour TTL
5. Token is set as an `HttpOnly; Secure; SameSite=Lax` cookie (`psh_session`)
6. Next.js middleware redirects unauthenticated requests to `/login`
7. All protected backend routes use `Depends(require_auth)` to validate the session cookie

Sessions are in-memory — restarting the backend invalidates all sessions. Rate limiting: 3 OTP requests per email per 15-minute window.

### Backend (`backend/`)

**`main.py`** — FastAPI app. All routes except `/api/auth/*` require authentication via `Depends(require_auth)`.

Auth routes (unauthenticated):
- `POST /api/auth/request` — validates email domain + Pylon membership, sends OTP
- `POST /api/auth/verify` — validates OTP, sets `psh_session` cookie
- `POST /api/auth/logout` — revokes session, clears cookie

Protected routes:
- `GET /api/accounts` — sorted list of premium accounts
- `GET /api/accounts/{id}/data?account_name=...&period=` — metrics + open issues, 5-minute in-memory TTL cache
- `GET /api/accounts/{id}/cached-ticket-summaries` — per-ticket AI summaries from disk cache, keyed by ticket number
- `POST /api/accounts/{id}/summary` — body `{account_name, model, period, force}`, streams SSE keepalive pings while Claude generates, sends final `result` event
- `GET /api/accounts/{id}/report?account_name=...&period=&sort_by=&sort_order=` — self-contained HTML report for PDF or email
- `POST /api/accounts/{id}/email-report` — body `{email, account_name, period, sort_by, sort_order}`, generates and emails report via SMTP
- `GET /api/accounts/{id}/slack-channel` — returns `{channel_id, channel_name, override, available_channels}` for the Slack channel picker in the UI
- `POST /api/accounts/{id}/slack-report` — body `{account_name, period, channel_id?}`, posts Block Kit metrics to Slack; `channel_id` overrides the account default; redirected to `SLACK_OVERRIDE_CHANNEL` env var when set
- `POST /api/slack/actions` — Slack interactive callback endpoint; handles `psh_post_summary`, `psh_post_issues`, `psh_post_issues_more` button actions; verifies HMAC-SHA256 signature

**`auth.py`** — In-memory OTP and session management. `generate_otp`, `verify_otp`, `create_session`, `validate_session`, `revoke_session`, `is_rate_limited`.

**`pylon_client.py`** — Pylon REST API client. Shared `httpx.Client`, `_get`/`_post`/`_patch` helpers with 429 retry, in-memory TTL cache. Key functions:
- `get_premium_accounts()` — GET /accounts
- `get_team_members()` — GET /users, cached 2 minutes, used for auth eligibility check
- `get_account(account_id)` — looks up a single account from the premium accounts disk cache (no network request)
- `get_slack_channel_id(account)` — extracts primary Slack channel ID from account's `channels` array
- `search_issues_for_account(account_id, ...)` — POST /issues/search with account filter, auto-paginates
- `make_date_range(period)` — returns (created_after, created_before) for the given period string

**`slack_client.py`** — Slack API client. `post_message` posts Block Kit + legacy attachment messages via `chat.postMessage`. `get_channels` lists internal channels the bot can post to (filters Slack Connect and external-prefixed channels). `get_channel_name` resolves a channel ID to its name via `conversations.info`, with in-process caching.

**`metrics.py`** — Pure-Python metric computation. Uses calendar arithmetic (not `timedelta(days=30)`) for monthly bucketing to correctly handle February and short months.
- `compute_period_metrics(issues, period)` — bucketed ticket counts
- `compute_avg_response_time(issues)` — hours to first response for closed tickets
- `get_priority_breakdown(issues)` / `get_state_breakdown(issues)` / `get_disposition_breakdown(issues)`

**`summary_agent.py`** — AI summary generation via `deepagents`. `generate_account_summary(...)` formats metrics as compact text and runs the agent. `make_summarise_tickets_tool(open_issues, force)` returns a tool that generates and caches per-ticket summaries in parallel.

**`report.py`** — Self-contained HTML report generator. `generate_report_html(..., is_email=False, banner_url=None, logo_url=None)`:
- `is_email=True`: email-safe layout (table-based, no SVG/CSS grid/flex), banner + footer, metric cards 2x2, breakdowns stacked
- `is_email=False`: browser/PDF layout with full CSS, banner inside max-width container

**`cache.py`** — JSON file cache (`.cache/analysis_cache.json`). Per-ticket summaries keyed by `sha256(issue_id:latest_message_time)`; account summaries keyed by `as:{account_id}:{period}`.

**`audit.py`** — JSONL audit log (`.cache/audit.jsonl`).

### Frontend (`frontend/`)

Next.js 15 app with Tailwind CSS. All `/api/*` requests are proxied to the backend via a catch-all route handler.

**`src/middleware.ts`** — Redirects to `/login` if `psh_session` cookie is absent. Skips `/login`, `/api/auth/*`, `/_next/*`.

**`src/app/login/page.tsx`** — Two-step login form: email input → 6-digit code input. Handles `sent` / `not_authorized` / `rate_limited` states inline without exposing which emails exist.

**`src/app/page.tsx`** — Main dashboard. Fetches accounts on mount, account data on selection. Manages filtering/sorting client-side. Polls cached ticket summaries every 2s while the summary agent runs.

**`src/app/api/[...path]/route.ts`** — Catch-all proxy. Forwards all headers (including `cookie` and `authorization`) to the backend. Injects `x-api-key` for LSD authentication server-side.

**`src/app/api/accounts/[accountId]/summary/route.ts`** — Custom route handler for the summary SSE stream. Buffers the stream and returns plain JSON once the `result` event arrives. Explicitly forwards `cookie` and `authorization` headers (the catch-all does this automatically; this handler has its own header dict).

**`src/components/Sidebar.tsx`** — Fixed left sidebar with LangChain logo, account selector, period selector, refresh button, model selector, and Settings menu (light/dark toggle + sign out). Collapsible.

**`src/components/EmailButton.tsx`** — Standalone email button that opens a popover with email input and send status.

**`src/components/SlackButton.tsx`** — One-click Slack send button. Shows spinner while sending, success/error states inline. No input needed — channel is resolved server-side from Pylon account data.

**`src/components/DownloadMenu.tsx`** — Dropdown with PDF and CSV export options.

**`src/lib/api.ts`** — TypeScript fetch functions. All functions check for 401 and redirect to `/login` via `window.location.href`.

**`src/lib/downloads.ts`** — `downloadPdf` opens the `/report` endpoint in a new tab. `downloadCsv` builds and downloads a CSV blob client-side. `emailReport` posts to `/email-report`.

## Key Patterns

- **"Open" states**: `new`, `waiting_on_you`, `on_hold`, `waiting_on_customer`
- **Pylon base URL**: `https://api.usepylon.com`
- **Pylon auth**: `Authorization: Bearer {PYLON_API_TOKEN}` header
- **Rate limit handling**: exponential backoff on 429 (`_MAX_RETRIES = 2`, `_RETRY_BACKOFF = 1.0s`)
- **Caching layers**: Pylon client (120s in-memory) → main.py route cache (300s in-memory) → disk cache for AI summaries
- **All persistent data** lives in `.cache/` (gitignored)
- **Design tokens**: LangSmith-inspired dark theme — bg `#09090f`, secondary `#111521`, border `#1b2030`, accent `#006ddd`
- **Email HTML**: fully table-based layout, no SVG/CSS grid/flex — required for Gmail and Outlook compatibility

## File Structure

```
premium-support-highlights/
├── backend/
│   ├── main.py             # FastAPI app + all routes
│   ├── auth.py             # OTP + session management
│   ├── pylon_client.py     # Pylon REST API client
│   ├── metrics.py          # Metric computation (pure Python)
│   ├── summary_agent.py    # AI summary + per-ticket tool via deepagents
│   ├── report.py           # HTML report generator (browser/PDF + email variants)
│   ├── cache.py            # JSON file cache (ticket + account summaries)
│   ├── audit.py            # JSONL audit log
│   └── pyproject.toml      # Python dependencies (uv)
├── frontend/
│   ├── src/
│   │   ├── middleware.ts           # Auth redirect middleware
│   │   ├── app/                    # Next.js app router (layout, page, globals.css)
│   │   ├── app/login/              # Login page (OTP flow)
│   │   ├── app/api/[...path]/      # Catch-all proxy to backend
│   │   ├── app/api/.../summary/    # Custom SSE handler for account summary
│   │   ├── components/             # UI components
│   │   └── lib/                    # api.ts + downloads.ts
│   ├── package.json
│   └── next.config.ts
├── langgraph.json          # LSD deployment config
├── start.sh                # Launches backend + frontend
├── .env                    # API keys (gitignored)
├── .env.example            # Template env file
└── .cache/                 # Runtime data (gitignored)
    ├── analysis_cache.json
    └── audit.jsonl
```
