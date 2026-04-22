"use client";

import { useState, useEffect, useRef } from "react";
import { Share2, Send, X, Check, AlertCircle, ChevronDown, Search } from "lucide-react";

const SECTIONS = [
  { id: "key_metrics", label: "Key Metrics" },
  { id: "ticket_trend", label: "Ticket Trend" },
  { id: "breakdowns", label: "Breakdowns" },
  { id: "account_summary", label: "Account Summary" },
  { id: "open_issues", label: "Open Issues" },
];

const ALL_SECTION_IDS = SECTIONS.map(s => s.id);

interface SlackChannel {
  id: string;
  name: string;
}

interface ShareButtonProps {
  onSlackReport: (channelId?: string, sections?: string[]) => Promise<void>;
  onEmailReport: (email: string, sections?: string[]) => Promise<void>;
  channelName?: string | null;
  channelId?: string | null;
  availableChannels?: SlackChannel[];
}

export default function ShareButton({
  onSlackReport,
  onEmailReport,
  channelName,
  channelId,
  availableChannels = [],
}: ShareButtonProps) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"slack" | "email">("slack");
  const [selectedSections, setSelectedSections] = useState<Set<string>>(new Set(ALL_SECTION_IDS));
  const [pickerOpen, setPickerOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(channelId ?? null);
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setSelectedId(channelId ?? null); }, [channelId]);

  useEffect(() => {
    if (pickerOpen) { setQuery(""); setTimeout(() => searchRef.current?.focus(), 0); }
  }, [pickerOpen]);

  useEffect(() => {
    if (open && mode === "email") { setTimeout(() => emailRef.current?.focus(), 0); }
  }, [open, mode]);

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setPickerOpen(false);
        setStatus(null);
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

  const selectedChannel = availableChannels.find(c => c.id === selectedId);
  const displayName = selectedChannel?.name ?? channelName ?? null;
  const filtered = query.trim()
    ? availableChannels.filter(c => c.name.toLowerCase().includes(query.toLowerCase()))
    : availableChannels;
  const allSelected = ALL_SECTION_IDS.every(id => selectedSections.has(id));

  async function handleSend() {
    if (sending) return;
    const sections = [...selectedSections];
    setSending(true);
    setStatus(null);
    try {
      if (mode === "slack") {
        await onSlackReport(selectedId ?? undefined, sections);
        setStatus({ ok: true, message: displayName ? `Sent to #${displayName}` : "Sent to Slack" });
      } else {
        if (!email.trim()) { setSending(false); return; }
        await onEmailReport(email.trim(), sections);
        setStatus({ ok: true, message: `Sent to ${email.trim()}` });
      }
      setTimeout(() => { setStatus(null); }, 10000);
    } catch (err) {
      setStatus({ ok: false, message: err instanceof Error ? err.message : "Failed to send" });
    } finally {
      setSending(false);
    }
  }

  return (
    <div ref={containerRef} className="relative print:hidden">
      <button
        onClick={() => { setOpen(o => !o); setPickerOpen(false); setStatus(null); }}
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
        className="flex items-center gap-1.5 text-sm rounded px-3 py-1.5 hover:bg-[var(--bg-tertiary)] transition-colors focus:outline-none cursor-pointer"
      >
        <Share2 size={14} />
        Share
      </button>

      {open && (
        <div
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}
          className="absolute right-0 top-[calc(100%+4px)] z-50 rounded-lg p-3 w-72"
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>Share report</span>
            <button
              onClick={() => { setOpen(false); setPickerOpen(false); setStatus(null); }}
              className="p-0.5 rounded hover:bg-[var(--bg-tertiary)] transition-colors"
              style={{ color: "var(--text-muted)" }}
            >
              <X size={11} />
            </button>
          </div>

          {/* Mode tabs */}
          <div
            className="flex rounded mb-3 p-0.5 gap-0.5"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}
          >
            {(["slack", "email"] as const).map(m => (
              <button
                key={m}
                onClick={() => { setMode(m); setStatus(null); setPickerOpen(false); }}
                className="flex-1 text-xs rounded py-1 transition-colors"
                style={{
                  background: mode === m ? "var(--accent)" : "transparent",
                  color: mode === m ? "#fff" : "var(--text-muted)",
                }}
              >
                {m === "slack" ? "Slack" : "Email"}
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
                const checked = selectedSections.has(s.id);
                return (
                  <button
                    key={s.id}
                    onClick={() => toggleSection(s.id)}
                    className="flex items-center gap-1.5 text-xs rounded px-2 py-1 text-left transition-colors hover:bg-[var(--bg-tertiary)]"
                    style={{ color: checked ? "var(--text-primary)" : "var(--text-caption)" }}
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

          {/* Destination + Send */}
          {mode === "slack" ? (
            <div className="flex gap-1.5">
              {availableChannels.length > 1 && (
                <div className="relative flex-1">
                  <button
                    onClick={() => setPickerOpen(o => !o)}
                    style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                    className="w-full flex items-center justify-between text-xs rounded px-2 py-1.5 focus:outline-none hover:border-[var(--border-hover)] transition-colors cursor-pointer"
                  >
                    <span className="truncate">{displayName ? `#${displayName}` : "Select channel"}</span>
                    <ChevronDown size={11} className={`flex-shrink-0 ml-1 transition-transform ${pickerOpen ? "rotate-180" : ""}`} style={{ color: "var(--text-muted)" }} />
                  </button>

                  {pickerOpen && (
                    <div
                      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}
                      className="absolute left-0 right-0 top-[calc(100%+4px)] z-50 rounded-lg overflow-hidden"
                    >
                      <div style={{ borderBottom: "1px solid var(--border)" }} className="flex items-center gap-2 px-2.5 py-2">
                        <Search size={13} style={{ color: "var(--text-muted)" }} className="flex-shrink-0" />
                        <input
                          ref={searchRef}
                          type="text"
                          value={query}
                          onChange={e => setQuery(e.target.value)}
                          placeholder="Search channels..."
                          style={{ background: "transparent", color: "var(--text-primary)" }}
                          className="flex-1 text-xs outline-none placeholder:text-[var(--text-caption)] min-w-0"
                        />
                      </div>
                      <div className="overflow-y-auto max-h-48">
                        {filtered.length === 0 ? (
                          <p className="text-xs px-3 py-2" style={{ color: "var(--text-caption)" }}>No channels match</p>
                        ) : filtered.map(c => {
                          const isSelected = c.id === selectedId;
                          return (
                            <button
                              key={c.id}
                              onClick={() => { setSelectedId(c.id); setPickerOpen(false); }}
                              style={{ color: isSelected ? "#006ddd" : "var(--text-primary)", background: isSelected ? "rgba(0,109,221,0.08)" : "transparent" }}
                              className="w-full flex items-center justify-between px-3 py-1.5 text-xs text-left hover:bg-[var(--bg-tertiary)] transition-colors"
                            >
                              <span className="truncate">#{c.name}</span>
                              {isSelected && <Check size={12} className="flex-shrink-0 ml-2" />}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}
              <button
                onClick={handleSend}
                disabled={sending || selectedSections.size === 0}
                style={{ background: "var(--accent)", color: "#fff" }}
                className="flex items-center justify-center gap-1.5 text-xs rounded px-3 py-1.5 disabled:opacity-50 transition-opacity hover:opacity-90 whitespace-nowrap"
              >
                {sending ? "…" : <Send size={12} />}
              </button>
            </div>
          ) : (
            <div className="flex gap-1.5">
              <input
                ref={emailRef}
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") handleSend(); }}
                placeholder="you@example.com"
                disabled={sending}
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                className="flex-1 text-xs rounded px-2 py-1.5 focus:outline-none placeholder:text-[var(--text-muted)] min-w-0"
              />
              <button
                onClick={handleSend}
                disabled={sending || !email.trim() || selectedSections.size === 0}
                style={{ background: "var(--accent)", color: "#fff" }}
                className="flex items-center justify-center gap-1.5 text-xs rounded px-3 py-1.5 disabled:opacity-50 transition-opacity hover:opacity-90 whitespace-nowrap"
              >
                {sending ? "…" : <Send size={12} />}
              </button>
            </div>
          )}

          {/* Status */}
          {status && (
            status.ok ? (
              <p className="text-xs mt-1.5 flex items-center gap-1" style={{ color: "var(--success, #10b981)" }}>
                <Check size={11} />
                {status.message}
              </p>
            ) : (() => {
              const inviteMatch = status.message.match(/(.*?\.).*?(\/invite\s+\S+)/);
              return inviteMatch ? (
                <div className="mt-2 rounded-md px-2.5 py-2 text-xs" style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)" }}>
                  <div className="flex items-start gap-1.5 mb-2" style={{ color: "#ef4444" }}>
                    <AlertCircle size={11} className="mt-0.5 flex-shrink-0" />
                    <span>{inviteMatch[1]}</span>
                  </div>
                  <div style={{ color: "var(--text-muted)" }} className="mb-1">Run in that channel:</div>
                  <code className="block px-2 py-1 rounded text-xs" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)" }}>
                    {inviteMatch[2]}
                  </code>
                </div>
              ) : (
                <p className="text-xs mt-1.5 flex items-center gap-1" style={{ color: "#ef4444" }}>
                  <AlertCircle size={11} />
                  {status.message}
                </p>
              );
            })()
          )}
        </div>
      )}
    </div>
  );
}
