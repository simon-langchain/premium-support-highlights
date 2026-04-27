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
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` — Google OAuth app credentials (Web application type). Add `{DASHBOARD_URL}/auth/google/callback` (and `http://localhost:3000/auth/google/callback` for local dev) to Authorised redirect URIs in Google Cloud Console. The redirect URI is derived automatically from `DASHBOARD_URL`.

Optional variables:
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` — For emailing reports (Postmark recommended)
- `SMTP_FROM` — From address (defaults to `SMTP_USER`)
- `REPORT_BANNER_URL` — Banner image URL for emailed/PDF reports
- `REPORT_LOGO_URL` — Logo PNG URL for email reports (improves Outlook compatibility)
- `ALLOWED_ORIGINS` — Comma-separated allowed CORS origins (default: `http://localhost:3000`)
- `LOCAL_TEST_MODE` — Set to `true` to bypass login for local development. Automatically disabled when `ALLOWED_ORIGINS` is set.
- `SLACK_BOT_TOKEN` — Slack bot token (`xoxb-...`) with `chat:write`, `channels:read`, `groups:read` scopes
- `SLACK_SIGNING_SECRET` — From Slack app Basic Information page; used to verify interactive button callbacks
- `SLACK_OVERRIDE_CHANNEL` — When set, ALL Slack posts go to this channel ID (use during testing to avoid sending to real customers)
- `LANGGRAPH_API_URL` — Set to `http://localhost:8000` for local dev so the schedule CRUD endpoints can reach the LangGraph cron API. Leave unset in LSD — the SDK uses ASGI in-process transport automatically.
- `DASHBOARD_URL` — Frontend URL linked from the "View Full Report" button in Slack messages

## Architecture

### Authentication

Google OAuth login flow (restricted to `@langchain.dev` Google Workspace accounts):
1. User clicks "Sign in with Google" on `/login`
2. Frontend calls `GET /api/auth/google/start` → backend generates CSRF state token, returns Google OAuth URL with `hd=langchain.dev`
3. User is redirected to Google; non-LangChain accounts are blocked at the Google account picker level
4. Google redirects to `/auth/google/callback?code=...&state=...`
5. Callback page POSTs `{code, state}` to `POST /api/auth/google/callback`
6. Backend verifies CSRF state, exchanges code for ID token, validates `hd=langchain.dev` and `@langchain.dev` email domain, checks active Pylon membership
7. Session token created (in-memory, 8-hour TTL), set as `HttpOnly; Secure; SameSite=Lax` cookie (`psh_session`)
8. Next.js middleware redirects unauthenticated requests to `/login`; `/auth/google/callback` and `/api/auth/*` are bypassed

Sessions are in-memory — restarting the backend invalidates all sessions. State tokens expire in 10 minutes and are single-use.

### Backend (`backend/`)

**`main.py`** — FastAPI app. All routes except `/api/auth/*` require authentication via `Depends(require_auth)`.

Auth routes (unauthenticated):
- `GET /api/auth/google/start` — returns `{url}` Google OAuth URL with CSRF state token embedded
- `POST /api/auth/google/callback` — body `{code, state}`, verifies state, exchanges code, validates domain + Pylon membership, sets `psh_session` cookie
- `POST /api/auth/logout` — revokes session, clears cookie

