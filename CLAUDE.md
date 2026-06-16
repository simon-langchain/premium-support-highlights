# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Premium Support Highlights — a dashboard that surfaces monthly support metrics and AI-generated summaries for premium customer accounts. Data comes from the Pylon REST API; AI summaries are generated via Claude through `deepagents`. Access is restricted to active Pylon team members with `@langchain.dev` emails via Google OAuth (primary) or OTP email (fallback).

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

**Gotcha — inherited `ANTHROPIC_BASE_URL`:** if you've set up the [LangSmith LLM Gateway](https://docs.langchain.com/langsmith/llm-gateway-coding-agents) to route Claude Code CLI through `gateway.smith.langchain.com` (exporting `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` in a shell), any VS Code integrated terminal opened from a window launched after that export inherits it too. Sourcing `.env` in `start.sh` can't undo this — it only sets vars actually present in the file, so the inherited gateway URL silently wins over the real key in `.env` and Anthropic calls 403 (the gateway requires a `gateway:invoke`-scoped key). `start.sh` now strips `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_URL` at startup unless you've explicitly added them to this project's own `.env`.

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
- `GOOGLE_SERVICE_ACCOUNT_JSON` — JSON string of the Google service account used by `slides_client.py` for Drive + Slides API access
- `BIGQUERY_SERVICE_ACCOUNT_JSON` / `GOOGLE_BIGQUERY_SERVICE_ACCOUNT_JSON` — JSON string of the service account for BigQuery access (falls back to ADC if absent)
- `QBR_SHARED_DRIVE_ID` — ID of the Shared Drive where QBR decks and chart cache files are stored; if absent, uses My Drive
- `QBR_TEMPLATE_ID` — Override the production template presentation ID (useful for testing with a test template)

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
- `POST /api/schedules` — body `{account_id, account_name, label, destination_type, channel_id?, email_addresses?, qbr_notify_type?, qbr_notify_channel_id?, qbr_notify_emails?, qbr_template_type?, sections?, period, frequency, weekday, nth, month_in_quarter, hour_local, timezone}`; `destination_type` is `"slack" | "email" | "qbr"` (Literal); QBR schedules ignore `sections`/`period` and require `qbr_notify_type` (`"slack"` or `"email"`), `qbr_notify_channel_id` (Slack), or `qbr_notify_emails` (email, must be `@langchain.dev`); `qbr_template_type` is `"full_deck"` (default) or `"support_highlights"` — selects which Google Slides template to use; all other fields Pydantic-validated; timezone passed natively to `crons.create(timezone=)`; `created_by` is the authenticated user's email, stored in the cron payload
- `DELETE /api/schedules/{cron_id}` — delete a scheduled report by LangGraph cron ID
- `POST /api/accounts/{id}/qbr-slides` — body `{account_name, template_type?}`; streams SSE progress events while generating a QBR Google Slides deck; `template_type` is `"full_deck"` (default) or `"support_highlights"` — selects the template and skips BigQuery chart steps for `support_highlights`; fetches the last 4 months of roadmap items from Google Drive, deduplicates by title (latest month wins), uses Claude to select the 3 most relevant items matching the account's open feature requests, inserts them into a template slide deck, saves the result to disk cache, and sends a final `result` event with `{url, month, month_label}`; also generates BigQuery-powered charts (commit usage with KPI tiles, usage composites, maturity radar, Academy sign-ups table) and inserts them into the deck (full deck only)
- `GET /api/accounts/{id}/qbr-slides/history` — returns last 6 months as `[{month, month_label, is_current, slide}]`; `slide` is `{url, pres_id, created_at, month_label}` or null; newest first

**`auth.py`** — Session management and OTP fallback auth. `generate_otp`, `verify_otp` handle the email OTP flow (used when Google OAuth is unavailable). `create_session`, `validate_session`, `revoke_session`, `is_rate_limited` manage the shared session store used by both auth paths.

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

**`summary_agent.py`** — AI summary generation via `deepagents`. `generate_account_summary(...)` formats metrics as compact text and runs the agent. `make_summarise_tickets_tool(open_issues, force, account_name)` returns a tool that generates and caches per-ticket summaries in parallel, passing the account name through to the prompt. `generate_qbr_insights(...)` generates `observations` and `opportunities` bullets for the Enterprise Support slide. `generate_usage_insights(...)` generates per-slide summaries, observations, and opportunities for the 3 LangSmith Usage slides (22-24) plus an Engagement Scorecard rollup, all in a single Claude Haiku call so everything stays aligned: slide 22 (commit usage — contract pacing, % commit used), slide 23 (tracing — traces, agent runs, page views, evaluator totals), slide 24 (feature adoption — experiments, Prompt Hub, datasets, evaluator rules by type). Returns `commit_summary`/`tracing_summary`/`feature_summary` (one-sentence per-slide headlines), `usage_summary` (rollup sentence for the Engagement Scorecard, synthesising all 3), and `commit_observations/opportunities`, `tracing_observations/opportunities`, `feature_observations/opportunities` (bullet lists).

