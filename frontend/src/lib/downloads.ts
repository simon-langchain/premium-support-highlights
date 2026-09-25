import type { MemberLabel } from "@/lib/accountLabels";
import type { ExportOptions } from "@/components/ExportOptions";
import { fetchReportData, type AccountData, type DuplicateGroup, type Issue, type TicketSummary } from "./api";

const PRIORITY_LABELS: Record<string, string> = {
  urgent: "Sev 1", high: "Sev 2", medium: "Sev 3", low: "Sev 4", none: "None",
};

function getStateLabels(accountName: string): Record<string, string> {
  return {
    new: "New",
    waiting_on_you: "Waiting on LangChain",
    on_hold: "On Hold",
    waiting_on_customer: `Waiting on ${accountName}`,
    closed: "Closed",
    resolved: "Resolved",
  };
}

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

const ALL_SECTION_IDS = ["key_metrics", "ticket_trend", "breakdowns", "account_summary", "open_issues"];

export function downloadCsv(
  accountName: string,
  period: string,
  data: AccountData,
  issues: Issue[],
  ticketSummaries: Record<number, TicketSummary | null>,
  sections?: string[],
  /** Account group view: member account per ticket, added as an "Account" column (full name). */
  members?: Map<string, MemberLabel> | null,
  /** linkedIds adds a "Linked tickets" column; duplicates adds "Possible duplicate of" (needs duplicateGroups). */
  options?: ExportOptions,
  duplicateGroups?: DuplicateGroup[],
): void {
  const secs = new Set(sections ?? ALL_SECTION_IDS);
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

  if (secs.has("key_metrics")) {
    rows.push("KEY METRICS");
    rows.push(`Open Issues (Current),${data.open_issues.length}`);
    rows.push(`Tickets Raised (${periodLabel}),${totalRaised}`);
    rows.push(`Tickets Closed (${periodLabel}),${totalClosed}`);
    if (data.avg_response_time !== null) {
      rows.push(`Avg Time to First Response (${periodLabel}) hrs,${data.avg_response_time.toFixed(1)}`);
    }
    if (data.avg_resolution_time !== null) {
      rows.push(`Avg Resolution Time (${periodLabel}) hrs,${data.avg_resolution_time.toFixed(1)}`);
    }
    if (data.sla_compliance_pct !== null) {
      rows.push(`SLA Compliance (${periodLabel}),${data.sla_compliance_pct}%`);
    }
    if (data.csat !== null) {
      rows.push(`CSAT (${periodLabel}),${data.csat % 1 === 0 ? data.csat : data.csat.toFixed(1)}`);
    }
    rows.push("");
  }

  if (secs.has("ticket_trend")) {
    rows.push("TICKET TREND");
    rows.push("Month,Raised,Closed");
    for (const m of data.monthly_metrics) {
      rows.push(`${cell(m.month)},${m.tickets_raised},${m.closed_tickets}`);
    }
    rows.push("");
  }

  if (secs.has("breakdowns")) {
    const breakdowns: [string, Record<string, number>, Record<string, string> | null][] = [
      ["PRIORITY BREAKDOWN", data.priority_breakdown, PRIORITY_LABELS],
      ["STATE BREAKDOWN", data.state_breakdown, getStateLabels(accountName)],
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
  }

  if (secs.has("open_issues")) {
    rows.push("OPEN TICKETS");
    const showLinkedIds = !!options?.linkedIds;
    const dupesOf = new Map<number, number[]>();
    if (options?.duplicates) {
      for (const g of duplicateGroups ?? []) for (const n of g.tickets) dupesOf.set(n, g.tickets.filter((t) => t !== n));
    }
    rows.push(`Number,${members ? "Account," : ""}Title,Requester,State,Priority,Disposition,Created,Summary,Next steps,${showLinkedIds ? "Linked tickets," : ""}${options?.duplicates ? "Possible duplicate of," : ""}Portal URL`);
    for (const issue of issues) {
      const state = getStateLabels(accountName)[issue.state] ?? issue.state.replace(/_/g, " ");
      const priority = PRIORITY_LABELS[issue.priority] ?? issue.priority;
      const entry = ticketSummaries[issue.number];
      const member = members && issue.account_id ? members.get(issue.account_id) : undefined;
      rows.push([
        issue.number,
        ...(members ? [cell(member?.fullName ?? "")] : []),
        cell(issue.title),
        cell(issue.requester_name ?? ""),
        cell(state),
        cell(priority),
        cell(issue.disposition),
        cell(issue.created_at ? issue.created_at.split("T")[0] : ""),
        cell(entry?.summary ?? ""),
        cell(entry?.next_steps ?? ""),
        ...(showLinkedIds
          ? [cell((issue.external_issues ?? []).map((ei) => ei.display_id).filter(Boolean).join(", "))]
          : []),
        ...(options?.duplicates ? [cell((dupesOf.get(issue.number) ?? []).map((n) => `#${n}`).join(", "))] : []),
        cell(issue.portal_url ?? ""),
      ].join(","));
    }
  }

  const blob = new Blob([rows.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${accountName.replace(/[^a-z0-9]/gi, "_")}_${period}_${new Date().toISOString().split("T")[0]}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/** Build the report as a real PDF in the browser (React-PDF, loaded on first use) and
 * download it. The content comes from GET /report-data, the same data as /report. */
export async function downloadPdf(accountId: string, accountName: string, period: string, sortBy: string, sortOrder: string, sections?: string[], model?: string, options?: ExportOptions): Promise<void> {
  const params = new URLSearchParams({ account_name: accountName, period, sort_by: sortBy, sort_order: sortOrder });
  if (sections) sections.forEach(s => params.append("sections", s));
  if (model) params.append("model", model);
  if (options?.linkedIds) params.append("linked_ids", "true");
  if (options?.duplicates) params.append("duplicates", "true");

  const [report, { renderReportPdf }] = await Promise.all([
    fetchReportData(accountId, params),
    import("@/components/reportPdf/SupportReportPdfDocument"),
  ]);
  const blob = await renderReportPdf(report, `${window.location.origin}/fonts/inter`);

  // e.g. "Orange - Support Highlights - 25 September 2026.pdf"
  const filename = `${accountName} - Support Highlights - ${report.generated}.pdf`.replace(/[\\/:*?"<>|]+/g, "-");
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function slackReport(
  accountId: string,
  accountName: string,
  period: string,
  channelId?: string,
  sections?: string[],
  options?: ExportOptions,
): Promise<void> {
  const res = await fetch(`/api/accounts/${accountId}/slack-report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      account_name: accountName,
      period,
      ...(channelId ? { channel_id: channelId } : {}),
      ...(sections ? { sections } : {}),
      show_linked_ids: !!options?.linkedIds,
      show_duplicates: !!options?.duplicates,
    }),
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
  sections?: string[],
  model?: string,
  options?: ExportOptions,
): Promise<void> {
  const res = await fetch(`/api/accounts/${accountId}/email-report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      account_name: accountName,
      period,
      sort_by: sortBy,
      sort_order: sortOrder,
      ...(sections ? { sections } : {}),
      ...(model ? { model } : {}),
      show_linked_ids: !!options?.linkedIds,
      show_duplicates: !!options?.duplicates,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? "Failed to send email");
  }
}
