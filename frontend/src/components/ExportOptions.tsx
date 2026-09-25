"use client";

import { Check, Copy, Link2 } from "lucide-react";

/** Opt-in extras for exports, shares and scheduled reports — all off by default. */
export interface ExportOptions {
  /** List linked Linear/GitHub IDs (as plain text) */
  linkedIds: boolean;
  /** Group possible-duplicate tickets together */
  duplicates: boolean;
}

export const DEFAULT_EXPORT_OPTIONS: ExportOptions = { linkedIds: false, duplicates: false };

const OPTIONS: { key: keyof ExportOptions; label: string; icon: typeof Link2 }[] = [
  { key: "linkedIds", label: "Linked ticket IDs", icon: Link2 },
  { key: "duplicates", label: "Possible duplicates", icon: Copy },
];

/** The "Options" heading + checkboxes, shared by Download, Share and Schedule (below "Sections"). */
export default function ExportOptionsPicker({
  value,
  onChange,
}: {
  value: ExportOptions;
  onChange: (value: ExportOptions) => void;
}) {
  return (
    <div>
      <span className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Options</span>
      <div className="flex flex-col gap-0.5">
        {OPTIONS.map(({ key, label, icon: Icon }) => {
          const checked = value[key];
          return (
            <button
              key={key}
              type="button"
              onClick={() => onChange({ ...value, [key]: !checked })}
              aria-pressed={checked}
              className="flex items-center gap-1.5 text-xs rounded px-2 py-1 text-left transition-colors hover:bg-[var(--bg-tertiary)]"
              style={{ color: checked ? "var(--text-primary)" : "var(--text-muted)" }}
            >
              <div
                className="w-3 h-3 rounded flex items-center justify-center flex-shrink-0"
                style={{
                  background: checked ? "var(--accent)" : "transparent",
                  border: `1px solid ${checked ? "var(--accent)" : "var(--text-caption)"}`,
                }}
              >
                {checked && <Check size={8} strokeWidth={3} color="#fff" />}
              </div>
              <Icon size={11} className="flex-shrink-0" />
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
