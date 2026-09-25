"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import type { DuplicateDismissal } from "@/lib/api";

/** "N dismissed" link + popover listing dismissed duplicate groups, each restorable. */
export default function DismissedDuplicates({
  dismissed,
  onRestore,
}: {
  dismissed: DuplicateDismissal[];
  onRestore: (dismissalId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  if (dismissed.length === 0) return null;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="text-xs hover:underline"
        style={{ color: "var(--text-muted)" }}
      >
        {dismissed.length} dismissed
      </button>
      {open && (
        <div
          className="absolute left-0 top-[calc(100%+4px)] z-40 w-80 rounded-lg p-2"
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}
        >
          <p className="text-xs px-1.5 pb-1.5" style={{ color: "var(--text-muted)" }}>Dismissed duplicate groups</p>
          <ul className="flex flex-col gap-1">
            {dismissed.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-2 rounded px-1.5 py-1 hover:bg-[var(--bg-tertiary)]">
                <div className="min-w-0">
                  <div className="text-xs font-mono" style={{ color: "var(--text-primary)" }}>
                    {d.tickets.map((n) => `#${n}`).join(", ")}
                  </div>
                  <div className="text-xs truncate" style={{ color: "var(--text-caption)" }}>
                    {d.by} · {new Date(d.at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
                  </div>
                </div>
                <button
                  onClick={async () => {
                    setRestoring(d.id);
                    try { await onRestore(d.id); } catch { /* shown by caller */ } finally { setRestoring(null); }
                  }}
                  disabled={restoring === d.id}
                  className="flex items-center gap-1 text-xs px-2 py-1 rounded flex-shrink-0 disabled:opacity-50"
                  style={{ color: "#006ddd", border: "1px solid rgba(0,109,221,0.4)" }}
                >
                  {restoring === d.id ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />}
                  Restore
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