Protected routes:
- `GET /api/tiers` — sorted list of available support tiers (derived from current customers cache)
- `GET /api/accounts?tier=Premium` — sorted list of accounts for the given tier (defaults to Premium)
- `GET /api/accounts/{id}/data?account_name=...&period=` — metrics + open issues, 5-minute in-memory TTL cache
- `GET /api/accounts/{id}/cached-ticket-summaries` — per-ticket AI summaries from disk cache, keyed by ticket number
- `POST /api/accounts/{id}/summary` — body `{account_name, model, period, force}`, streams SSE keepalive pings while Claude generates, sends final `result` event
- `GET /api/accounts/{id}/report?account_name=...&period=&sort_by=&sort_order=&sections=...` — self-contained HTML report for PDF; `sections` is a repeated query param (e.g. `&sections=key_metrics&sections=open_issues`), omit for all sections
- `POST /api/accounts/{id}/email-report` — body `{email, account_name, period, sort_by, sort_order, sections?}`, generates and emails report via SMTP; `sections` is an optional list of section IDs to include (omit for all)
- `GET /api/accounts/{id}/slack-channel` — returns `{channel_id, channel_name, override, available_channels}` for the Slack channel picker in the UI
- `POST /api/accounts/{id}/slack-report` — body `{account_name, period, channel_id?, sections?}`, posts Block Kit metrics to Slack; `channel_id` overrides the account default; `sections` filters which blocks are included; redirected to `SLACK_OVERRIDE_CHANNEL` env var when set
- `POST /api/slack/actions` — Slack interactive callback endpoint; handles `psh_post_summary`, `psh_post_issues`, `psh_post_issues_more` button actions; verifies HMAC-SHA256 signature
- `GET /api/schedules?account_id=` — list all (or account-filtered) LangGraph Platform cron jobs for the `report_dispatcher` graph; looks up the `report_dispatcher` assistant UUID via `client.assistants.search` before querying crons (UUID required by SDK)
- `POST /api/schedules` — body `{account_id, account_name, label, destination_type, channel_id?, email_addresses?, sections?, period, frequency, weekday, nth, month_in_quarter, hour_local, timezone}`; all fields Pydantic-validated (`destination_type`/`frequency` are `Literal` types, ranges enforced on `weekday`/`nth`/`hour_local`/`month_in_quarter`, `timezone` validated via `zoneinfo.ZoneInfo`, `sections` filtered to `ALL_SECTIONS` allowlist, `email_addresses` checked for `@`); timezone passed natively to `crons.create(timezone=)` so LangGraph Platform handles DST and sub-hour offsets; `created_by` is the authenticated user's email, stored in the cron payload
- `DELETE /api/schedules/{cron_id}` — delete a scheduled report by LangGraph cron ID
- `POST /api/accounts/{id}/qbr-slides` — streams SSE progress events while generating a QBR Google Slides deck; fetches the last 4 months of roadmap items from Google Drive, deduplicates by title (latest month wins), uses Claude to select the 3 most relevant items matching the account's open feature requests, inserts them into a template slide deck, saves the result to disk cache, and sends a final `result` event with `{url, month, month_label}`
- `GET /api/accounts/{id}/qbr-slides/history` — returns last 6 months as `[{month, month_label, is_current, slide}]`; `slide` is `{url, pres_id, created_at, month_label}` or null; newest first

**`auth.py`** — In-memory OTP and session management. `generate_otp`, `verify_otp`, `create_session`, `validate_session`, `revoke_session`, `is_rate_limited`.

**`pylon_client.py`** — Pylon REST API client. Shared `httpx.Client`, `_get`/`_post`/`_patch` helpers with 429 retry, in-memory TTL cache. Key functions:
- `get_current_customers(force_refresh)` — POST /accounts/search filtered to Relationship_Status = "Current Customer"; disk-cached for 1 hour; single source of truth for all tier/account derivation
- `get_available_tiers()` — sorted list of unique Support_Tier values across all current customers (derived from cache, no extra API calls)
- `get_accounts_by_tier(tier)` — current customers filtered to the given tier (derived from cache, no extra API calls)
- `get_premium_accounts()` — backward-compatible wrapper for `get_accounts_by_tier("Premium")`
- `get_team_members()` — GET /users, cached 2 minutes, used for auth eligibility check
- `get_account(account_id)` — looks up a single account from the current customers disk cache (no network request)
- `get_slack_channel_id(account)` — extracts primary Slack channel ID from account's `channels` array
- `search_issues_for_account(account_id, ...)` — POST /issues/search with account filter, auto-paginates
- `make_date_range(period)` — returns (created_after, created_before) for the given period string
- **Custom fields**: Pylon returns `custom_fields` as a dict keyed by slug, e.g. `{"account.salesforce.Support_Tier__c": {"value": "Premium"}}`. Use `_get_custom_field(account, slug)` for safe extraction.

**`slack_client.py`** — Slack API client. `post_message` posts Block Kit + legacy attachment messages via `chat.postMessage`. `get_channels` lists internal channels the bot can post to (filters Slack Connect and external-prefixed channels). `get_channel_name` resolves a channel ID to its name via `conversations.info`, with in-process caching. `get_bot_name` calls `auth.test` to return the bot's workspace username (used in "not in channel" error messages).

