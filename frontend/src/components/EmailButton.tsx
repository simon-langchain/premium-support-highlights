"use client";

import { useState, useEffect, useRef } from "react";
import { Mail, Send, X } from "lucide-react";

interface EmailButtonProps {
  onEmailReport: (email: string) => Promise<void>;
}

export default function EmailButton({ onEmailReport }: EmailButtonProps) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setStatus(null);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  useEffect(() => {
    if (open && inputRef.current) {
      inputRef.current.focus();
    }
  }, [open]);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!email.trim()) return;
    setSending(true);
    setStatus(null);
    try {
      await onEmailReport(email.trim());
      setStatus({ ok: true, message: `Sent to ${email.trim()}` });
      setEmail("");
      setTimeout(() => {
        setOpen(false);
        setStatus(null);
      }, 2000);
    } catch (err) {
      setStatus({ ok: false, message: err instanceof Error ? err.message : "Failed to send" });
    } finally {
      setSending(false);
    }
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
        <Mail size={14} />
        Email
      </button>

      {open && (
        <div
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          }}
          className="absolute right-0 top-[calc(100%+4px)] z-50 rounded-lg p-3 w-64"
        >
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
              Send report to
            </span>
            <button
              onClick={() => { setOpen(false); setStatus(null); setEmail(""); }}
              className="p-0.5 rounded hover:bg-[var(--bg-tertiary)] transition-colors"
              style={{ color: "var(--text-muted)" }}
            >
              <X size={11} />
            </button>
          </div>
          <form onSubmit={handleSubmit} className="flex gap-1.5">
            <input
              ref={inputRef}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              disabled={sending}
              style={{
                background: "var(--bg-primary)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
              }}
              className="flex-1 text-xs rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-[var(--accent)] placeholder:text-[var(--text-muted)] min-w-0"
            />
            <button
              type="submit"
              disabled={sending || !email.trim()}
              style={{ background: "var(--accent)", color: "#fff" }}
              className="flex items-center justify-center rounded px-2 py-1.5 disabled:opacity-50 transition-opacity hover:opacity-90"
            >
              <Send size={12} />
            </button>
          </form>
          {status && (
            <p
              className="text-xs mt-1.5"
              style={{ color: status.ok ? "var(--success, #10b981)" : "var(--error, #ef4444)" }}
            >
              {status.message}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
