"use client";

import { useState, useEffect, useRef } from "react";
import { RefreshCw, ChevronLeft, ChevronRight, Sun, Moon, LogOut, Settings, LayoutDashboard } from "lucide-react";
import { useTheme } from "next-themes";
import { useRouter } from "next/navigation";
import type { Account } from "@/lib/api";
import OptionPicker from "@/components/OptionPicker";
import AccountPicker from "@/components/AccountPicker";

interface SidebarProps {
  accounts: Account[];
  selected: Account | null;
  onSelect: (account: Account) => void;
  onRefresh: () => void;
  selectedModel: string;
  onModelChange: (model: string) => void;
  period: string;
  onPeriodChange: (period: string) => void;
  onSetup: () => void;
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


export default function Sidebar({
  accounts,
  selected,
  onSelect,
  onRefresh,
  selectedModel,
  onModelChange,
  period,
  onPeriodChange,
  onSetup,
}: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [mounted, setMounted] = useState(false);
  const { resolvedTheme, setTheme } = useTheme();
  const router = useRouter();

  async function handleSignOut() {
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    router.push("/login");
  }

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  const isDark = resolvedTheme === "dark";

  return (
    <aside
      style={{
        width: collapsed ? 48 : 240,
        minWidth: collapsed ? 48 : 240,
        background: "var(--bg-base)",
        borderRight: "1px solid var(--border)",
      }}
      className="relative flex flex-col h-screen transition-[width,min-width] duration-200 ease-in-out print:hidden"
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

      {/* Inner content — overflow-hidden here so text clips during collapse animation */}
      <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
        {/* Collapsed: logo at top, spacer */}
        {collapsed && (
          <div className="flex flex-col items-center flex-1 pt-[18px]">
            <Logo />
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

            <div className="px-4 pb-3">
              <label style={{ color: "var(--text-muted)" }} className="block text-xs uppercase tracking-wider mb-1">
                Summary Model
              </label>
              <OptionPicker options={MODELS} value={selectedModel} onChange={onModelChange} />
            </div>

            <div style={{ borderColor: "var(--border)" }} className="mx-4 border-t my-1" />

            <div className="px-4 py-3">
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
            </div>

            <div className="flex-1" />
          </>
        )}
      </div>

      {/* Settings footer — outside overflow-hidden so the popover can escape */}
      <div
        ref={menuRef}
        className="relative"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        <button
          onClick={() => setMenuOpen((o) => !o)}
          style={{
            color: menuOpen ? "var(--text-primary)" : "var(--text-muted)",
            background: menuOpen ? "var(--bg-tertiary)" : "transparent",
            width: "100%",
          }}
          className={`flex items-center gap-2.5 px-3.5 py-3 text-sm transition-colors hover:bg-[var(--bg-tertiary)] ${collapsed ? "justify-center" : ""}`}
        >
          <Settings size={14} className="flex-shrink-0" />
          {!collapsed && <span className="whitespace-nowrap">Settings</span>}
        </button>

        {menuOpen && mounted && (
          <div
            style={{
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              boxShadow: "0 -4px 24px rgba(0,0,0,0.4)",
            }}
            className="absolute bottom-[calc(100%+4px)] left-2 z-50 rounded-lg overflow-hidden w-44"
          >
            <button
              onClick={() => { setTheme(isDark ? "light" : "dark"); setMenuOpen(false); }}
              style={{ color: "var(--text-primary)" }}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left hover:bg-[var(--bg-tertiary)] transition-colors"
            >
              {isDark ? <Sun size={14} /> : <Moon size={14} />}
              {isDark ? "Light mode" : "Dark mode"}
            </button>
            <div style={{ borderColor: "var(--border)" }} className="border-t" />
            <button
              onClick={() => { setMenuOpen(false); onSetup(); }}
              style={{ color: "var(--text-primary)" }}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left hover:bg-[var(--bg-tertiary)] transition-colors"
            >
              <LayoutDashboard size={14} />
              Change dashboard
            </button>
            <div style={{ borderColor: "var(--border)" }} className="border-t" />
            <button
              onClick={() => { setMenuOpen(false); handleSignOut(); }}
              style={{ color: "var(--text-primary)" }}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left hover:bg-[var(--bg-tertiary)] transition-colors"
            >
              <LogOut size={14} />
              Sign out
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