**`report.py`** — Self-contained HTML report generator. `generate_report_html(..., is_email=False, banner_url=None, logo_url=None, sections=None)`:
- `is_email=True`: email-safe layout (table-based, no SVG/CSS grid/flex), banner + footer, metric cards 2x2, breakdowns stacked
- `is_email=False`: browser/PDF layout with full CSS, banner inside max-width container
- `sections`: optional `set[str]` of section IDs to include — `key_metrics`, `ticket_trend`, `breakdowns`, `account_summary`, `open_issues`; `None` means all sections

**`ticket_summarizer.py`** — Per-ticket AI output via Claude Haiku. `summarize_ticket(title, body_html, messages, state, account_name)` returns structured `"Summary: ...\nNext steps: ..."` text. State labels are context-aware: `waiting_on_customer` tells the model LangChain has responded and is waiting; `waiting_on_you` means LangChain needs to act; `on_hold` means an internal LangChain team (Engineering/Product) is holding it. `parse_ticket_output(text)` parses the two-line output; old plain-text cache entries fall back to displaying as `next_steps`.

**`cache.py`** — JSON file cache (`.cache/analysis_cache.json`). Per-ticket summaries keyed by `sha256(issue_id:latest_message_time)`; account summaries keyed by `as:{account_id}:{period}`; QBR slide records keyed by `qbr:{account_id}:{YYYY-MM}` (no expiry — permanent).

**`roadmap_client.py`** — Google Slides roadmap parser. `extract_roadmap_items(slides_service, pres_id, month)` parses a roadmap presentation into a list of `{title, description, month, image_url}` items. Handles two multi-feature slide patterns: Pattern A (next text fragment starts with `:`), Pattern B (bullet ends with `:`). Single-feature title extraction joins fragments with `": "`, strips trailing colons, limits to 45 chars. `select_roadmap_items(items, open_issues, account_name, today)` uses Claude Haiku to pick the 3 most relevant items; tags items UPCOMING/DELIVERED relative to `today` and prioritises delivered items that match open feature requests.

**`bigquery_client.py`** — BigQuery client for QBR chart data and maturity scorecard. `fetch_chart_data(metronome_id)` runs 7 queries and returns a dict with these keys (all empty list/dict on failure, never raises):
- `"monthly_usage"` — monthly trace/agent run counts (last 12 months); for SH customers overlays the latest month with `stg_postgres__usage_snapshots` values for experiments/prompt_commits/prompt_pulls/datasets
- `"cumulative_usage"` — daily running totals within the active contract period
- `"page_views"` — monthly LangSmith page view counts (last 12 months)
- `"evaluator_usage"` — monthly evaluator rule counts by category (last 12 months)
- `"contract_metrics"` — single-row dict: `contract_end_date`, `pct_into_contract`, `pct_commit_used` from `dim__contracts WHERE is_active_contract=TRUE`; powers the 4 KPI tiles on slide 22
- `"enablement_stats"` — single-row dict: `academy_enrolled` (distinct contacts with Salesforce Academy Enrollment touchpoints), `billable_seats` (max from `fct__organization_usage_daily` last 30 days), `est_engineering_headcount` (ZoomInfo or `employees × 0.22`); `billable_seats` is the correct denominator — `est_engineering_headcount` can be 20,000+ for large enterprises
- `"sign_ups_by_course"` — list of `{course_name, sign_ups}` dicts from Salesforce Academy Enrollment touchpoints grouped by `source_detail`, ordered by sign_ups DESC

`fetch_maturity_data(metronome_id)` returns maturity dimension scores for the radar chart. `_param(name, value)` is a local helper for parameterised BQ queries. `_rows_to_dicts(rows)` converts BQ Row objects to plain dicts, serialising `date`/`datetime` to ISO strings.

