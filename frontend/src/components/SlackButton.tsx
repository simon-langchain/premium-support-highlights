"use client";

import { useState, useEffect, useRef } from "react";
import { Slack, Send, X, Check, AlertCircle, ChevronDown, Search } from "lucide-react";

interface SlackChannel {
  id: string;
  name: string;
}

interface SlackButtonProps {
  onSlackReport: (channelId?: string) => Promise<void>;
  channelName?: string | null;
  channelId?: string | null;
  availableChannels?: SlackChannel[];
}

export default function SlackButton({ onSlackReport, channelName, channelId, availableChannels = [] }: SlackButtonProps) {
  const [open, setOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(channelId ?? null);
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setSelectedId(channelId ?? null); }, [channelId]);

  useEffect(() => {
    if (pickerOpen) { setQuery(""); setTimeout(() => searchRef.current?.focus(), 0); }
  }, [pickerOpen]);

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

  const selectedChannel = availableChannels.find(c => c.id === selectedId);
  const displayName = selectedChannel?.name ?? channelName ?? null;
  const filtered = query.trim()
    ? availableChannels.filter(c => c.name.toLowerCase().includes(query.toLowerCase()))
    : availableChannels;

  async function handleSend() {
    if (sending) return;
    setSending(true);
    setStatus(null);
    try {
      await onSlackReport(selectedId ?? undefined);
      setStatus({ ok: true, message: displayName ? `Sent to #${displayName}` : "Sent to Slack" });
      setTimeout(() => { setOpen(false); setPickerOpen(false); setStatus(null); }, 2000);
    } catch (err) {
      setStatus({ ok: false, message: err instanceof Error ? err.message : "Failed to send" });
    } finally {
      setSending(false);
    }
  }

  return (
    <div ref={containerRef} className="relative print:hidden">
      <button
        onClick={() => { setOpen((o) => !o); setPickerOpen(false); setStatus(null); }}
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          color: "var(--text-primary)",
        }}
        className="flex items-center gap-1.5 text-sm rounded px-3 py-1.5 hover:bg-[var(--bg-tertiary)] transition-colors focus:outline-none cursor-pointer"
      >
        <Slack size={14} />
        Slack
      </button>

      {open && (
        <div
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          }}
          className="absolute right-0 top-[calc(100%+4px)] z-50 rounded-lg p-3 w-72"
        >
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
              Post to Slack channel
            </span>
            <button
              onClick={() => { setOpen(false); setPickerOpen(false); setStatus(null); }}
              className="p-0.5 rounded hover:bg-[var(--bg-tertiary)] transition-colors"
              style={{ color: "var(--text-muted)" }}
            >
              <X size={11} />
            </button>
          </div>

          <div className="flex gap-1.5">
            {/* Channel picker — only shown when multiple channels available */}
            {availableChannels.length > 1 && (
              <div className="relative flex-1">
                <button
                  onClick={() => setPickerOpen(o => !o)}
                  style={{
                    background: "var(--bg-primary)",
                    border: "1px solid var(--border)",
                    color: "var(--text-primary)",
                  }}
                  className="w-full flex items-center justify-between text-xs rounded px-2 py-1.5 focus:outline-none hover:border-[var(--border-hover)] transition-colors cursor-pointer"
                >
                  <span className="truncate">{displayName ? `#${displayName}` : "Select channel"}</span>
                  <ChevronDown size={11} className={`flex-shrink-0 ml-1 transition-transform ${pickerOpen ? "rotate-180" : ""}`} style={{ color: "var(--text-muted)" }} />
                </button>

                {pickerOpen && (
                  <div
                    style={{
                      background: "var(--bg-secondary)",
                      border: "1px solid var(--border)",
                      boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
                    }}
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
                            style={{
                              color: isSelected ? "#006ddd" : "var(--text-primary)",
                              background: isSelected ? "rgba(0,109,221,0.08)" : "transparent",
                            }}
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
              disabled={sending}
              style={{ background: "var(--accent)", color: "#fff" }}
              className="flex items-center justify-center gap-1.5 text-xs rounded px-2 py-1.5 disabled:opacity-50 transition-opacity hover:opacity-90"
            >
              {sending ? "Sending…" : <Send size={12} />}
            </button>
          </div>

          {status && (
            <p
              className="text-xs mt-1.5 flex items-center gap-1"
              style={{ color: status.ok ? "var(--success, #10b981)" : "var(--error, #ef4444)" }}
            >
              {status.ok ? <Check size={11} /> : <AlertCircle size={11} />}
              {status.message}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
