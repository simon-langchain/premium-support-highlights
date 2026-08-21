"use client";

import { RefreshCw } from "lucide-react";

interface RefreshButtonProps {
  onRefresh: () => void;
  dataUpdatedAt: Date | null;
  label?: string;
}

/** Shared "Refresh Data" button + last-updated timestamp, used by both the
 * customer-account sidebar and the internal team-dashboard sidebar. */
export default function RefreshButton({ onRefresh, dataUpdatedAt, label = "Refresh Data" }: RefreshButtonProps) {
  return (
    <div className="px-4 py-3">
      <button
        onClick={onRefresh}
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          color: "var(--text-primary)",
        }}
        className="flex items-center gap-2 text-sm rounded px-3 py-1.5 transition-colors hover:bg-[var(--bg-tertiary)]"
      >
        <RefreshCw size={14} />
        {label}
      </button>
      {dataUpdatedAt && (
        <p className="text-xs mt-1.5 pl-0.5" style={{ color: "var(--text-muted)" }}>
          Updated {dataUpdatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZoneName: "short" })}
        </p>
      )}
    </div>
  );
}
