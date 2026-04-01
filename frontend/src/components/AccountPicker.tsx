"use client";

import { useState, useEffect, useRef } from "react";
import { ChevronDown, Check, Search } from "lucide-react";
import type { Account } from "@/lib/api";

export default function AccountPicker({
  accounts,
  selected,
  onSelect,
}: {
  accounts: Account[];
  selected: Account | null;
  onSelect: (account: Account) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = query.trim()
    ? accounts.filter((a) => a.name.toLowerCase().includes(query.toLowerCase()))
    : accounts;

  useEffect(() => {
    if (open) {
      setQuery("");
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          color: selected ? "var(--text-primary)" : "var(--text-caption)",
        }}
        className="w-full flex items-center justify-between text-sm rounded px-2 py-1.5 focus:outline-none cursor-pointer hover:border-[var(--border-hover)] transition-colors"
      >
        <span className="truncate">{selected?.name ?? "Select account"}</span>
        <ChevronDown size={13} className={`flex-shrink-0 ml-1 transition-transform ${open ? "rotate-180" : ""}`} style={{ color: "var(--text-muted)" }} />
      </button>

      {open && (
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
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search accounts..."
              style={{ background: "transparent", color: "var(--text-primary)" }}
              className="flex-1 text-sm outline-none placeholder:text-[var(--text-caption)] min-w-0"
            />
          </div>

          <div className="overflow-y-auto max-h-56">
            {filtered.length === 0 ? (
              <p style={{ color: "var(--text-caption)" }} className="text-xs px-3 py-2">No accounts match</p>
            ) : (
              filtered.map((a) => {
                const isSelected = a.id === selected?.id;
                return (
                  <button
                    key={a.id}
                    onClick={() => { onSelect(a); setOpen(false); }}
                    style={{
                      color: isSelected ? "#006ddd" : "var(--text-primary)",
                      background: isSelected ? "rgba(0,109,221,0.08)" : "transparent",
                    }}
                    className="w-full flex items-center justify-between px-3 py-1.5 text-sm text-left hover:bg-[var(--bg-tertiary)] transition-colors"
                  >
                    <span className="truncate">{a.name}</span>
                    {isSelected && <Check size={13} className="flex-shrink-0 ml-2" />}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
