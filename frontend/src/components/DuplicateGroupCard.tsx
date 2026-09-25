"use client";

import { useState } from "react";
import { Copy, Link2, Sparkles, UserCheck, Loader2, X } from "lucide-react";
import type { DuplicateGroup } from "@/lib/api";

const REASON_ICONS = {
  link: Link2,
  ai: Sparkles,
  manual: UserCheck,
} as const;

/** Outlined container that keeps a possible-duplicate group's ticket cards together. */
export default function DuplicateGroupCard({
  group,
  visibleCount,
  onDismiss,
  children,
}: {
  group: DuplicateGroup;
  /** Tickets in this group that pass the current filters (the rest are hidden) */
  visibleCount: number;
  onDismiss: () => Promise<void>;
  children: React.ReactNode;
}) {
  const [dismissing, setDismissing] = useState(false);
  const hidden = group.tickets.length - visibleCount;

  return (
    <div
      id={`dupe-${group.id}`}
      className="rounded-xl p-2.5 flex flex-col gap-2"
      style={{ border: "1px dashed rgba(0,109,221,0.55)", background: "rgba(0,109,221,0.04)" }}
    >
      <div className="flex items-start justify-between gap-3 px-1.5 pt-0.5">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
            <Copy size={12} style={{ color: "#006ddd" }} />
            Possible duplicates · {group.tickets.length} tickets
            {hidden > 0 && (
              <span className="font-normal" style={{ color: "var(--text-caption)" }}>
                ({hidden} hidden by filters)
              </span>
            )}
          </div>
          <ul className="mt-1 flex flex-col gap-0.5">
            {group.reasons.map((r, i) => {
              const Icon = REASON_ICONS[r.kind];
              // In a 3+ ticket group, name the tickets when this reason covers only some of them
              const partial = r.tickets && r.tickets.length < group.tickets.length;
              return (
                <li key={i} className="flex items-start gap-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                  <Icon size={11} className="flex-shrink-0 mt-0.5" />
                  <span>
                    {partial && (
                      <span className="font-mono font-medium" style={{ color: "var(--text-primary)" }}>
                        {r.tickets!.map((n) => `#${n}`).join(" + ")}
                        <span style={{ color: "var(--text-caption)" }}> · </span>
                      </span>
                    )}
                    {r.text}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
        <button
          onClick={async () => {
            setDismissing(true);
            try { await onDismiss(); } catch { /* shown by caller */ } finally { setDismissing(false); }
          }}
          disabled={dismissing}
          title="Hide this group — you can restore it from the dismissed list"
          className="flex items-center gap-1 text-xs px-2 py-1 rounded flex-shrink-0 hover:bg-[var(--bg-tertiary)] transition-colors disabled:opacity-50"
          style={{ color: "var(--text-muted)", border: "1px solid var(--border)" }}
        >
          {dismissing ? <Loader2 size={11} className="animate-spin" /> : <X size={11} />}
          Not duplicates
        </button>
      </div>
      {children}
    </div>
  );
}
