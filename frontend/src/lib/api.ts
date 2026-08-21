export interface Account {
  id: string;
  name: string;
}

export interface ExternalIssue {
  source: string;
  external_id: string;
  link: string;
}

export interface Issue {
  number: number;
  title: string;
  state: string;
  priority: string;
  created_at: string;
  tags: string[];
  disposition: string;
  external_issues: ExternalIssue[];
  portal_url: string | null;
}

export interface MonthlyMetric {
  month: string;
  tickets_raised: number;
  closed_tickets: number;
}

export interface AccountData {
  open_issues: Issue[];
  monthly_metrics: MonthlyMetric[];
  avg_response_time: number | null;
  avg_resolution_time: number | null;
  sla_compliance_pct: number | null;
  csat: number | null;
  priority_breakdown: Record<string, number>;
  state_breakdown: Record<string, number>;
  disposition_breakdown: Record<string, number>;
}

function handleUnauthorized(res: Response): void {
  if (res.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
  }
}

/** Fetch available support tiers, sorted alphabetically. */
export async function fetchTiers(): Promise<string[]> {
  const res = await fetch("/api/tiers");
  if (res.status === 401) { handleUnauthorized(res); return []; }
  if (!res.ok) {
    throw new Error(`Failed to fetch tiers: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export interface LlmModel {
  id: string;
  label: string;
  provider: string;
  model: string;
}

/** Fetch LLM models available through the LangSmith Gateway. */
export async function fetchModels(): Promise<LlmModel[]> {
  const res = await fetch("/api/models");
  if (res.status === 401) { handleUnauthorized(res); return []; }
  if (!res.ok) {
    throw new Error(`Failed to fetch models: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/** Fetch accounts for the given support tier, sorted alphabetically by name. */
export async function fetchAccounts(tier: string = "Premium"): Promise<Account[]> {
  const res = await fetch(`/api/accounts?${new URLSearchParams({ tier })}`);
  if (res.status === 401) { handleUnauthorized(res); return []; }
  if (!res.ok) {
    throw new Error(`Failed to fetch accounts: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/** Fetch open issues, period metrics, and breakdowns for a single account. */
export async function fetchAccountData(
  accountId: string,
  accountName: string,
  period: string = "6m",
  force = false,
): Promise<AccountData> {
  const params = new URLSearchParams({ account_name: accountName, period });
  if (force) params.set("force", "true");
  const res = await fetch(`/api/accounts/${accountId}/data?${params}`);
  if (res.status === 401) { handleUnauthorized(res); throw new Error("Not authenticated"); }
  if (!res.ok) {
    throw new Error(
      `Failed to fetch account data: ${res.status} ${res.statusText}`
    );
  }
  return res.json();
}

/**
 * Return any ticket summaries already cached from the last agent run,
 * keyed by ticket number. Polled every 2s while the summary agent is running
 * so ticket cards populate progressively as Haiku finishes each summary.
 */
export interface TicketSummary {
  summary: string;
  next_steps: string;
}

export async function fetchCachedTicketSummaries(accountId: string, model: string = ""): Promise<Record<number, TicketSummary>> {
  const params = model ? `?model=${encodeURIComponent(model)}` : "";
  const res = await fetch(`/api/accounts/${accountId}/cached-ticket-summaries${params}`);
  if (res.status === 401) { handleUnauthorized(res); return {}; }
  if (!res.ok) {
    throw new Error(`Failed to fetch ticket summaries: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/**
 * Trigger the AI summary pipeline and return the account-level summary text.
 *
 * The backend streams SSE keepalive pings while Claude works, then sends a final
 * `result` event. The Next.js route handler at /app/api/.../summary/route.ts
 * converts that stream to plain JSON, so this function just awaits a normal response.
 *
 * Set force=true to bypass the ticket summary disk cache and regenerate everything.
 */
export async function generateSummary(
  accountId: string,
  accountName: string,
  model: string,
  period: string = "6m",
  force = false,
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetch(`/api/accounts/${accountId}/summary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_name: accountName, model, period, force }),
    signal,
  });
  if (res.status === 401) { handleUnauthorized(res); throw new Error("Not authenticated"); }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(
      (data as { detail?: string; error?: string }).detail ||
      (data as { error?: string }).error ||
        `Failed to generate summary: ${res.status} ${res.statusText}`
    );
  }

  const data = await res.json();
  return (data as { summary: string }).summary;
}

// ---------------------------------------------------------------------------
// Scheduled reports
// ---------------------------------------------------------------------------

export interface Schedule {
  cron_id: string;
  account_id: string;
  account_name: string;
  label: string;
  destination_type: "slack" | "email" | "qbr";
  channel_id: string | null;
  email_addresses: string[] | null;
  qbr_notify_type: "slack" | "email" | null;
  qbr_notify_channel_id: string | null;
  qbr_notify_emails: string[] | null;
  qbr_template_type: "full_deck" | "support_highlights";
  sections: string[] | null;
  period: string;
  model: string;
  frequency: "weekly" | "monthly" | "quarterly";
  weekday: number;          // 0=Mon … 6=Sun (Python weekday)
  nth: number;              // 1–4 or -1 (last); ignored for weekly
  month_in_quarter: number; // 1–3; which month of the quarter (quarterly only)
  hour_utc: number;   // actual UTC hour in the cron expression
  hour_local: number; // display hour in the configured timezone
  timezone: string;   // IANA timezone name
  created_by: string | null;
  schedule: string;
  next_run_date: string | null;
  created_at: string;
  channel_warning: string | null; // set when the bot isn't a member of the target Slack channel
}

export interface CreateScheduleRequest {
  account_id: string;
  account_name: string;
  label: string;
  destination_type: "slack" | "email" | "qbr";
  channel_id?: string;
  email_addresses?: string[];
  qbr_notify_type?: "slack" | "email";
  qbr_notify_channel_id?: string;
  qbr_notify_emails?: string[];
  qbr_template_type?: "full_deck" | "support_highlights";
  sections?: string[];
  period: string;
  model?: string;
  frequency: "weekly" | "monthly" | "quarterly";
  weekday: number;
  nth: number;
  month_in_quarter: number;
  hour_local: number;
  timezone: string;
}

export async function fetchSchedules(accountId?: string): Promise<Schedule[]> {
  const params = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  const res = await fetch(`/api/schedules${params}`);
  if (res.status === 401) { handleUnauthorized(res); return []; }
  if (!res.ok) throw new Error(`Failed to fetch schedules: ${res.status}`);
  return res.json();
}

export async function createSchedule(req: CreateScheduleRequest): Promise<Schedule> {
  const res = await fetch("/api/schedules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (res.status === 401) { handleUnauthorized(res); throw new Error("Not authenticated"); }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = (err as { detail?: string | { msg: string }[] }).detail;
    const message = Array.isArray(detail)
      ? detail.map((e) => e.msg).join("; ")
      : detail ?? `Failed to create schedule: ${res.status}`;
    throw new Error(message);
  }
  return res.json();
}

export type QbrStepStatus = "pending" | "running" | "done";

export interface QbrSlide {
  url: string;
  pres_id: string;
  created_at: string;
  month_label: string;
}

export interface QbrHistoryEntry {
  month: string;        // "2026-04"
  month_label: string;  // "April 2026"
  is_current: boolean;
  slide: QbrSlide | null;
}

/** Ensure the current user has writer access to a previously generated QBR slide. */
export async function shareQbrSlide(accountId: string, presId: string): Promise<void> {
  const res = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/qbr-slides/${encodeURIComponent(presId)}/share`, {
    method: "POST",
  });
  if (res.status === 401) { handleUnauthorized(res); return; }
  // Non-fatal: user may already have access
}

export async function deleteQbrSlide(accountId: string, yearMonth: string): Promise<void> {
  const res = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/qbr-slides/${encodeURIComponent(yearMonth)}`, {
    method: "DELETE",
  });
  if (res.status === 401) { handleUnauthorized(res); return; }
  if (!res.ok) throw new Error(`Failed to delete QBR slide: ${res.status}`);
}

export async function fetchQbrHistory(accountId: string, accountName: string): Promise<QbrHistoryEntry[]> {
  const params = new URLSearchParams({ account_name: accountName });
  const res = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/qbr-slides/history?${params}`);
  if (res.status === 401) { handleUnauthorized(res); return []; }
  if (!res.ok) throw new Error(`Failed to fetch QBR history: ${res.status}`);
  return res.json();
}

export async function streamQbrSlides(
  accountId: string,
  accountName: string,
  onProgress: (step: string, label: string, status: "running" | "done") => void,
  templateType: "full_deck" | "support_highlights" = "full_deck",
  model: string = "",
): Promise<string> {
  const res = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/qbr-slides`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_name: accountName, template_type: templateType, model }),
  });
  if (res.status === 401) { handleUnauthorized(res); return ""; }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Failed to generate QBR slides: ${res.status}`);
  }

  if (!res.body) throw new Error("Response body is null");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let url = "";

  outer: while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      let eventName = "";
      let dataStr = "";
      for (const line of part.split("\n")) {
        if (line.startsWith("event: ")) eventName = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataStr = line.slice(6).trim();
      }
      if (!eventName || !dataStr) continue;
      const data = JSON.parse(dataStr);
      if (eventName === "progress") {
        onProgress(data.step, data.label, data.status);
      } else if (eventName === "result") {
        url = data.url;
        break outer;
      } else if (eventName === "error") {
        throw new Error(data.detail ?? "QBR generation failed");
      }
    }
  }
  return url;
}

/**
 * Live pre-save check: is the bot a member of this Slack channel?
 * Returns a human-readable warning message (with an /invite command) if not,
 * or null if the bot is present, the channel couldn't be checked, or the
 * check itself failed — always fails soft, never throws.
 */
export async function checkSlackChannel(channelId: string): Promise<string | null> {
  try {
    const res = await fetch(`/api/slack/channel-check?${new URLSearchParams({ channel_id: channelId })}`);
    if (res.status === 401) { handleUnauthorized(res); return null; }
    if (!res.ok) return null;
    const data = await res.json();
    return (data as { warning?: string | null }).warning ?? null;
  } catch {
    return null;
  }
}

export async function deleteSchedule(cronId: string): Promise<void> {
  const res = await fetch(`/api/schedules/${encodeURIComponent(cronId)}`, { method: "DELETE" });
  if (res.status === 401) { handleUnauthorized(res); return; }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Failed to delete schedule: ${res.status}`);
  }
}

// ---------------------------------------------------------------------------
// Internal Support-Team Dashboard
// ---------------------------------------------------------------------------

export interface MeInfo {
  email: string;
  is_support_team_member: boolean;
  is_admin: boolean;
}

/** Fetch the current session's email and team-dashboard authorization flags. */
export async function fetchMe(): Promise<MeInfo | null> {
  const res = await fetch("/api/me");
  if (res.status === 401) { handleUnauthorized(res); return null; }
  if (!res.ok) {
    throw new Error(`Failed to fetch current user: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export interface RepMetrics {
  tickets_taken: number;
  avg_response_time: number | null;
  median_response_time: number | null;
  sla_compliance_pct: number | null;
  avg_resolution_time: number | null;
  median_resolution_time: number | null;
  backlog_count: number;
  avg_csat: number | null;
  median_csat: number | null;
  update_count: number;
  avg_reply_time: number | null;
  median_reply_time: number | null;
  state_breakdown: Record<string, number>;
  pending_wait: Record<string, { count: number; avg_wait_hours: number; median_wait_hours: number }>;
}

export interface RepTrendPoint {
  label: string;
  tickets_taken: number;
  avg_response_time: number | null;
  median_response_time: number | null;
  avg_resolution_time: number | null;
  median_resolution_time: number | null;
  update_count: number;
  avg_reply_time: number | null;
  median_reply_time: number | null;
}

export interface TeamDashboardData {
  me: RepMetrics;
  me_trend: RepTrendPoint[];
  team_average: RepMetrics;
  team_average_trend: RepTrendPoint[];
  team_median: RepMetrics;
  team_median_trend: RepTrendPoint[];
  team_member_count: number;
  viewing_as: { email: string; name: string } | null;
}

/**
 * Fetch a member's metrics vs. the Support-team average for a period.
 * Pass `asEmail` (admin-only, 403 otherwise) to view another member's metrics
 * instead of the caller's own. Pass `force=true` (mirrors fetchAccountData's
 * force param) to bypass the cache and refetch from Pylon. Pass `granularity`
 * ("day" | "week" | "month") to override the period's default trend-chart
 * bucketing; omit to keep that default.
 */
export async function fetchTeamDashboardData(
  period: string,
  asEmail?: string,
  force = false,
  granularity?: "day" | "week" | "month",
): Promise<TeamDashboardData> {
  const params = new URLSearchParams({ period });
  if (asEmail) params.set("as", asEmail);
  if (force) params.set("force", "true");
  if (granularity) params.set("granularity", granularity);
  const res = await fetch(`/api/team-dashboard/data?${params}`);
  if (res.status === 401) { handleUnauthorized(res); throw new Error("Not authenticated"); }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Failed to fetch team dashboard data: ${res.status}`);
  }
  return res.json();
}

export interface TeamMember {
  email: string;
  name: string;
  is_admin: boolean;
  metrics: RepMetrics;
}

export interface TeamOverview {
  members: TeamMember[];
  team_average: RepMetrics;
  team_average_trend: RepTrendPoint[];
  team_median: RepMetrics;
  team_median_trend: RepTrendPoint[];
}

/** Admin-only: every Support-team member's individual metrics + team average. */
export async function fetchTeamOverview(period: string, member?: string, force = false): Promise<TeamOverview> {
  const params = new URLSearchParams({ period });
  if (member) params.set("member", member);
  if (force) params.set("force", "true");
  const res = await fetch(`/api/team-dashboard/team?${params}`);
  if (res.status === 401) { handleUnauthorized(res); throw new Error("Not authenticated"); }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Failed to fetch team overview: ${res.status}`);
  }
  return res.json();
}

