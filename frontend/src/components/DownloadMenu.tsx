"use client";

import { useState, useEffect, useRef } from "react";
import { Download, ChevronDown, FileText, Sheet } from "lucide-react";

interface DownloadMenuProps {
  onDownloadPdf: () => void;
  onDownloadCsv: () => void;
}

export default function DownloadMenu({ onDownloadPdf, onDownloadCsv }: DownloadMenuProps) {
  const [open, setOpen] = useState(false);
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

  function handleOption(fn: () => void) {
    setOpen(false);
    fn();
  }

  return (
    <div ref={containerRef} className="relative print:hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          color: "var(--text-primary)",
        }}
        className="flex items-center gap-1.5 text-sm rounded px-3 py-1.5 hover:bg-[var(--bg-tertiary)] transition-colors focus:outline-none cursor-pointer"
      >
        <Download size={14} />
        Download
        <ChevronDown size={12} className={`transition-transform ${open ? "rotate-180" : ""}`} style={{ color: "var(--text-muted)" }} />
      </button>

      {open && (
        <div
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          }}
          className="absolute right-0 top-[calc(100%+4px)] z-50 rounded-lg overflow-hidden w-40"
        >
          <button
            onClick={() => handleOption(onDownloadPdf)}
            style={{ color: "var(--text-primary)" }}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <FileText size={13} style={{ color: "var(--text-muted)" }} />
            PDF
          </button>
          <button
            onClick={() => handleOption(onDownloadCsv)}
            style={{ color: "var(--text-primary)" }}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <Sheet size={13} style={{ color: "var(--text-muted)" }} />
            CSV
          </button>
        </div>
      )}
    </div>
  );
}
