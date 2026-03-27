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
  csat: number | null;
  priority_breakdown: Record<string, number>;
  state_breakdown: Record<string, number>;
  disposition_breakdown: Record<string, number>;
}

/** Fetch all premium accounts, sorted alphabetically by name. */
export async function fetchAccounts(): Promise<Account[]> {
  const res = await fetch("/api/accounts");
  if (!res.ok) {
    throw new Error(`Failed to fetch accounts: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/** Fetch open issues, period metrics, and breakdowns for a single account. */
export async function fetchAccountData(
  accountId: string,
  accountName: string,
  period: string = "6m"
): Promise<AccountData> {
  const params = new URLSearchParams({ account_name: accountName, period });
  const res = await fetch(`/api/accounts/${accountId}/data?${params}`);
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
export async function fetchCachedTicketSummaries(accountId: string): Promise<Record<number, string>> {
  const res = await fetch(`/api/accounts/${accountId}/cached-ticket-summaries`);
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
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(
      (data as { detail?: string }).detail ||
        `Failed to generate summary: ${res.status} ${res.statusText}`
    );
  }

  const data = await res.json();
  return (data as { summary: string }).summary;
}