/** Admin-only: list current team-dashboard admin emails. */
export async function fetchAdmins(): Promise<string[]> {
  const res = await fetch("/api/team-dashboard/admins");
  if (res.status === 401) { handleUnauthorized(res); throw new Error("Not authenticated"); }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Failed to fetch admins: ${res.status}`);
  }
  const data = await res.json();
  return data.admins ?? [];
}

/** Admin-only: add an email to the team-dashboard admin list. */
export async function addAdmin(email: string): Promise<string[]> {
  const res = await fetch("/api/team-dashboard/admins", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (res.status === 401) { handleUnauthorized(res); throw new Error("Not authenticated"); }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Failed to add admin: ${res.status}`);
  }
  const data = await res.json();
  return data.admins ?? [];
}

/** Admin-only: remove an email from the team-dashboard admin list. */
export async function removeAdmin(email: string): Promise<string[]> {
  const res = await fetch(`/api/team-dashboard/admins/${encodeURIComponent(email)}`, { method: "DELETE" });
  if (res.status === 401) { handleUnauthorized(res); throw new Error("Not authenticated"); }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Failed to remove admin: ${res.status}`);
  }
  const data = await res.json();
  return data.admins ?? [];
}

export interface MessageActivitySyncStatus {
  stage: "7d" | "1m" | "3m" | "6m" | "1y" | null;
  stages_completed: string[];
  current_stage_synced: number;
  current_stage_total: number;
  complete: boolean;
  started_at: string | null;
  last_incremental_sync_at: string | null;
}

/** Progress of the background ticket-update-history backfill (see message_activity.py). */
export async function fetchMessageActivitySyncStatus(): Promise<MessageActivitySyncStatus | null> {
  const res = await fetch("/api/team-dashboard/sync-status");
  if (res.status === 401) { handleUnauthorized(res); return null; }
  if (!res.ok) return null;
  return res.json();
}
