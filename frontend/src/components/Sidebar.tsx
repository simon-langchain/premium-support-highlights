"use client";

import { useState, useEffect, useRef } from "react";
import { ChevronLeft, ChevronRight, Sun, Moon, LogOut, Settings, LayoutDashboard } from "lucide-react";
import { useTheme } from "next-themes";
import { useRouter } from "next/navigation";
import type { Account, AccountGroup, AccountSettings, LlmModel } from "@/lib/api";
import OptionPicker from "@/components/OptionPicker";
import AccountPicker from "@/components/AccountPicker";
import RefreshButton from "@/components/RefreshButton";

interface SidebarProps {
  accounts: Account[];
  selected: Account | null;
  onSelect: (account: Account) => void;
  onGroupsChanged: (created: AccountGroup | null, removedId?: string) => void;
  onRefresh: () => void;
  dataUpdatedAt: Date | null;
  selectedProvider: string;
  selectedModelName: string;
  onProviderChange: (provider: string) => void;
  onModelChange: (model: string) => void;
  models: LlmModel[];
  period: string;
  onPeriodChange: (period: string) => void;
  onSetup: () => void;
  tiers: string[];
  selectedTier: string;
  onTierChange: (tier: string) => void;
  /** Per-account settings shown as switches; null = no account selected */
  accountSettings: AccountSettings | null;
  onAccountSettingChange: (key: keyof AccountSettings, value: boolean) => void;
}

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


function SidebarField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="px-4 pb-3">
      <label style={{ color: "var(--text-muted)" }} className="block text-xs uppercase tracking-wider mb-1">
        {label}
      </label>
      {children}
    </div>
  );
}

function SidebarDivider() {
  return <div style={{ borderColor: "var(--border)" }} className="mx-4 border-t mb-3" />;
}

function SidebarSwitch({
  label,
  title,
  checked,
  onChange,
}: {
  label: string;
  title: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      title={title}
      className="w-full flex items-center justify-between gap-2"
    >
      <span className="text-sm" style={{ color: "var(--text-primary)" }}>{label}</span>
      <span
        className="relative w-7 h-4 rounded-full flex-shrink-0 transition-colors"
        style={{ background: checked ? "#006ddd" : "var(--text-caption)" }}
      >
        <span className="absolute top-0.5 w-3 h-3 rounded-full bg-white transition-[left]" style={{ left: checked ? 14 : 2 }} />
      </span>
    </button>
  );
}

export default function Sidebar({
  accounts,
  selected,
  onSelect,
  onGroupsChanged,
  onRefresh,
  dataUpdatedAt,
  selectedProvider,
  selectedModelName,
  onProviderChange,
  onModelChange,
  models,
  period,
  onPeriodChange,
  onSetup,
  tiers,
  selectedTier,
  onTierChange,
  accountSettings,
  onAccountSettingChange,
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
            </div>

            {/* What to look at */}
            <SidebarField label="Support Tier">
              <OptionPicker
                options={tiers.length > 0
                  ? tiers.map((t) => ({ value: t, label: t }))
                  : [{ value: "Premium", label: "Premium" }]}
                value={selectedTier}
                onChange={onTierChange}
              />
            </SidebarField>
            <SidebarField label="Account">
              <AccountPicker accounts={accounts} selected={selected} onSelect={onSelect} onGroupsChanged={onGroupsChanged} />
            </SidebarField>
            <SidebarField label="Time Period">
              <OptionPicker options={PERIODS} value={period} onChange={onPeriodChange} />
            </SidebarField>

            {/* Per-account display settings */}
            {accountSettings && (
              <>
                <SidebarDivider />
                <SidebarField label="Display">
                  <div className="flex flex-col gap-2 pt-0.5">
                    <SidebarSwitch
                      label="Linked ticket IDs"
                      title="Show Linear/GitHub IDs on this account's tickets. Remembered for this account."
                      checked={accountSettings.show_linked_ids}
                      onChange={(v) => onAccountSettingChange("show_linked_ids", v)}
                    />
                    <SidebarSwitch
                      label="Flag duplicates"
                      title="Group possible duplicate tickets in the open issues list. Remembered for this account."
                      checked={accountSettings.flag_duplicates}
                      onChange={(v) => onAccountSettingChange("flag_duplicates", v)}
                    />
                  </div>
                </SidebarField>
              </>
            )}

            {/* Model used for AI summaries */}
            <SidebarDivider />
            <SidebarField label="AI Model">
              <div className="flex flex-col gap-1.5">
                <OptionPicker
                  options={[...new Set(models.map((m) => m.provider))].map((p) => ({
                    value: p,
                    label: p.charAt(0).toUpperCase() + p.slice(1),
                  }))}
                  value={selectedProvider}
                  onChange={onProviderChange}
                />
                <OptionPicker
                  options={models.filter((m) => m.provider === selectedProvider).map((m) => ({ value: m.id, label: m.label }))}
                  value={selectedModelName}
                  onChange={onModelChange}
                />
              </div>
            </SidebarField>

            <SidebarDivider />

            <RefreshButton onRefresh={onRefresh} dataUpdatedAt={dataUpdatedAt} />

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