`ALL_SECTIONS = frozenset({"key_metrics", "ticket_trend", "breakdowns", "account_summary", "open_issues"})` is the canonical set of section IDs used by both Slack and email. `_build_metrics_blocks` accepts `sections: set[str] | None` and conditionally includes each block group; the "More Details" heading becomes "Details" when no metric/trend/breakdown blocks precede the action buttons.

**`metrics.py`** — Pure-Python metric computation. Uses calendar arithmetic (not `timedelta(days=30)`) for monthly bucketing to correctly handle February and short months.
- `compute_period_metrics(issues, period)` — bucketed ticket counts
- `compute_avg_response_time(issues)` — hours to first response for closed tickets
- `get_priority_breakdown(issues)` / `get_state_breakdown(issues)` / `get_disposition_breakdown(issues)`

**`summary_agent.py`** — AI summary generation via `deepagents`. `generate_account_summary(...)` formats metrics as compact text and runs the agent. `make_summarise_tickets_tool(open_issues, force, account_name)` returns a tool that generates and caches per-ticket summaries in parallel, passing the account name through to the prompt.

**`report.py`** — Self-contained HTML report generator. `generate_report_html(..., is_email=False, banner_url=None, logo_url=None, sections=None)`:
- `is_email=True`: email-safe layout (table-based, no SVG/CSS grid/flex), banner + footer, metric cards 2x2, breakdowns stacked
- `is_email=False`: browser/PDF layout with full CSS, banner inside max-width container
- `sections`: optional `set[str]` of section IDs to include — `key_metrics`, `ticket_trend`, `breakdowns`, `account_summary`, `open_issues`; `None` means all sections

**`ticket_summarizer.py`** — Per-ticket AI output via Claude Haiku. `summarize_ticket(title, body_html, messages, state, account_name)` returns structured `"Summary: ...\nNext steps: ..."` text. State labels are context-aware: `waiting_on_customer` tells the model LangChain has responded and is waiting; `waiting_on_you` means LangChain needs to act; `on_hold` means an internal LangChain team (Engineering/Product) is holding it. `parse_ticket_output(text)` parses the two-line output; old plain-text cache entries fall back to displaying as `next_steps`.

**`cache.py`** — JSON file cache (`.cache/analysis_cache.json`). Per-ticket summaries keyed by `sha256(issue_id:latest_message_time)`; account summaries keyed by `as:{account_id}:{period}`; QBR slide records keyed by `qbr:{account_id}:{YYYY-MM}` (no expiry — permanent).

**`roadmap_client.py`** — Google Slides roadmap parser. `extract_roadmap_items(slides_service, pres_id, month)` parses a roadmap presentation into a list of `{title, description, month, image_url}` items. Handles two multi-feature slide patterns: Pattern A (next text fragment starts with `:`), Pattern B (bullet ends with `:`). Single-feature title extraction joins fragments with `": "`, strips trailing colons, limits to 45 chars. `select_roadmap_items(items, open_issues, account_name, today)` uses Claude Haiku to pick the 3 most relevant items; tags items UPCOMING/DELIVERED relative to `today` and prioritises delivered items that match open feature requests.

**`slides_client.py`** — Google Slides deck builder. `create_slide_deck(account_name, slide14, slide15, quarter_label, month_label)` copies the template presentation into the customer's Drive folder and applies text replacements. `add_roadmap_items(pres_id, items)` inserts up to 3 roadmap items into the roadmap slide using three isolated `batchUpdate` calls (text, image replace with `CENTER_CROP`, white border outline). Uses `image_url` (embedded product screenshot) as the image source, falling back to `getThumbnail` for slides without embedded images.

**`audit.py`** — JSONL audit log (`.cache/audit.jsonl`).

**`report_dispatcher.py`** — LangGraph graph registered as `"report_dispatcher"` in `langgraph.json`. Single node `send_report` that:
1. Evaluates `run_condition` (e.g. `{"type": "nth_weekday_of_month", "n": 1, "weekday": 0}` = first Monday of month) — skips if today doesn't match
2. Fetches fresh Pylon data for the account
3. Sends a Slack Block Kit message or HTML email, respecting the `sections` filter