**Gotcha — schema drift breaks chart insertion silently:** the `monthly_usage`/`cumulative_usage` queries read `fct__organization_usage_daily.billable_lsd_runs`/`actual_lsd_runs` (renamed from `billable_agent_runs`/`actual_agent_runs` upstream — the Python-side field names keep the old `*_agent_runs` aliases so no downstream code needed to change). If a BQ query fails, `fetch_chart_data` catches the exception and returns an empty list for that key rather than raising — and `build_commit_usage_from_bq`/`create_usage_composite_from_bq` both return empty bytes when their source rows are empty, which makes `main.py` skip the chart-insertion call entirely (`if commit_bytes: ...`). The net effect: the slide's chart silently never updates (stuck on whatever was last successfully inserted) while AI-generated text from unrelated queries like `contract_metrics` keeps updating fine — easy to mistake for a text/chart sync bug rather than a broken upstream column reference. Check `logs/backend.log` for `fetch_chart_data: ... query failed` warnings when a chart looks frozen.

**`hex_client.py`** — Chart image generation (direct BigQuery path; Hex API path kept for `check_hex_cells.py` validation utility). Key functions:
- `create_usage_composite_from_bq(chart_data)` — renders a 2×2 composite of monthly trace/agent/page-view/evaluator bar charts from BQ data
- `create_feature_usage_composite_from_bq(chart_data)` — renders a 3+2 composite of feature usage bar charts (experiments, prompt commits/pulls, datasets, evaluator rules); the evaluator rules chart is rendered as a **stacked bar chart** when there are multiple series
- `build_commit_usage_from_bq(chart_data)` — renders the commit usage line chart with 4 KPI tiles above it (Contract End Date, % into Contract Period, % Commit Used, Total Traces) using a 2-row GridSpec; tiles are populated from `chart_data["contract_metrics"]`; total traces from the last row of `chart_data["cumulative_usage"]`
- `generate_maturity_radar_from_bq(maturity_data, customer_name)` — renders the Agent Engineering Maturity radar chart from BQ data
- `generate_maturity_bar_from_bq(maturity_data, customer_name)` — renders a horizontal bar chart per dimension (used when radar rendering is requested as the bar view)
- `build_sign_ups_table_from_bq(rows)` — renders a dark-themed matplotlib table of Academy sign-ups by course; caps at top 5 rows; column header is `"Top 5 Courses"` when more than 5 courses exist, `"Course"` otherwise
- `fetch_chart_images(metronome_id, static_ids)` — triggers a Hex notebook run and downloads cell images; utility for `check_hex_cells.py` only
- All chart functions use the dark theme: BG=`#0c0d1a`, CARD=`#161729`, TEXT=`#e2e8f0`, MUTED=`#94a3b8`, BORDER=`#2d3148`

