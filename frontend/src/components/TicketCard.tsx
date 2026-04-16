import type { Issue, ExternalIssue } from "@/lib/api";

// SVG paths sourced from simpleicons.org (24×24 viewBox)
const SOURCE_ICONS: Record<string, string> = {
  linear:
    "M2.886 4.18A11.982 11.982 0 0 1 11.99 0C18.624 0 24 5.376 24 12.009c0 3.64-1.62 6.903-4.18 9.105L2.887 4.18ZM1.817 5.626l16.556 16.556c-.524.33-1.075.62-1.65.866L.951 7.277c.247-.575.537-1.126.866-1.65ZM.322 9.163l14.515 14.515c-.71.172-1.443.282-2.195.322L0 11.358a12 12 0 0 1 .322-2.195Zm-.17 4.862 9.823 9.824a12.02 12.02 0 0 1-9.824-9.824Z",
  github:
    "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12",
};

function SourceIcon({ source }: { source: string }) {
  const path = SOURCE_ICONS[source.toLowerCase()];
  if (!path) return null;
  return (
    <svg viewBox="0 0 24 24" width={13} height={13} fill="currentColor" aria-label={source}>
      <path d={path} />
    </svg>
  );
}

interface TicketCardProps {
  issue: Issue;
  /** null = still loading, string = loaded (may be empty) */
  ticketSummary?: string | null;
}

interface BadgeVars {
  bg: string;
  text: string;
  border: string;
}

const STATE_BADGE: Record<string, BadgeVars> = {
  new:                 { bg: "var(--badge-new-bg)",     text: "var(--badge-new-text)",     border: "var(--badge-new-border)" },
  waiting_on_you:      { bg: "var(--badge-lc-bg)",      text: "var(--badge-lc-text)",      border: "var(--badge-lc-border)" },
  on_hold:             { bg: "var(--badge-hold-bg)",    text: "var(--badge-hold-text)",    border: "var(--badge-hold-border)" },
  waiting_on_customer: { bg: "var(--badge-sky-bg)",     text: "var(--badge-sky-text)",     border: "var(--badge-sky-border)" },
  closed:              { bg: "var(--badge-emerald-bg)", text: "var(--badge-emerald-text)", border: "var(--badge-emerald-border)" },
  resolved:            { bg: "var(--badge-teal-bg)",    text: "var(--badge-teal-text)",    border: "var(--badge-teal-border)" },
};

const STATE_LABELS: Record<string, string> = {
  new: "New",
  waiting_on_you: "Waiting on LangChain",
  on_hold: "On Hold",
  waiting_on_customer: "Waiting on Customer",
  closed: "Closed",
  resolved: "Resolved",
};

const PRIORITY_LABELS: Record<string, string> = {
  urgent: "Sev 1",
  high: "Sev 2",
  medium: "Sev 3",
  low: "Sev 4",
};

const PRIORITY_BADGE: Record<string, BadgeVars> = {
  urgent: { bg: "var(--badge-red-bg)",    text: "var(--badge-red-text)",    border: "var(--badge-red-border)" },
  high:   { bg: "var(--badge-orange-bg)", text: "var(--badge-orange-text)", border: "var(--badge-orange-border)" },
  medium: { bg: "var(--badge-yellow-bg)", text: "var(--badge-yellow-text)", border: "var(--badge-yellow-border)" },
  low:    { bg: "var(--badge-slate-bg)",  text: "var(--badge-slate-text)",  border: "var(--badge-slate-border)" },
};

function formatDate(isoStr: string): string {
  try {
    return new Date(isoStr).toLocaleDateString("en-GB", {
      day: "numeric", month: "short", year: "numeric",
    });
  } catch {
    return isoStr;
  }
}

function Badge({ vars, label }: { vars: BadgeVars; label: string }) {
  return (
    <span
      style={{
        background: vars.bg,
        color: vars.text,
        border: `1px solid ${vars.border}`,
      }}
      className="text-xs px-2 py-0.5 rounded font-medium"
    >
      {label}
    </span>
  );
}

export default function TicketCard({ issue, ticketSummary }: TicketCardProps) {
  const stateBadge = STATE_BADGE[issue.state] ?? STATE_BADGE.waiting_on_customer;
  const stateLabel = STATE_LABELS[issue.state] ?? issue.state;
  const priorityBadge = issue.priority ? PRIORITY_BADGE[issue.priority] : null;

  return (
    <div
      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
      className="hover:border-[#006ddd] rounded-lg px-4 py-3 transition-colors"
    >
      <div className="flex items-start gap-2 mb-2">
        <a
          href={`https://app.usepylon.com/issues?issueNumber=${issue.number}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[#006ddd] font-mono text-xs font-semibold hover:underline whitespace-nowrap mt-0.5"
        >
          #{issue.number}
        </a>
        <p style={{ color: "var(--text-primary)" }} className="text-sm leading-snug flex-1">
          {issue.title}
        </p>
      </div>

      {ticketSummary === null ? (
        <p style={{ color: "var(--text-caption)" }} className="text-xs mt-1 mb-2 italic">
          Generating summary...
        </p>
      ) : ticketSummary ? (
        <p style={{ color: "var(--text-muted)" }} className="text-xs leading-relaxed mt-1 mb-2">
          {ticketSummary}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-1.5">
        <Badge vars={stateBadge} label={stateLabel} />
        {priorityBadge && issue.priority !== "none" && (
          <Badge vars={priorityBadge} label={PRIORITY_LABELS[issue.priority] ?? issue.priority} />
        )}
        {issue.disposition && (
          <span
            style={{ background: "var(--bg-tertiary)", color: "var(--text-muted)", border: "1px solid var(--border-hover)" }}
            className="text-xs px-2 py-0.5 rounded"
          >
            {issue.disposition}
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 mt-2">
        {issue.created_at && (
          <span style={{ color: "var(--text-caption)" }} className="text-xs">
            Created {formatDate(issue.created_at)}
          </span>
        )}
        {issue.tags && issue.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {issue.tags.map((tag) => (
              <span
                key={tag}
                style={{ background: "var(--bg-tertiary)", color: "var(--text-muted)" }}
                className="text-xs px-1.5 py-0.5 rounded"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
        {issue.external_issues && issue.external_issues.length > 0 && (
          <div className="flex gap-1">
            {issue.external_issues.map((ei) => (
              <a
                key={ei.external_id || ei.link}
                href={ei.link}
                target="_blank"
                rel="noopener noreferrer"
                title={ei.external_id ? `${ei.source} · ${ei.external_id}` : ei.source}
                style={{ color: "var(--text-muted)", border: "1px solid var(--border-hover)" }}
                className="flex items-center justify-center w-6 h-6 rounded hover:text-[#006ddd] hover:border-[#006ddd] transition-colors"
              >
                <SourceIcon source={ei.source} />
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