`_should_run(condition)` supports three condition types; `n=-1` means "last" in all cases:
- `nth_weekday_of_month` — monthly; fires on the nth occurrence of the given weekday in the current month
- `nth_weekday_of_month_in_quarter` — quarterly; fires on the nth weekday of a specific month within the quarter (`month_in_quarter` 1–3); current primary quarterly type
- `nth_weekday_of_quarter` — legacy quarterly type (counts weekdays through the entire quarter); kept for backwards compatibility with older schedules

Cron expressions fire every week on the given weekday (e.g. `0 9 * * 1` = Mondays at 09:00 in the cron's timezone); the run_condition filters to the correct occurrence. Imports `_build_metrics_blocks`, `_build_payload`, and other helpers directly from `main.py` — safe because `main.py` never imports from `report_dispatcher.py`.

**Scheduling architecture**: cron jobs are stored in LangGraph Platform's Postgres database (not in the app), so they persist across redeployments. `_lg_client()` defaults to `http://localhost:8000` (override with `LANGGRAPH_API_URL`); `url=None` in-process ASGI transport only works from inside a graph node, not from a FastAPI HTTP handler. Timezone is passed natively to `crons.create(timezone=)` — the platform adjusts for DST and sub-hour offsets (e.g. IST +5:30) automatically, so the cron expression always uses the local hour. Weekday conversion: Python weekday 0=Mon → cron weekday 1=Mon (formula: `cron_wd = (python_wd + 1) % 7`). Reverse: `python_wd = (cron_wd - 1) % 7`.

### Frontend (`frontend/`)

Next.js 15 app with Tailwind CSS. All `/api/*` requests are proxied to the backend via a catch-all route handler.

**`src/middleware.ts`** — Redirects to `/login?return=<path>` if `psh_session` cookie is absent. Skips `/login`, `/auth/google/callback`, `/api/auth/*`, `/api/slack/*`, `/_next/*`.

**`src/app/login/page.tsx`** — Google OAuth button (primary). Clicking "or sign in with email" hides the Google button and reveals the OTP flow (email input → 6-digit code). Each OTP step has a "Back to Google sign-in" link. Handles `sent` / `not_authorized` / `rate_limited` states inline.

**`src/app/page.tsx`** — Main dashboard. Loads available tiers on mount; re-fetches accounts when the selected tier changes. Fetches account data on account selection. Manages filtering/sorting client-side. Polls cached ticket summaries every 2s while the summary agent runs. Reads `?account=<slug>` on mount for deep links; updates the URL on every account switch so all views are shareable. Account names are slugified (`toSlug`: lowercase, apostrophes/brackets stripped, non-alphanumeric runs → hyphens). State labels (e.g. "Waiting on Customer") use the actual account name via `getStateLabels(accountName)`. Shows a centered empty state when no account is selected.

The **QBR Slides** popover (header button) lazily fetches history on first open and shows the last 6 months filtered to months that have slides plus the current month. Each row shows month name, generation date, an Open link (if slides exist), and a Generate/refresh-icon button (current month only). Generate runs immediately; the refresh icon shows a custom confirm modal before overwriting. Progress steps are shown inline while generating; the month list is hidden during generation. Switching accounts resets all QBR state. `runQbrGeneration(account)` is a shared `useCallback` used by both the generate button and the confirm modal.

**`src/app/api/[...path]/route.ts`** — Catch-all proxy. Forwards all headers (including `cookie` and `authorization`) to the backend. Injects `x-api-key` for LSD authentication server-side.

**`src/app/api/accounts/[accountId]/summary/route.ts`** — Custom route handler for the summary SSE stream. Buffers the stream and returns plain JSON once the `result` event arrives. Explicitly forwards `cookie` and `authorization` headers (the catch-all does this automatically; this handler has its own header dict).

**`src/components/Sidebar.tsx`** — Fixed left sidebar with LangChain logo, support tier selector, account selector, period selector, refresh button, model selector, and Settings menu (light/dark toggle + sign out). Collapsible.

**`src/components/ScheduleModal.tsx`** — Two-view modal for managing scheduled reports, opened via the "Schedule" button in the page header.
- **List view**: active schedules per account, each card showing label + `[Slack/Email]` pill, compact schedule description (e.g. "4th Wed of every month at 08:00 PDT · 6 months"), destination, and next run + created-by metadata. Edit (pencil) and delete (trash) actions per card. "Add Schedule" button pinned at the bottom.
- **Form view**: create/edit form with back-arrow navigation. Fields: label (optional), destination toggle (Slack channel picker or email chip input), report period, frequency (Weekly/Monthly/Quarterly), occurrence in month (1st–Last; monthly/quarterly), month of quarter (1st/2nd/3rd; quarterly only), day of week, time + timezone picker.
- **Timezone picker**: 51 IANA timezones ordered west-to-east; UTC at its natural position. Labels show DST-aware abbreviation + offset (e.g. "Los Angeles (PDT · GMT-7)") computed via `Intl.DateTimeFormat` with a `getTzAbbr` lookup table for international zones. `tzShort(tz)` returns just the abbreviation for compact schedule descriptions.
- **Email chip input** (`EmailTagInput`): emails displayed as removable chips; Enter/comma/space (when input contains `@`) commits a chip; paste of comma- or whitespace-separated lists splits automatically; Backspace removes the last chip.
- **Quarterly model**: `month_in_quarter` (1–3) selects which month of the quarter, combined with the standard nth-weekday picker. Produces `nth_weekday_of_month_in_quarter` run conditions.
- On submit success: `setView("list")` returns to list view; edit is implemented as delete + recreate.

**`src/components/SlackIcon.tsx`** — Shared Slack brand mark SVG component (official paths, 270×270 viewBox cropped to 73.6 73.6 122.8 122.8). Used by `ScheduleModal` and `ShareButton`.

**`src/components/ShareButton.tsx`** — Combined share button for Slack and email. Opens a popover with a Slack/Email mode tab, section checkboxes (Key Metrics, Ticket Trend, Breakdowns, Account Summary, Open Issues — all checked by default), a channel picker (Slack, when multiple channels available) or email input, and a send button. Success status clears after 10 seconds; the popover stays open until dismissed by clicking outside.

**`src/components/DownloadMenu.tsx`** — Download popover with PDF/CSV format tabs and section checkboxes (same sections as ShareButton). Account Summary is greyed out and disabled for CSV (not available in that format), with a "Not available in CSV" tooltip on hover. Download button is disabled if no sections are selected.

**`src/lib/api.ts`** — TypeScript fetch functions. All functions check for 401 and redirect to `/login` via `window.location.href`. The `Schedule` interface includes `month_in_quarter`, `hour_local`, `timezone`, and `created_by` fields. `createSchedule` handles Pydantic validation errors (which return `detail` as an array) by joining the `msg` fields into a readable string. `QbrSlide`, `QbrHistoryEntry` interfaces and `fetchQbrHistory`, `streamQbrSlides` functions support the QBR slides feature; `streamQbrSlides` reads an SSE stream of `progress`/`result`/`error` events.

**`src/lib/downloads.ts`** — `downloadPdf` opens the `/report` endpoint in a new tab; accepts optional `sections?: string[]` appended as repeated query params. `downloadCsv` builds and downloads a CSV blob client-side; accepts optional `sections?: string[]` and conditionally includes each section (KEY METRICS, TICKET TREND, PRIORITY/STATE/DISPOSITION BREAKDOWNS, OPEN TICKETS — Account Summary has no CSV representation and is ignored). `slackReport` and `emailReport` both accept an optional `sections?: string[]` forwarded to the backend.

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
│   ├── main.py                 # FastAPI app + all routes
│   ├── report_dispatcher.py    # LangGraph graph for scheduled Slack/email reports
│   ├── auth.py                 # OTP + session management
│   ├── pylon_client.py         # Pylon REST API client
│   ├── metrics.py              # Metric computation (pure Python)
│   ├── summary_agent.py        # AI summary + per-ticket tool via deepagents
│   ├── report.py               # HTML report generator (browser/PDF + email variants)
│   ├── roadmap_client.py       # Google Slides roadmap parser + AI item selector
│   ├── slides_client.py        # Google Slides QBR deck builder
│   ├── cache.py                # JSON file cache (ticket summaries, account summaries, QBR slides)
│   ├── audit.py                # JSONL audit log
│   └── pyproject.toml          # Python dependencies (uv)
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