**`slides_client.py`** — Google Slides deck builder. `create_slide_deck(account_name, slide14, slide15, quarter_label, month_label)` copies the template presentation into the customer's Drive folder and applies text replacements. All template substitutions use `{placeholder}` tokens (not literal sentence matching) so wording changes in the template don't silently break replacement. Key replacements in `_build_requests`:
- Enterprise Support slide: `{sev1 status}`, `{{OBSERVATIONS}}`, `{{OPPORTUNITIES}}` (waiting-on-LangChain count, Sev 2-4 breakdown, and in-progress feature request count have no template placeholder in the current deck and aren't computed — that info is shown independently via `priority_breakdown`/`state_breakdown` in Slack and email/PDF reports)
- Product Feedback slide: `{{FEATURE_REQUEST_LIST}}`, `{feature request summary}`
- Enablement & Training slide (slide 26): `{enablement stat}` → real enrolled/seats fraction (or just enrolled count for self-hosted with `billable_seats=0`); `{enablement pct}` → real pct, or `"{total_sign_ups} sign-ups across {num_courses} courses"` fallback when seats is unknown; `{enablement session note}` → `"Instructor-led in-person session for [X] {account_name} professionals"`
- LangSmith Usage slides (22-24) and Engagement Scorecard: `{commit summary}`/`{tracing summary}`/`{feature summary}`/`{usage summary}` plus the `observations`/`opportunities` placeholders documented in the QBR data flow table below
- Global: `[Customer]` / `[CUSTOMER]` → account name (applied last); `[Date]` / `[Customer][date]` used elsewhere in the file for date stamps

**Two-template system**: two Google Slides templates are supported, both stored in the shared Drive `Template` folder:
- `"full_deck"` — `"LangChain QBR Template"`: complete deck with chart slides (maturity radar/bar, commit usage, LangSmith usage, feature usage, enablement, engagement scorecard, etc.)
- `"support_highlights"` — `"LangChain QBR Template - Support Highlights"`: 2-slide deck (Enterprise Support + Product Feedback only)

`TEMPLATE_NAMES` maps the type key to the display name used for Drive lookup. `_TEMPLATE_CONFIGS` maps each hardcoded template presentation ID to its object IDs (shape/image IDs for text boxes, dot rows, roadmap placeholders, chart slide indices). Chart slide indices are `None` in the `support_highlights` config — all chart-insertion functions check for `None` and skip gracefully. `resolve_template_id(template_type)` looks up the presentation ID from the shared Drive `Template` folder by display name, caching the result in `_discovered_template_ids`; falls back to hardcoded IDs if the Drive lookup fails. `_template_ctx` is a `contextvars.ContextVar[str | None]` that holds the resolved presentation ID for the current async request; all functions call `_template_id()` which reads from the context var first, then `QBR_TEMPLATE_ID` env var, then falls back to the production template ID. The context var is set in `_stream()` inside `create_qbr_slides` and in `_do_qbr_generation`, and always reset in `finally`/before return. Run `discover_template_ids.py` against a new template to find the object IDs needed to populate `_TEMPLATE_CONFIGS`.

`add_roadmap_items(pres_id, items)` inserts up to 3 roadmap items using three isolated `batchUpdate` calls (text, image replace with `CENTER_CROP`, white border outline). `add_academy_table(pres_id, customer_folder_id, chart_bytes)` deletes any existing image placeholder on the enablement slide then creates a new image at explicit coordinates (38% from left, 57% from top, 55% wide, 33% tall) so the table is never constrained by a small placeholder element. `_insert_chart_at_slide` is the generic helper used by all other chart-insert functions — it replaces the first image element with `CENTER_INSIDE` if one exists, or creates a new image centred at 60% page size. `share_with_domain(pres_id, domain="langchain.dev")` grants `reader` access to the whole domain — called for all scheduled QBR runs so any `@langchain.dev` team member can open the link.

**`audit.py`** — JSONL audit log (`.cache/audit.jsonl`).

**`report_dispatcher.py`** — LangGraph graph registered as `"report_dispatcher"` in `langgraph.json`. Single node `send_report` that:
1. Evaluates `run_condition` (e.g. `{"type": "nth_weekday_of_month", "n": 1, "weekday": 0}` = first Monday of month) — skips if today doesn't match
2. Fetches fresh Pylon data for the account (Slack/email only; QBR generates its own data)
3. Sends a Slack Block Kit message, HTML email, or generates QBR slides, respecting `destination_type`

For QBR destinations: calls `_do_qbr_generation(account_id, account_name, template_type)` from `main.py` — `template_type` comes from `state["qbr_template_type"]` (defaults `"full_deck"` for existing schedules without the field). Runs the full pipeline (Pylon fetch, AI insights, slide deck, roadmap, metrics chart, domain share, cache), skipping BigQuery chart steps for `"support_highlights"`. Then sends a Slack notification or styled HTML email with the slide URL to the configured `qbr_notify_*` channel/addresses. The domain share grants any `@langchain.dev` account reader access via `slides_client.share_with_domain`.

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

**`src/app/page.tsx`** — Main dashboard. Loads available tiers on mount; re-fetches accounts when the selected tier changes. Fetches account data on account selection. Manages filtering/sorting client-side. Polls cached ticket summaries every 2s while the summary agent runs. Reads `?account=<slug>` on mount for deep links; updates the URL on every account switch so all views are shareable. Account names are slugified (`toSlug`: lowercase, apostrophes/brackets stripped, non-alphanumeric runs → hyphens). State labels (e.g. "Waiting on Customer") use the actual account name via `getStateLabels(accountName)`. Shows a centered empty state when no account is selected. Model picker offers `claude-sonnet-4-6` (default), `claude-opus-4-6`, and `claude-haiku-4-5-20251001`.

The **QBR Slides** popover (header button) lazily fetches history on first open and shows the last 6 months filtered to months that have slides plus the current month. A **Full Deck / Support Slides** toggle at the top selects the template (defaults to Full Deck, resets on account change). Each row shows month name, generation date, an Open link (if slides exist), and a Generate/refresh-icon button (current month only). Generate runs immediately using the selected template; the refresh icon shows a custom confirm modal before overwriting. Progress steps are shown inline while generating; the month list is hidden during generation. Switching accounts resets all QBR state. `runQbrGeneration(account, templateType)` is a shared `useCallback` used by both the generate button and the confirm modal. An indicator row at the bottom of the popover shows the count of active QBR schedules (fetched alongside history); tapping "Set up →" (0 schedules) opens the modal directly to a new QBR form; tapping "View →" (N schedules) opens the modal to the list view.

**`src/app/api/[...path]/route.ts`** — Catch-all proxy. Forwards all headers (including `cookie` and `authorization`) to the backend. Injects `x-api-key` for LSD authentication server-side.

**`src/app/api/accounts/[accountId]/summary/route.ts`** — Custom route handler for the summary SSE stream. Buffers the stream and returns plain JSON once the `result` event arrives. Explicitly forwards `cookie` and `authorization` headers (the catch-all does this automatically; this handler has its own header dict).

**`src/components/Sidebar.tsx`** — Fixed left sidebar with LangChain logo, support tier selector, account selector, period selector, refresh button, model selector, and Settings menu (light/dark toggle + sign out). Collapsible.

**`src/components/ScheduleModal.tsx`** — Two-view modal for managing scheduled reports, opened via the "Schedule" button in the page header.
- **List view**: active schedules per account, each card showing label + `[Slack/Email/QBR]` pill, compact schedule description (e.g. "4th Wed of every month at 08:00 PDT · 6 months"), destination, and next run + created-by metadata. QBR cards show dual icons (Presentation + Slack or Mail). Edit (pencil) and delete (trash) actions per card. "Add Schedule" button pinned at the bottom.
- **Form view**: create/edit form with back-arrow navigation. Fields: label (optional), destination toggle (Slack / Email / QBR Slides), report period (hidden for QBR), sections (hidden for QBR), frequency (Weekly/Monthly/Quarterly for Slack/email; Monthly/Quarterly only for QBR, defaulting to Quarterly), occurrence in month (1st–Last), month of quarter (quarterly only), day of week, time + timezone picker.
- **QBR form**: when QBR destination is selected, shows a **Full Deck / Support Slides** template toggle (defaults to Full Deck), a Slack/Email sub-toggle for the notification delivery, then a channel picker or email chip input (placeholder `name@langchain.dev`). Period and Sections fields are hidden. The selected template type is stored as `qbr_template_type` in the cron payload and shown in the list card as e.g. `"Full Deck · Notify: #channel"`.
- **`openToNewQbr` prop**: when `true`, the modal opens directly to the new-schedule form with QBR pre-selected and frequency set to Quarterly.
- **Timezone picker**: 51 IANA timezones ordered west-to-east; UTC at its natural position. Labels show DST-aware abbreviation + offset (e.g. "Los Angeles (PDT · GMT-7)") computed via `Intl.DateTimeFormat` with a `getTzAbbr` lookup table for international zones. `tzShort(tz)` returns just the abbreviation for compact schedule descriptions.
- **Email chip input** (`EmailTagInput`): emails displayed as removable chips; Enter/comma/space (when input contains `@`) commits a chip; paste of comma- or whitespace-separated lists splits automatically; Backspace removes the last chip.
- **Quarterly model**: `month_in_quarter` (1–3) selects which month of the quarter, combined with the standard nth-weekday picker. Produces `nth_weekday_of_month_in_quarter` run conditions.
- On submit success: `setView("list")` returns to list view; edit is implemented as delete + recreate.

**`src/components/SlackIcon.tsx`** — Shared Slack brand mark SVG component (official paths, 270×270 viewBox cropped to 73.6 73.6 122.8 122.8). Used by `ScheduleModal` and `ShareButton`.

**`src/components/ShareButton.tsx`** — Combined share button for Slack and email. Opens a popover with a Slack/Email mode tab, section checkboxes (Key Metrics, Ticket Trend, Breakdowns, Account Summary, Open Issues — all checked by default), a channel picker (Slack, when multiple channels available) or email input, and a send button. Success status clears after 10 seconds; the popover stays open until dismissed by clicking outside.

**`src/components/DownloadMenu.tsx`** — Download popover with PDF/CSV format tabs and section checkboxes (same sections as ShareButton). Account Summary is greyed out and disabled for CSV (not available in that format), with a "Not available in CSV" tooltip on hover. Download button is disabled if no sections are selected.

**`src/lib/api.ts`** — TypeScript fetch functions. All functions check for 401 and redirect to `/login` via `window.location.href`. The `Schedule` interface includes `month_in_quarter`, `hour_local`, `timezone`, `created_by`, `qbr_notify_type`/`qbr_notify_channel_id`/`qbr_notify_emails`, and `qbr_template_type` fields; `destination_type` is `"slack" | "email" | "qbr"`. `createSchedule` handles Pydantic validation errors (which return `detail` as an array) by joining the `msg` fields into a readable string. `QbrSlide`, `QbrHistoryEntry` interfaces and `fetchQbrHistory`, `streamQbrSlides` functions support the QBR slides feature; `streamQbrSlides` accepts an optional `templateType` parameter (`"full_deck"` default) forwarded in the POST body; reads an SSE stream of `progress`/`result`/`error` events.

**`src/lib/downloads.ts`** — `downloadPdf` opens the `/report` endpoint in a new tab; accepts optional `sections?: string[]` appended as repeated query params. `downloadCsv` builds and downloads a CSV blob client-side; accepts optional `sections?: string[]` and conditionally includes each section (KEY METRICS, TICKET TREND, PRIORITY/STATE/DISPOSITION BREAKDOWNS, OPEN TICKETS — Account Summary has no CSV representation and is ignored). `slackReport` and `emailReport` both accept an optional `sections?: string[]` forwarded to the backend.

### QBR slide generation data flow

The `slide14` dict is the primary data carrier for the QBR deck. It is built in `main.py` from multiple sources and passed to `slides_client._build_requests`:

| Key | Source | Used for |
|-----|---------|----------|
| `open_tickets`, `waiting_on_langchain`, `sev1/2/3/4_tickets`, `sev1/2/3/4_since_*` | Pylon API | Enterprise Support slide text |
| `fr_list`, `fr_count`, `delivered_count` | Pylon API + Claude | Product Feedback slide |
| `observations`, `opportunities` | Claude (AI insights) | Enterprise Support slide observations/opportunities bullets |
| `commit_summary` | Claude (AI insights) | Slide 22 (commit usage) headline — `{commit summary}` template placeholder |
| `commit_observations`, `commit_opportunities` | Claude (AI insights) | Slide 22 (commit usage) — `{commit observations}` / `{commit opportunities}` template placeholders |
| `tracing_summary` | Claude (AI insights) | Slide 23 (tracing) headline — `{tracing summary}` template placeholder |
| `tracing_observations`, `tracing_opportunities` | Claude (AI insights) | Slide 23 (tracing) — `{tracing observations}` / `{tracing opportunities}` template placeholders |
| `feature_summary` | Claude (AI insights) | Slide 24 (feature adoption) headline — `{feature summary}` template placeholder |
| `feature_observations`, `feature_opportunities` | Claude (AI insights) | Slide 24 (feature adoption) — `{feature observations}` / `{feature opportunities}` template placeholders |
| `usage_summary` | Claude (AI insights) | LangSmith Engagement Scorecard rollup headline, synthesises commit/tracing/feature summaries — `{usage summary}` template placeholder |
| `academy_enrolled` | BigQuery `enablement_stats` | Enablement & Training slide |
| `billable_seats` | BigQuery `enablement_stats` | Denominator for enrolled % (preferred over `est_engineering_headcount` which can be 20k+ for large enterprises) |
| `est_engineering_headcount` | BigQuery `enablement_stats` | Stored but not used as denominator |
| `total_sign_ups` | Computed in main.py from `sign_ups_by_course` | Fallback text when `billable_seats=0` |
| `num_courses` | Computed in main.py from `sign_ups_by_course` | Fallback text when `billable_seats=0` |

The `sign_ups_by_course` list from BigQuery is rendered by `hex_client.build_sign_ups_table_from_bq` (top 5 courses, dark-themed table) and inserted into slide 26 by `slides_client.add_academy_table`. The academy table function always deletes the template placeholder and creates a fresh image at explicit coordinates (not `replaceImage CENTER_INSIDE`) so its size is never constrained by the original placeholder.

`contract_metrics` from BigQuery powers the 4 KPI tiles rendered by `build_commit_usage_from_bq` at the top of the commit usage chart on slide 22.

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
│   ├── bigquery_client.py      # BigQuery client for QBR chart data (7 queries)
│   ├── hex_client.py           # Chart image generation (Hex API + direct BQ matplotlib)
│   ├── cache.py                # JSON file cache (ticket summaries, account summaries, QBR slides)
│   ├── audit.py                # JSONL audit log
│   ├── check_hex_cells.py      # Utility: fetch & save Hex chart PNGs for visual validation
│   ├── discover_template_ids.py # Utility: print shape/image object IDs from a QBR template deck
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
