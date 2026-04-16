import type { AccountData, Issue } from "./api";

const PRIORITY_LABELS: Record<string, string> = {
  urgent: "Sev 1", high: "Sev 2", medium: "Sev 3", low: "Sev 4", none: "None",
};

const STATE_LABELS: Record<string, string> = {
  new: "New",
  waiting_on_you: "Waiting on LangChain",
  on_hold: "On Hold",
  waiting_on_customer: "Waiting on Customer",
  closed: "Closed",
  resolved: "Resolved",
};

const PERIOD_LABELS: Record<string, string> = {
  "7d": "7 Days", "1m": "1 Month", "3m": "3 Months", "6m": "6 Months", "1y": "1 Year",
};

function cell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const s = String(value);
  return s.includes(",") || s.includes('"') || s.includes("\n")
    ? `"${s.replace(/"/g, '""')}"`
    : s;
}

export function downloadCsv(
  accountName: string,
  period: string,
  data: AccountData,
  issues: Issue[],
  ticketSummaries: Record<number, string | null>,
): void {
  const rows: string[] = [];
  const periodLabel = PERIOD_LABELS[period] ?? period;
  const date = new Date().toLocaleDateString("en-GB", {
    day: "numeric", month: "long", year: "numeric",
  });
  const totalRaised = data.monthly_metrics.reduce((s, m) => s + m.tickets_raised, 0);
  const totalClosed = data.monthly_metrics.reduce((s, m) => s + m.closed_tickets, 0);

  rows.push(`Account,${cell(accountName)}`);
  rows.push(`Period,${cell(periodLabel)}`);
  rows.push(`Generated,${cell(date)}`);
  rows.push("");

  rows.push("KEY METRICS");
  rows.push(`Open Issues,${data.open_issues.length}`);
  rows.push(`Tickets Raised (${periodLabel}),${totalRaised}`);
  rows.push(`Tickets Closed (${periodLabel}),${totalClosed}`);
  if (data.avg_response_time !== null) {
    rows.push(`Avg Response Time (hrs),${data.avg_response_time.toFixed(1)}`);
  }
  if (data.csat !== null) {
    rows.push(`CSAT,${data.csat % 1 === 0 ? data.csat : data.csat.toFixed(1)}`);
  }
  rows.push("");

  rows.push("MONTHLY TREND");
  rows.push("Month,Raised,Closed");
  for (const m of data.monthly_metrics) {
    rows.push(`${cell(m.month)},${m.tickets_raised},${m.closed_tickets}`);
  }
  rows.push("");

  const breakdowns: [string, Record<string, number>, Record<string, string> | null][] = [
    ["PRIORITY BREAKDOWN", data.priority_breakdown, PRIORITY_LABELS],
    ["STATE BREAKDOWN", data.state_breakdown, STATE_LABELS],
    ["DISPOSITION BREAKDOWN", data.disposition_breakdown, null],
  ];
  for (const [title, breakdown, labels] of breakdowns) {
    if (Object.keys(breakdown).length > 0) {
      rows.push(title);
      rows.push("Label,Count");
      for (const [key, count] of Object.entries(breakdown)) {
        const label = labels ? (labels[key] ?? key.replace(/_/g, " ")) : key;
        rows.push(`${cell(label)},${count}`);
      }
      rows.push("");
    }
  }

  rows.push("OPEN TICKETS");
  rows.push("Number,Title,State,Priority,Disposition,Created,AI Summary");
  for (const issue of issues) {
    const state = STATE_LABELS[issue.state] ?? issue.state.replace(/_/g, " ");
    const priority = PRIORITY_LABELS[issue.priority] ?? issue.priority;
    const summary = ticketSummaries[issue.number] ?? "";
    rows.push([
      issue.number,
      cell(issue.title),
      cell(state),
      cell(priority),
      cell(issue.disposition),
      cell(issue.created_at ? issue.created_at.split("T")[0] : ""),
      cell(summary),
    ].join(","));
  }

  const blob = new Blob([rows.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${accountName.replace(/[^a-z0-9]/gi, "_")}_${period}_${new Date().toISOString().split("T")[0]}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadPdf(accountId: string, accountName: string, period: string, sortBy: string, sortOrder: string): void {
  const params = new URLSearchParams({ account_name: accountName, period, sort_by: sortBy, sort_order: sortOrder });
  window.open(`/api/accounts/${accountId}/report?${params}`, "_blank");
}

export async function slackReport(
  accountId: string,
  accountName: string,
  period: string,
  channelId?: string,
): Promise<void> {
  const res = await fetch(`/api/accounts/${accountId}/slack-report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_name: accountName, period, ...(channelId ? { channel_id: channelId } : {}) }),
  });
  if (res.status === 401) {
    if (typeof window !== "undefined") window.location.href = "/login";
    return;
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? "Failed to send to Slack");
  }
}

export async function emailReport(
  accountId: string,
  accountName: string,
  email: string,
  period: string,
  sortBy: string,
  sortOrder: string,
): Promise<void> {
  const res = await fetch(`/api/accounts/${accountId}/email-report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, account_name: accountName, period, sort_by: sortBy, sort_order: sortOrder }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? "Failed to send email");
  }
}
