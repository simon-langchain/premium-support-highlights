"use client";

import { useState, useEffect, useRef } from "react";
import { RefreshCw, ChevronLeft, ChevronRight, Sun, Moon, Search, Check, ChevronDown } from "lucide-react";
import { useTheme } from "next-themes";
import type { Account } from "@/lib/api";

interface SidebarProps {
  accounts: Account[];
  selected: Account | null;
  onSelect: (account: Account) => void;
  onRefresh: () => void;
  selectedModel: string;
  onModelChange: (model: string) => void;
  period: string;
  onPeriodChange: (period: string) => void;
}

const MODELS = [
  { value: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
  { value: "claude-opus-4-6", label: "Claude Opus 4.6" },
  { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
];

const PERIODS = [
  { value: "7d", label: "7 Days" },
  { value: "1m", label: "1 Month" },
  { value: "3m", label: "3 Months" },
  { value: "6m", label: "6 Months" },
  { value: "1y", label: "1 Year" },
];

const Logo = () => (
  <svg
    width="22"
    height="22"
    viewBox="0 0 128 128"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    className="flex-shrink-0"
  >
    <path d="M40.1024 85.0722C47.6207 77.5537 51.8469 67.3453 51.8469 56.7136C51.8469 46.0818 47.617 35.8734 40.1024 28.355L11.7446 0C4.22995 7.5185 0 17.7269 0 28.3586C0 38.9903 4.22995 49.1987 11.7446 56.7172L40.0987 85.0722H40.1024Z" fill="white" />
    <path d="M99.4385 87.698C91.9239 80.1832 81.7121 75.9531 71.0844 75.9531C60.4566 75.9531 50.2448 80.1832 42.7266 87.698L71.0844 116.057C78.599 123.571 88.8107 127.802 99.4421 127.802C110.074 127.802 120.282 123.571 127.8 116.057L99.4421 87.698H99.4385Z" fill="white" />
    <path d="M11.8146 115.987C19.3329 123.502 29.541 127.732 40.1724 127.732V87.6289H0.0664062C0.0700559 98.2606 4.29635 108.469 11.8146 115.987Z" fill="white" />
    <path d="M110.387 45.7684C102.869 38.2535 92.6608 34.0198 82.0258 34.0234C71.3943 34.0234 61.1863 38.2535 53.668 45.772L82.0258 74.1306L110.387 45.7684Z" fill="white" />
  </svg>
);

function OptionPicker({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const selected = options.find((o) => o.value === value);

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
          color: "var(--text-primary)",
        }}
        className="w-full flex items-center justify-between text-sm rounded px-2 py-1.5 focus:outline-none cursor-pointer hover:border-[var(--border-hover)] transition-colors"
      >
        <span className="truncate">{selected?.label ?? value}</span>
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
          {options.map((o) => {
            const isSelected = o.value === value;
            return (
              <button
                key={o.value}
                onClick={() => { onChange(o.value); setOpen(false); }}
                style={{
                  color: isSelected ? "#006ddd" : "var(--text-primary)",
                  background: isSelected ? "rgba(0,109,221,0.08)" : "transparent",
                }}
                className="w-full flex items-center justify-between px-3 py-1.5 text-sm text-left hover:bg-[var(--bg-tertiary)] transition-colors"
              >
                <span>{o.label}</span>
                {isSelected && <Check size={13} className="flex-shrink-0 ml-2" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AccountPicker({
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
          {/* Search */}
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

          {/* List */}
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

export default function Sidebar({
  accounts,
  selected,
  onSelect,
  onRefresh,
  selectedModel,
  onModelChange,
  period,
  onPeriodChange,
}: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [mounted, setMounted] = useState(false);
  const { resolvedTheme, setTheme } = useTheme();

  useEffect(() => setMounted(true), []);

  const ThemeToggle = ({ size = 14 }: { size?: number }) =>
    mounted ? (
      <button
        onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
        title="Toggle theme"
        style={{ color: "var(--text-muted)" }}
        className="p-1 rounded hover:bg-[var(--bg-tertiary)] transition-colors"
      >
        {resolvedTheme === "dark" ? <Sun size={size} /> : <Moon size={size} />}
      </button>
    ) : null;

  return (
    <aside
      style={{
        width: collapsed ? 48 : 240,
        minWidth: collapsed ? 48 : 240,
        background: "var(--bg-base)",
        borderRight: "1px solid var(--border)",
      }}
      className="relative flex flex-col h-screen overflow-hidden transition-[width,min-width] duration-200 ease-in-out print:hidden"
    >
      {/* Toggle button */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        style={{ color: "var(--text-muted)" }}
        className="absolute top-4 right-2 z-10 p-1 rounded hover:bg-[var(--bg-tertiary)] transition-colors"
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
      </button>

      {/* Collapsed: logo at top, theme toggle pinned to bottom */}
      {collapsed && (
        <div className="flex flex-col items-center flex-1 pt-[18px] pb-4">
          <Logo />
          <div className="flex-1" />
          <ThemeToggle />
        </div>
      )}

      {/* Expanded content */}
      {!collapsed && (
        <>
          {/* Logo + title */}
          <div className="px-4 pt-5 pb-4 pr-8">
            <div className="flex items-center gap-2 mb-1">
              <Logo />
              <span style={{ color: "var(--text-primary)" }} className="font-semibold text-sm leading-tight whitespace-nowrap">
                Support Highlights
              </span>
            </div>
            <p style={{ color: "var(--text-muted)" }} className="text-xs ml-7">Premium accounts</p>
          </div>

          <div className="px-4 pb-3">
            <label style={{ color: "var(--text-muted)" }} className="block text-xs uppercase tracking-wider mb-1">
              Account
            </label>
            <AccountPicker accounts={accounts} selected={selected} onSelect={onSelect} />
          </div>

          <div className="px-4 pb-3">
            <label style={{ color: "var(--text-muted)" }} className="block text-xs uppercase tracking-wider mb-1">
              Time Period
            </label>
            <OptionPicker options={PERIODS} value={period} onChange={onPeriodChange} />
          </div>

          <div style={{ borderColor: "var(--border)" }} className="mx-4 border-t my-1" />

          <div className="px-4 py-3 flex flex-col gap-3">
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
              Refresh Data
            </button>

            <div>
              <label style={{ color: "var(--text-muted)" }} className="block text-xs uppercase tracking-wider mb-1">
                Summary Model
              </label>
              <OptionPicker options={MODELS} value={selectedModel} onChange={onModelChange} />
            </div>
          </div>

          <div className="flex-1" />

          <div className="px-4 py-4 flex items-center justify-between">
            <p style={{ color: "var(--text-caption)" }} className="text-xs">Powered by Pylon + Claude</p>
            <ThemeToggle />
          </div>
        </>
      )}
    </aside>
  );
}
