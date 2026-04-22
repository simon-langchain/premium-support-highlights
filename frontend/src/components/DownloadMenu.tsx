"use client";

import { useState, useEffect, useRef } from "react";
import { Download, FileText, Sheet, Check } from "lucide-react";

const SECTIONS = [
  { id: "key_metrics", label: "Key Metrics" },
  { id: "ticket_trend", label: "Ticket Trend" },
  { id: "breakdowns", label: "Breakdowns" },
  { id: "account_summary", label: "Account Summary" },
  { id: "open_issues", label: "Open Issues" },
];

const ALL_SECTION_IDS = SECTIONS.map(s => s.id);

interface DownloadMenuProps {
  onDownloadPdf: (sections: string[]) => void;
  onDownloadCsv: (sections: string[]) => void;
}

export default function DownloadMenu({ onDownloadPdf, onDownloadCsv }: DownloadMenuProps) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"pdf" | "csv">("pdf");
  const [selectedSections, setSelectedSections] = useState<Set<string>>(new Set(ALL_SECTION_IDS));
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  function toggleSection(id: string) {
    setSelectedSections(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  const allSelected = ALL_SECTION_IDS.every(id => selectedSections.has(id));

  function handleDownload() {
    const sections = [...selectedSections];
    setOpen(false);
    if (mode === "pdf") onDownloadPdf(sections);
    else onDownloadCsv(sections);
  }

  return (
    <div ref={containerRef} className="relative print:hidden">
      <button
        onClick={() => setOpen(o => !o)}
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
        className="flex items-center gap-1.5 text-sm rounded px-3 py-1.5 hover:bg-[var(--bg-tertiary)] transition-colors focus:outline-none cursor-pointer"
      >
        <Download size={14} />
        Download
      </button>

      {open && (
        <div
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}
          className="absolute right-0 top-[calc(100%+4px)] z-50 rounded-lg p-3 w-72"
        >
          {/* Header */}
          <div className="mb-3">
            <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>Download report</span>
          </div>

          {/* Format tabs */}
          <div
            className="flex rounded mb-3 p-0.5 gap-0.5"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}
          >
            {(["pdf", "csv"] as const).map(m => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className="flex-1 flex items-center justify-center gap-1.5 text-xs rounded py-1 transition-colors"
                style={{
                  background: mode === m ? "var(--accent)" : "transparent",
                  color: mode === m ? "#fff" : "var(--text-muted)",
                }}
              >
                {m === "pdf" ? <FileText size={11} /> : <Sheet size={11} />}
                {m.toUpperCase()}
              </button>
            ))}
          </div>

          {/* Section checkboxes */}
          <div className="mb-3">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>Sections</span>
              <button
                onClick={() => setSelectedSections(allSelected ? new Set() : new Set(ALL_SECTION_IDS))}
                className="text-xs hover:underline"
                style={{ color: "var(--accent)" }}
              >
                {allSelected ? "Deselect all" : "Select all"}
              </button>
            </div>
            <div className="grid grid-cols-2 gap-0.5">
              {SECTIONS.map(s => {
                const unavailable = mode === "csv" && s.id === "account_summary";
                const checked = selectedSections.has(s.id) && !unavailable;
                return (
                  <button
                    key={s.id}
                    onClick={() => { if (!unavailable) toggleSection(s.id); }}
                    disabled={unavailable}
                    title={unavailable ? "Not available in CSV" : undefined}
                    className="flex items-center gap-1.5 text-xs rounded px-2 py-1 text-left transition-colors hover:bg-[var(--bg-tertiary)] disabled:cursor-not-allowed disabled:hover:bg-transparent"
                    style={{ color: unavailable ? "var(--text-caption)" : checked ? "var(--text-primary)" : "var(--text-caption)", opacity: unavailable ? 0.4 : 1 }}
                  >
                    <div
                      className="w-3 h-3 rounded flex items-center justify-center flex-shrink-0"
                      style={{
                        background: checked ? "var(--accent)" : "transparent",
                        border: `1px solid ${checked ? "var(--accent)" : "var(--border)"}`,
                      }}
                    >
                      {checked && <Check size={8} strokeWidth={3} color="#fff" />}
                    </div>
                    {s.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Download button */}
          <button
            onClick={handleDownload}
            disabled={selectedSections.size === 0}
            style={{ background: "var(--accent)", color: "#fff" }}
            className="w-full flex items-center justify-center gap-1.5 text-xs rounded px-3 py-1.5 disabled:opacity-50 transition-opacity hover:opacity-90"
          >
            <Download size={12} />
            Download {mode.toUpperCase()}
          </button>
        </div>
      )}
    </div>
  );
}
