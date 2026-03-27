"use client";

import { RotateCcw, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";

interface SummaryPanelProps {
  summary: string | null;
  generatedAt: Date | null;
  loading: boolean;
  error: string | null;
  onRegenerate: () => void;
}

export default function SummaryPanel({ summary, generatedAt, loading, error, onRegenerate }: SummaryPanelProps) {
  return (
    <div
      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
      className="rounded-lg px-4 py-4"
    >
      <div className="flex items-center justify-between mb-3">
        <h3 style={{ color: "var(--text-muted)" }} className="text-xs uppercase tracking-wider font-medium">
          AI Account Summary
        </h3>
        <button
          onClick={onRegenerate}
          disabled={loading}
          className="flex items-center gap-1.5 text-sm bg-[#006ddd] hover:bg-[#0058b8] disabled:opacity-50 disabled:cursor-not-allowed text-white rounded px-3 py-1.5 transition-colors font-medium"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
          {loading ? "Generating..." : "Regenerate Summaries"}
        </button>
      </div>

      {error && (
        <p className="text-red-600 dark:text-red-400 text-sm bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/40 rounded px-3 py-2">
          {error}
        </p>
      )}

      {loading && !summary && (
        <p style={{ color: "var(--text-caption)" }} className="text-sm">
          Analysing tickets and generating summary...
        </p>
      )}

      {summary && !error && (
        <div className="border-l-2 border-[#006ddd] pl-4 mt-2">
          <div style={{ color: "var(--text-primary)" }} className="text-sm leading-relaxed prose prose-sm prose-invert max-w-none
            [&_p]:mb-2 [&_p:last-child]:mb-0
            [&_ul]:mb-2 [&_ul]:pl-4 [&_ul:last-child]:mb-0
            [&_ol]:mb-2 [&_ol]:pl-4 [&_ol:last-child]:mb-0
            [&_li]:mb-0.5
            [&_strong]:text-[var(--text-primary)] [&_strong]:font-semibold
            [&_h1]:text-base [&_h1]:font-semibold [&_h1]:mb-1
            [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mb-1
            [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mb-1">
            <ReactMarkdown>{summary}</ReactMarkdown>
          </div>
          {generatedAt && (
            <p style={{ color: "var(--text-caption)" }} className="text-xs mt-2">
              Generated {generatedAt.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" })}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
