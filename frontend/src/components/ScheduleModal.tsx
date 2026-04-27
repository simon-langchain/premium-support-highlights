"use client";

import { useState, useEffect, useRef } from "react";
import { X, Trash2, Pencil, Plus, Check, ChevronDown, Search, AlertCircle, RefreshCw, ArrowLeft, Mail, Presentation } from "lucide-react";
import SlackIcon from "./SlackIcon";
import {
  fetchSchedules,
  createSchedule,
  deleteSchedule,
  type Schedule,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SECTIONS = [
  { id: "key_metrics", label: "Key Metrics" },
  { id: "ticket_trend", label: "Ticket Trend" },
  { id: "breakdowns", label: "Breakdowns" },
  { id: "account_summary", label: "Account Summary" },
  { id: "open_issues", label: "Open Issues" },
];
const ALL_SECTION_IDS = SECTIONS.map((s) => s.id);

const PERIODS = [
  { value: "1m", label: "1 Month" },
  { value: "3m", label: "3 Months" },
  { value: "6m", label: "6 Months" },
  { value: "1y", label: "1 Year" },
];

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const NTH_OPTIONS = [
  { value: 1, label: "1st" },
  { value: 2, label: "2nd" },
  { value: 3, label: "3rd" },
  { value: 4, label: "4th" },
  { value: -1, label: "Last" },
];

const MONTH_IN_QUARTER_OPTIONS = [
  { value: 1, label: "1st month" },
  { value: 2, label: "2nd month" },
  { value: 3, label: "3rd month" },
];

const PERIOD_LABELS: Record<string, string> = {
  "7d": "7 days", "1m": "1 month", "3m": "3 months", "6m": "6 months", "1y": "1 year",
};

// Ordered west-to-east by UTC offset; UTC sits at its natural position (offset 0)
const TIMEZONE_NAMES = [
  // UTC-10 to UTC-3 — Americas & Pacific
  "Pacific/Honolulu",
  "America/Anchorage",
  "America/Los_Angeles",
  "America/Denver",
  "America/Phoenix",
  "America/Chicago",
  "America/New_York",
  "America/Halifax",
  "America/Caracas",
  "America/Sao_Paulo",
  "America/Argentina/Buenos_Aires",
  // UTC+0
  "UTC",
  // UTC+0/+1 to UTC+3 — Europe & Africa
  "Europe/London",
  "Africa/Lagos",
  "Africa/Johannesburg",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Helsinki",
  "Europe/Kyiv",
  "Africa/Cairo",
  "Europe/Moscow",
  "Africa/Nairobi",
  "Asia/Riyadh",
  // UTC+3:30 to UTC+5:45 — Middle East & South Asia
  "Asia/Tehran",
  "Asia/Dubai",
  "Asia/Karachi",
  "Asia/Kolkata",
  "Asia/Kathmandu",
  // UTC+6 to UTC+8 — Central, Southeast & East Asia
  "Asia/Dhaka",
  "Asia/Yangon",
  "Asia/Bangkok",
  "Asia/Jakarta",
  "Asia/Singapore",
  "Asia/Hong_Kong",
  "Asia/Shanghai",
  "Australia/Perth",
  // UTC+9 to UTC+13 — East Asia, Oceania
  "Asia/Seoul",
  "Asia/Tokyo",
  "Australia/Darwin",
  "Australia/Adelaide",
  "Australia/Brisbane",
  "Australia/Sydney",
  "Pacific/Auckland",
  "Pacific/Fiji",
];

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

// Returns the correct abbreviation for a timezone given its current UTC offset.
// Handles DST-observing zones by checking the offset string.
function getTzAbbr(tz: string, offset: string): string {
  switch (tz) {
    // North America
    case "Pacific/Honolulu":                        return "HST";
    case "America/Anchorage":                       return offset === "GMT-8" ? "AKST" : "AKDT";
    case "America/Los_Angeles":                     return offset === "GMT-8" ? "PST" : "PDT";
    case "America/Denver":                          return offset === "GMT-7" ? "MST" : "MDT";
    case "America/Phoenix":                         return "MST";
    case "America/Chicago":                         return offset === "GMT-6" ? "CST" : "CDT";
    case "America/New_York":                        return offset === "GMT-5" ? "EST" : "EDT";
    case "America/Halifax":                         return offset === "GMT-4" ? "AST" : "ADT";
    case "America/Caracas":                         return "VET";
    case "America/Sao_Paulo":                       return offset === "GMT-3" ? "BRT" : "BRST";
    case "America/Argentina/Buenos_Aires":          return "ART";
    // Europe
    case "Europe/London":                           return offset === "GMT+1" ? "BST" : "GMT";
    case "Europe/Paris":
    case "Europe/Berlin":                           return offset === "GMT+2" ? "CEST" : "CET";
    case "Europe/Helsinki":
    case "Europe/Kyiv":                             return offset === "GMT+3" ? "EEST" : "EET";
    case "Europe/Moscow":                           return "MSK";
    // Africa / Middle East
    case "Africa/Lagos":                            return "WAT";
    case "Africa/Johannesburg":                     return "SAST";
    case "Africa/Cairo":                            return offset === "GMT+3" ? "EEST" : "EET";
    case "Africa/Nairobi":                          return "EAT";
    case "Asia/Riyadh":                             return "AST";
    case "Asia/Tehran":                             return offset === "GMT+4:30" ? "IRDT" : "IRST";
    // Asia
    case "Asia/Dubai":                              return "GST";
    case "Asia/Karachi":                            return "PKT";
    case "Asia/Kolkata":                            return "IST";
    case "Asia/Kathmandu":                          return "NPT";
    case "Asia/Dhaka":                              return "BST";
    case "Asia/Yangon":                             return "MMT";
    case "Asia/Bangkok":                            return "ICT";
    case "Asia/Jakarta":                            return "WIB";
    case "Asia/Singapore":                          return "SGT";
    case "Asia/Hong_Kong":                          return "HKT";
    case "Asia/Shanghai":                           return "CST";
    case "Asia/Seoul":                              return "KST";
    case "Asia/Tokyo":                              return "JST";
    // Australia / Pacific
    case "Australia/Perth":                         return "AWST";
    case "Australia/Darwin":                        return "ACST";
    case "Australia/Adelaide":                      return offset === "GMT+10:30" ? "ACDT" : "ACST";
    case "Australia/Brisbane":                      return "AEST";
    case "Australia/Sydney":                        return offset === "GMT+11" ? "AEDT" : "AEST";
    case "Pacific/Auckland":                        return offset === "GMT+13" ? "NZDT" : "NZST";
    case "Pacific/Fiji":                            return "FJT";
    default:                                        return "";
  }
}

function tzLabel(tz: string): string {
  try {
    if (tz === "UTC") return "UTC";
    const city = tz.split("/").pop()?.replace(/_/g, " ") ?? tz;
    const offset = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "shortOffset" })
      .formatToParts(new Date()).find((p) => p.type === "timeZoneName")?.value ?? "";
    const abbr = getTzAbbr(tz, offset);
    const suffix = abbr ? `${abbr} · ${offset}` : offset;
    return `${city} (${suffix})`;
  } catch {
    return tz;
  }
}

// Computed once at module load — must be after TZ_ABBR and tzLabel
const TIMEZONE_OPTIONS = TIMEZONE_NAMES.map((tz) => ({ value: tz, label: tzLabel(tz) }));

// Returns just the short abbreviation for compact display (e.g. "PDT", "BST", "IST")
function tzShort(tz: string): string {
  if (tz === "UTC") return "UTC";
  try {
    const offset = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "shortOffset" })
      .formatToParts(new Date()).find((p) => p.type === "timeZoneName")?.value ?? "";
    return getTzAbbr(tz, offset) || offset;
  } catch {
    return tz;
  }
}

function describeSchedule(s: Schedule): string {
  const day = WEEKDAYS[s.weekday] ?? "?";
  const localHour = s.hour_local ?? s.hour_utc;
  const tz = s.timezone ?? "UTC";
  const time = `${String(localHour).padStart(2, "0")}:00 ${tzShort(tz)}`;
  const period = PERIOD_LABELS[s.period] ?? s.period;
  let when: string;
  if (s.frequency === "weekly") {
    when = `Every ${day}`;
  } else if (s.frequency === "monthly") {
    const nthLabel = NTH_OPTIONS.find((o) => o.value === s.nth)?.label ?? String(s.nth);
    when = `${nthLabel} ${day} of every month`;
  } else {
    const nthLabel = NTH_OPTIONS.find((o) => o.value === s.nth)?.label ?? String(s.nth);
    const mqLabel = MONTH_IN_QUARTER_OPTIONS.find((o) => o.value === (s.month_in_quarter ?? 1))?.label ?? "1st month";
    when = `${nthLabel} ${day}, ${mqLabel} of every quarter`;
  }
  if (s.destination_type === "qbr") {
    return `${when} at ${time}`;
  }
  return `${when} at ${time} · ${period}`;
}

function formatNextRun(iso: string | null, tz: string = "UTC"): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const date = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: tz });
    const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: tz });
    return `${date} at ${time} ${tzShort(tz)}`;
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Email tag input
// ---------------------------------------------------------------------------

interface EmailTagInputProps {
  emails: string[];
  onChange: (emails: string[]) => void;
  placeholder?: string;
}

function EmailTagInput({ emails, onChange, placeholder = "email@example.com" }: EmailTagInputProps) {
  const [inputVal, setInputVal] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function commitMany(raw: string) {
    const parts = raw.split(/[\s,\n]+/).map((s) => s.trim()).filter(Boolean);
    const toAdd = parts.filter((p) => !emails.includes(p));
    if (toAdd.length > 0) onChange([...emails, ...toAdd]);
    setInputVal("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commitMany(inputVal); }
    else if (e.key === " " && inputVal.includes("@")) { e.preventDefault(); commitMany(inputVal); }
    else if (e.key === "Backspace" && inputVal === "" && emails.length > 0) {
      onChange(emails.slice(0, -1));
    }
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = e.target.value;
    if (val.includes(",")) {
      const parts = val.split(",");
      const remainder = parts.pop() ?? "";
      commitMany(parts.join(","));
      setInputVal(remainder.trimStart());
    } else {
      setInputVal(val);
    }
  }

  function handlePaste(e: React.ClipboardEvent<HTMLInputElement>) {
    const pasted = e.clipboardData.getData("text");
    if (/[\s,\n]/.test(pasted)) {
      e.preventDefault();
      commitMany(pasted);
    }
  }

  return (
    <div
      className="flex flex-wrap gap-1 rounded px-2 py-1.5 cursor-text"
      style={{ border: "1px solid var(--border)", background: "var(--bg-primary)", minHeight: "32px" }}
      onClick={() => inputRef.current?.focus()}
    >
      {emails.map((email) => (
        <span
          key={email}
          className="flex items-center gap-1 text-xs px-1.5 py-0.5 rounded"
          style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
        >
          {email}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onChange(emails.filter((em) => em !== email)); }}
            className="rounded hover:opacity-70 leading-none"
            style={{ color: "var(--text-caption)" }}
          >
            <X size={10} />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        value={inputVal}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onBlur={() => commitMany(inputVal)}
        onPaste={handlePaste}
        placeholder={emails.length === 0 ? placeholder : ""}
        className="text-xs bg-transparent border-none outline-none flex-1"
        style={{ color: "var(--text-primary)", minWidth: "8rem" }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline channel picker
// ---------------------------------------------------------------------------

interface ChannelPickerProps {
  channels: { id: string; name: string }[];
  selected: string | null;
  onSelect: (id: string) => void;
}

function ChannelPicker({ channels, selected, onSelect }: ChannelPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  useEffect(() => {
    if (open) setTimeout(() => searchRef.current?.focus(), 50);
  }, [open]);

  const filtered = channels.filter((c) => !query || c.name.toLowerCase().includes(query.toLowerCase()));
  const selectedName = channels.find((c) => c.id === selected)?.name ?? null;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between gap-2 rounded px-2.5 py-1.5 text-xs text-left"
        style={{
          background: "var(--bg-primary)", border: "1px solid var(--border)",
          color: selectedName ? "var(--text-primary)" : "var(--text-caption)",
        }}
      >
        <span className="truncate">{selectedName ? `#${selectedName}` : "Choose channel…"}</span>
        <ChevronDown size={11} style={{ flexShrink: 0 }} />
      </button>
      {open && (
        <div
          className="absolute z-50 left-0 top-[calc(100%+2px)] rounded-lg p-1 w-full"
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}
        >
          <div className="flex items-center gap-1.5 px-2 py-1 mb-1" style={{ borderBottom: "1px solid var(--border)" }}>
            <Search size={11} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search…"
              className="text-xs bg-transparent border-none outline-none w-full"
              style={{ color: "var(--text-primary)" }}
            />
          </div>
          <div className="max-h-36 overflow-y-auto">
            {filtered.length === 0
              ? <div className="px-2 py-1.5 text-xs" style={{ color: "var(--text-caption)" }}>No channels found</div>
              : filtered.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => { onSelect(c.id); setOpen(false); setQuery(""); }}
                  className="w-full text-left px-2 py-1 text-xs rounded hover:bg-[var(--bg-tertiary)]"
                  style={{ color: c.id === selected ? "var(--accent)" : "var(--text-primary)" }}
                >
                  #{c.name}
                </button>
              ))
            }
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Generic option picker (matches ChannelPicker style)
// ---------------------------------------------------------------------------

interface OptionPickerProps {
  options: { value: string; label: string }[];
  selected: string;
  onSelect: (v: string) => void;
  searchable?: boolean;
  direction?: "down" | "up";
  className?: string;
}

function OptionPicker({ options, selected, onSelect, searchable = false, direction = "down", className }: OptionPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  useEffect(() => {
    if (!open) return;
    if (searchable) setTimeout(() => searchRef.current?.focus(), 50);
    setTimeout(() => {
      listRef.current?.querySelector<HTMLElement>("[data-selected]")?.scrollIntoView({ block: "center" });
    }, 0);
  }, [open, searchable]);

  const filtered = searchable && query
    ? options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase()))
    : options;

  const selectedLabel = options.find((o) => o.value === selected)?.label ?? selected;

  return (
    <div ref={ref} className={`relative ${className ?? ""}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between gap-2 rounded px-2.5 py-1.5 text-xs text-left"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
      >
        <span className="truncate">{selectedLabel}</span>
        <ChevronDown size={11} style={{ flexShrink: 0 }} />
      </button>
      {open && (
        <div
          className={`absolute z-50 left-0 rounded-lg p-1 w-full ${direction === "up" ? "bottom-[calc(100%+2px)]" : "top-[calc(100%+2px)]"}`}
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}
        >
          {searchable && (
            <div className="flex items-center gap-1.5 px-2 py-1 mb-1" style={{ borderBottom: "1px solid var(--border)" }}>
              <Search size={11} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
              <input
                ref={searchRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search…"
                className="text-xs bg-transparent border-none outline-none w-full"
                style={{ color: "var(--text-primary)" }}
              />
            </div>
          )}
          <div ref={listRef} className="max-h-48 overflow-y-auto">
            {filtered.length === 0
              ? <div className="px-2 py-1.5 text-xs" style={{ color: "var(--text-caption)" }}>No results</div>
              : filtered.map((o) => (
                <button
                  key={o.value}
                  data-selected={o.value === selected ? "true" : undefined}
                  type="button"
                  onClick={() => { onSelect(o.value); setOpen(false); setQuery(""); }}
                  className="w-full text-left px-2 py-1 text-xs rounded hover:bg-[var(--bg-tertiary)]"
                  style={{ color: o.value === selected ? "var(--accent)" : "var(--text-primary)" }}
                >
                  {o.label}
                </button>
              ))
            }
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ScheduleModal
// ---------------------------------------------------------------------------

interface ScheduleModalProps {
  accountId: string;
  accountName: string;
  defaultPeriod: string;
  channelId: string | null;
  availableChannels: { id: string; name: string }[];
  onClose: () => void;
  openToNewQbr?: boolean;
}

export default function ScheduleModal({
  accountId,
  accountName,
  defaultPeriod,
  channelId,
  availableChannels,
  onClose,
  openToNewQbr = false,
}: ScheduleModalProps) {
  // Navigation
  const [view, setView] = useState<"list" | "form">(openToNewQbr ? "form" : "list");

  // List state
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Form state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [mode, setMode] = useState<"slack" | "email" | "qbr">(openToNewQbr ? "qbr" : "slack");
  const [selectedChannel, setSelectedChannel] = useState<string | null>(channelId);
  const [emails, setEmails] = useState<string[]>([]);
  // QBR notification sub-fields
  const [qbrNotifyType, setQbrNotifyType] = useState<"slack" | "email">("slack");
  const [qbrNotifyChannel, setQbrNotifyChannel] = useState<string | null>(channelId);
  const [qbrNotifyEmails, setQbrNotifyEmails] = useState<string[]>([]);
  const [period, setPeriod] = useState(["1m", "3m", "6m", "1y"].includes(defaultPeriod) ? defaultPeriod : "1m");
  const [frequency, setFrequency] = useState<"weekly" | "monthly" | "quarterly">(openToNewQbr ? "quarterly" : "monthly");
  const [weekday, setWeekday] = useState(0);
  const [nth, setNth] = useState(1);
  const [monthInQuarter, setMonthInQuarter] = useState(1);
  const [hourLocal, setHourLocal] = useState(9);
  const [timezone, setTimezone] = useState("UTC");
  const [selectedSections, setSelectedSections] = useState<Set<string>>(new Set(ALL_SECTION_IDS));
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => { setSelectedChannel(channelId); }, [channelId]);

  function loadSchedules() {
    setLoadingList(true);
    setListError(null);
    fetchSchedules(accountId)
      .then((data) => { setSchedules(data); setListError(null); })
      .catch((err) => setListError(err instanceof Error ? err.message : "Failed to load schedules"))
      .finally(() => setLoadingList(false));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(loadSchedules, [accountId]);

  function openNewForm() {
    setEditingId(null);
    setLabel("");
    setMode("slack");
    setSelectedChannel(channelId);
    setEmails([]);
    setQbrNotifyType("slack");
    setQbrNotifyChannel(channelId);
    setQbrNotifyEmails([]);
    setPeriod(["1m", "3m", "6m", "1y"].includes(defaultPeriod) ? defaultPeriod : "1m");
    setFrequency("monthly");
    setWeekday(0);
    setNth(1);
    setMonthInQuarter(1);
    setHourLocal(9);
    setTimezone("UTC");
    setSelectedSections(new Set(ALL_SECTION_IDS));
    setCreateError(null);
    setView("form");
  }

  function openEditForm(s: Schedule) {
    setEditingId(s.cron_id);
    setLabel(s.label ?? "");
    setMode((s.destination_type ?? "slack") as "slack" | "email" | "qbr");
    setSelectedChannel(s.channel_id ?? channelId);
    setEmails(s.email_addresses ?? []);
    setQbrNotifyType((s.qbr_notify_type ?? "slack") as "slack" | "email");
    setQbrNotifyChannel(s.qbr_notify_channel_id ?? channelId);
    setQbrNotifyEmails(s.qbr_notify_emails ?? []);
    setPeriod(s.period ?? "1m");
    setFrequency(s.frequency ?? "monthly");
    setWeekday(s.weekday ?? 0);
    setNth(s.nth ?? 1);
    setMonthInQuarter(s.month_in_quarter ?? 1);
    setHourLocal(s.hour_local ?? s.hour_utc ?? 9);
    setTimezone(s.timezone ?? "UTC");
    setSelectedSections(new Set(s.sections ?? ALL_SECTION_IDS));
    setCreateError(null);
    setView("form");
  }

  function goBack() {
    setView("list");
    setEditingId(null);
    setCreateError(null);
  }

  async function handleDelete(cronId: string) {
    setDeletingId(cronId);
    try {
      await deleteSchedule(cronId);
      setSchedules((prev) => prev.filter((s) => s.cron_id !== cronId));
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Failed to delete schedule");
    } finally {
      setDeletingId(null);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setCreateError(null);

    if (mode === "email" && emails.length === 0) { setCreateError("Enter at least one email address."); return; }
    if (mode === "slack" && !selectedChannel) { setCreateError("Choose a Slack channel."); return; }
    if (mode === "qbr" && qbrNotifyType === "email" && qbrNotifyEmails.length === 0) { setCreateError("Enter at least one @langchain.dev email for QBR notifications."); return; }
    if (mode === "qbr" && qbrNotifyType === "slack" && !qbrNotifyChannel) { setCreateError("Choose a Slack channel for QBR notifications."); return; }
    if (mode !== "qbr" && selectedSections.size === 0) { setCreateError("Select at least one section."); return; }

    // Validate @langchain.dev constraint for QBR email notifications
    if (mode === "qbr" && qbrNotifyType === "email") {
      const invalid = qbrNotifyEmails.filter((e) => !e.toLowerCase().endsWith("@langchain.dev"));
      if (invalid.length > 0) {
        setCreateError(`QBR notifications can only go to @langchain.dev addresses: ${invalid.join(", ")}`);
        return;
      }
    }

    setCreating(true);
    try {
      if (editingId) {
        await deleteSchedule(editingId);
        setSchedules((prev) => prev.filter((s) => s.cron_id !== editingId));
      }
      const created = await createSchedule({
        account_id: accountId,
        account_name: accountName,
        label,
        destination_type: mode,
        channel_id: mode === "slack" ? (selectedChannel ?? undefined) : undefined,
        email_addresses: mode === "email" ? emails : undefined,
        qbr_notify_type: mode === "qbr" ? qbrNotifyType : undefined,
        qbr_notify_channel_id: mode === "qbr" && qbrNotifyType === "slack" ? (qbrNotifyChannel ?? undefined) : undefined,
        qbr_notify_emails: mode === "qbr" && qbrNotifyType === "email" ? qbrNotifyEmails : undefined,
        sections: mode !== "qbr" ? [...selectedSections] : undefined,
        period,
        frequency,
        weekday,
        nth,
        month_in_quarter: monthInQuarter,
        hour_local: hourLocal,
        timezone,
      });
      setSchedules((prev) => [...prev, created]);
      setView("list");
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to save schedule");
    } finally {
      setCreating(false);
    }
  }

  const allSelected = ALL_SECTION_IDS.every((id) => selectedSections.has(id));

  function toggleSection(id: string) {
    setSelectedSections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function getChannelName(cid: string | null): string {
    if (!cid) return "default channel";
    return availableChannels.find((c) => c.id === cid)?.name ?? cid;
  }

  // ---------------------------------------------------------------------------
  // Shared modal shell
  // ---------------------------------------------------------------------------

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.6)" }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="relative w-full max-w-lg rounded-xl overflow-hidden flex flex-col"
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          boxShadow: "0 16px 48px rgba(0,0,0,0.5)",
          maxHeight: "90vh",
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          <div className="flex items-center gap-2 min-w-0">
            {view === "form" && (
              <button
                onClick={goBack}
                className="rounded p-1 hover:bg-[var(--bg-tertiary)] transition-colors flex-shrink-0"
                style={{ color: "var(--text-muted)" }}
              >
                <ArrowLeft size={14} />
              </button>
            )}
            <div className="min-w-0">
              <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                {view === "form" ? (editingId ? "Edit Schedule" : "New Schedule") : "Scheduled Reports"}
              </h2>
              <p className="text-xs mt-0.5 truncate" style={{ color: "var(--text-muted)" }}>{accountName}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 hover:bg-[var(--bg-tertiary)] transition-colors flex-shrink-0"
            style={{ color: "var(--text-muted)" }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1">
          {view === "list" ? (

            // ----------------------------------------------------------------
            // List view
            // ----------------------------------------------------------------
            <div className="flex flex-col h-full">
              <div className="flex-1 px-5 pt-4 pb-3">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-xs font-medium uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
                    Active schedules
                  </h3>
                  <button
                    onClick={loadSchedules}
                    disabled={loadingList}
                    title="Refresh"
                    className="rounded p-1 hover:bg-[var(--bg-tertiary)] transition-colors disabled:opacity-40"
                    style={{ color: "var(--text-muted)" }}
                  >
                    <RefreshCw size={12} className={loadingList ? "animate-spin" : ""} />
                  </button>
                </div>

                {listError ? (
                  <div className="flex items-start gap-2 text-xs text-red-400 py-1">
                    <AlertCircle size={13} className="mt-0.5 flex-shrink-0" />
                    <span>{listError}</span>
                  </div>
                ) : loadingList ? (
                  <p className="text-xs py-2" style={{ color: "var(--text-caption)" }}>Loading…</p>
                ) : schedules.length === 0 ? (
                  <p className="text-xs py-4 text-center" style={{ color: "var(--text-caption)" }}>
                    No scheduled reports yet.
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {schedules.map((s) => {
                      const addrs = s.email_addresses ?? [];
                      let destLine: string;
                      if (s.destination_type === "slack") {
                        destLine = `#${getChannelName(s.channel_id)}`;
                      } else if (s.destination_type === "qbr") {
                        if (s.qbr_notify_type === "slack") {
                          destLine = `Notify: #${getChannelName(s.qbr_notify_channel_id)}`;
                        } else {
                          const ne = s.qbr_notify_emails ?? [];
                          destLine = ne.length === 0 ? "Notify: —"
                            : ne.length <= 2 ? `Notify: ${ne.join(", ")}`
                            : `Notify: ${ne.slice(0, 2).join(", ")} +${ne.length - 2} more`;
                        }
                      } else {
                        destLine = addrs.length === 0 ? "—"
                          : addrs.length <= 2 ? addrs.join(", ")
                          : `${addrs.slice(0, 2).join(", ")} +${addrs.length - 2} more`;
                      }
                      return (
                        <div
                          key={s.cron_id}
                          className="rounded-lg px-3 py-2.5"
                          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}
                        >
                          {/* Row 1: label + type pill + actions */}
                          <div className="flex items-center justify-between gap-2 mb-1">
                            <div className="flex items-center gap-1.5 min-w-0">
                              <span className="text-xs font-medium truncate" style={{ color: s.label ? "var(--text-primary)" : "var(--text-muted)", fontStyle: s.label ? "normal" : "italic" }}>
                                {s.label || "Untitled"}
                              </span>
                              {s.destination_type === "slack" && <SlackIcon size={16} />}
                              {s.destination_type === "email" && <Mail size={16} strokeWidth={1.75} style={{ color: "var(--accent)" }} />}
                              {s.destination_type === "qbr" && <>
                                <Presentation size={16} strokeWidth={1.75} style={{ color: "var(--accent)" }} />
                                {s.qbr_notify_type === "slack"
                                  ? <SlackIcon size={14} />
                                  : <Mail size={14} strokeWidth={1.75} style={{ color: "var(--accent)" }} />}
                              </>}
                            </div>
                            <div className="flex items-center gap-0.5 flex-shrink-0">
                              <button
                                onClick={() => openEditForm(s)}
                                title="Edit"
                                className="rounded p-1 hover:bg-[var(--bg-tertiary)] transition-colors"
                                style={{ color: "var(--text-caption)" }}
                              >
                                <Pencil size={12} />
                              </button>
                              <button
                                onClick={() => handleDelete(s.cron_id)}
                                disabled={deletingId === s.cron_id}
                                title="Delete"
                                className="rounded p-1 hover:bg-red-500/10 transition-colors disabled:opacity-40"
                                style={{ color: "var(--text-caption)" }}
                              >
                                <Trash2 size={12} />
                              </button>
                            </div>
                          </div>
                          {/* Row 2: schedule description */}
                          <p className="text-xs" style={{ color: "var(--text-muted)" }}>{describeSchedule(s)}</p>
                          {/* Row 3: destination */}
                          <p className="text-xs mt-0.5 truncate" style={{ color: "var(--text-muted)" }}>{destLine}</p>
                          {/* Row 4: next run + created by */}
                          {(s.next_run_date || s.created_by) && (
                            <p className="text-xs mt-1" style={{ color: "var(--text-caption)" }}>
                              {s.next_run_date && <>Next {formatNextRun(s.next_run_date, s.timezone ?? "UTC")}</>}
                              {s.created_by && <span>{s.next_run_date ? " · " : ""}by {s.created_by}</span>}
                            </p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Add button pinned to bottom of list view */}
              <div className="px-5 py-4 flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
                <button
                  onClick={openNewForm}
                  className="w-full flex items-center justify-center gap-1.5 text-xs rounded px-3 py-2 transition-opacity hover:opacity-90"
                  style={{ background: "var(--accent)", color: "#fff" }}
                >
                  <Plus size={12} />
                  Add Schedule
                </button>
              </div>
            </div>

          ) : (

            // ----------------------------------------------------------------
            // Form view (create / edit)
            // ----------------------------------------------------------------
            <form onSubmit={handleSubmit} className="px-5 py-4 space-y-4">

              {/* Label */}
              <div>
                <label className="block text-xs mb-1" style={{ color: "var(--text-muted)" }}>
                  Label <span style={{ color: "var(--text-caption)" }}>(optional)</span>
                </label>
                <input
                  type="text"
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="e.g. Monthly Slack update"
                  className="w-full rounded px-2.5 py-1.5 text-xs bg-transparent outline-none"
                  style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}
                />
              </div>

              {/* Destination */}
              <div>
                <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Schedule type</label>
                <div className="flex rounded mb-2 p-0.5 gap-0.5" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                  {(["slack", "email", "qbr"] as const).map((m) => (
                    <button
                      key={m} type="button"
                      onClick={() => {
                        setMode(m);
                        if (m === "qbr") setFrequency("quarterly");
                      }}
                      className="flex-1 flex items-center justify-center gap-1.5 text-xs rounded py-1 transition-colors"
                      style={{ background: mode === m ? "var(--accent)" : "transparent", color: mode === m ? "#fff" : "var(--text-muted)" }}
                    >
                      {m === "slack" ? <SlackIcon size={12} /> : m === "email" ? <Mail size={11} strokeWidth={2} /> : <Presentation size={11} strokeWidth={2} />}
                      {m === "slack" ? "Slack" : m === "email" ? "Email" : "QBR Slides"}
                    </button>
                  ))}
                </div>
                {mode === "slack" && (
                  availableChannels.length > 0
                    ? <ChannelPicker channels={availableChannels} selected={selectedChannel} onSelect={setSelectedChannel} />
                    : <p className="text-xs" style={{ color: "var(--text-caption)" }}>No Slack channels — check SLACK_BOT_TOKEN.</p>
                )}
                {mode === "email" && (
                  <EmailTagInput emails={emails} onChange={setEmails} />
                )}
                {mode === "qbr" && (
                  <div className="space-y-2">
                    <p className="text-xs italic" style={{ color: "var(--text-muted)" }}>
                      Slides are generated and shared with the @langchain.dev domain. Choose how to be notified when ready.
                    </p>
                    <div className="flex rounded p-0.5 gap-0.5" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)" }}>
                      {(["slack", "email"] as const).map((t) => (
                        <button
                          key={t} type="button" onClick={() => setQbrNotifyType(t)}
                          className="flex-1 flex items-center justify-center gap-1.5 text-xs rounded py-1 transition-colors"
                          style={{ background: qbrNotifyType === t ? "var(--bg-secondary)" : "transparent", color: qbrNotifyType === t ? "var(--text-primary)" : "var(--text-muted)", border: qbrNotifyType === t ? "1px solid var(--border)" : "1px solid transparent" }}
                        >
                          {t === "slack" ? <SlackIcon size={11} /> : <Mail size={10} strokeWidth={2} />}
                          {t === "slack" ? "Slack" : "Email"}
                        </button>
                      ))}
                    </div>
                    {qbrNotifyType === "slack" ? (
                      availableChannels.length > 0
                        ? <ChannelPicker channels={availableChannels} selected={qbrNotifyChannel} onSelect={setQbrNotifyChannel} />
                        : <p className="text-xs" style={{ color: "var(--text-caption)" }}>No Slack channels — check SLACK_BOT_TOKEN.</p>
                    ) : (
                      <>
                        <EmailTagInput emails={qbrNotifyEmails} onChange={setQbrNotifyEmails} placeholder="name@langchain.dev" />
                        <p className="text-xs italic" style={{ color: "var(--text-muted)" }}>Only @langchain.dev addresses allowed.</p>
                      </>
                    )}
                  </div>
                )}
              </div>

              {/* Period — not applicable for QBR (always current quarter) */}
              {mode !== "qbr" && <div>
                <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Report period</label>
                <div className="flex gap-1 flex-wrap">
                  {PERIODS.map((p) => (
                    <button key={p.value} type="button" onClick={() => setPeriod(p.value)}
                      className="text-xs rounded px-2.5 py-1 transition-colors"
                      style={{
                        background: period === p.value ? "var(--accent)" : "var(--bg-primary)",
                        color: period === p.value ? "#fff" : "var(--text-muted)",
                        border: `1px solid ${period === p.value ? "var(--accent)" : "var(--border)"}`,
                      }}
                    >{p.label}</button>
                  ))}
                </div>
              </div>}

              {/* Frequency */}
              <div>
                <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Frequency</label>
                <div className="flex gap-1">
                  {(mode === "qbr" ? ["monthly", "quarterly"] : ["weekly", "monthly", "quarterly"] as const).map((f) => (
                    <button key={f} type="button" onClick={() => setFrequency(f as typeof frequency)}
                      className="text-xs rounded px-2.5 py-1 capitalize transition-colors"
                      style={{
                        background: frequency === f ? "var(--accent)" : "var(--bg-primary)",
                        color: frequency === f ? "#fff" : "var(--text-muted)",
                        border: `1px solid ${frequency === f ? "var(--accent)" : "var(--border)"}`,
                      }}
                    >{f.charAt(0).toUpperCase() + f.slice(1)}</button>
                  ))}
                </div>
              </div>

              {/* Occurrence — monthly */}
              {frequency === "monthly" && (
                <div>
                  <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Occurrence in month</label>
                  <div className="flex gap-1 flex-wrap">
                    {NTH_OPTIONS.map((o) => (
                      <button key={o.value} type="button" onClick={() => setNth(o.value)}
                        className="text-xs rounded px-2.5 py-1 transition-colors"
                        style={{
                          background: nth === o.value ? "var(--accent)" : "var(--bg-primary)",
                          color: nth === o.value ? "#fff" : "var(--text-muted)",
                          border: `1px solid ${nth === o.value ? "var(--accent)" : "var(--border)"}`,
                        }}
                      >{o.label}</button>
                    ))}
                  </div>
                </div>
              )}

              {/* Occurrence — quarterly */}
              {frequency === "quarterly" && (
                <>
                  <div>
                    <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Month of quarter</label>
                    <div className="flex gap-1">
                      {MONTH_IN_QUARTER_OPTIONS.map((o) => (
                        <button key={o.value} type="button" onClick={() => setMonthInQuarter(o.value)}
                          className="text-xs rounded px-2.5 py-1 transition-colors"
                          style={{
                            background: monthInQuarter === o.value ? "var(--accent)" : "var(--bg-primary)",
                            color: monthInQuarter === o.value ? "#fff" : "var(--text-muted)",
                            border: `1px solid ${monthInQuarter === o.value ? "var(--accent)" : "var(--border)"}`,
                          }}
                        >{o.label}</button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Occurrence in month</label>
                    <div className="flex gap-1 flex-wrap">
                      {NTH_OPTIONS.map((o) => (
                        <button key={o.value} type="button" onClick={() => setNth(o.value)}
                          className="text-xs rounded px-2.5 py-1 transition-colors"
                          style={{
                            background: nth === o.value ? "var(--accent)" : "var(--bg-primary)",
                            color: nth === o.value ? "#fff" : "var(--text-muted)",
                            border: `1px solid ${nth === o.value ? "var(--accent)" : "var(--border)"}`,
                          }}
                        >{o.label}</button>
                      ))}
                    </div>
                  </div>
                </>
              )}

              {/* Day */}
              <div>
                <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Day</label>
                <div className="flex gap-1 flex-wrap">
                  {WEEKDAYS.map((d, i) => (
                    <button key={i} type="button" onClick={() => setWeekday(i)}
                      className="text-xs rounded px-2.5 py-1 transition-colors"
                      style={{
                        background: weekday === i ? "var(--accent)" : "var(--bg-primary)",
                        color: weekday === i ? "#fff" : "var(--text-muted)",
                        border: `1px solid ${weekday === i ? "var(--accent)" : "var(--border)"}`,
                      }}
                    >{d}</button>
                  ))}
                </div>
              </div>

              {/* Time + Timezone */}
              <div>
                <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Time</label>
                <div className="flex items-center gap-2">
                  <OptionPicker
                    options={Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: `${String(h).padStart(2, "0")}:00` }))}
                    selected={String(hourLocal)}
                    onSelect={(v) => setHourLocal(Number(v))}
                    direction="up"
                    className="w-24"
                  />
                  <OptionPicker
                    options={TIMEZONE_OPTIONS}
                    selected={timezone}
                    onSelect={setTimezone}
                    searchable
                    direction="up"
                    className="flex-1 min-w-0"
                  />
                </div>
              </div>

              {/* Sections — not applicable for QBR */}
              {mode !== "qbr" && <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs" style={{ color: "var(--text-muted)" }}>Sections</label>
                  <button
                    type="button"
                    onClick={() => setSelectedSections(allSelected ? new Set() : new Set(ALL_SECTION_IDS))}
                    className="text-xs hover:underline"
                    style={{ color: "var(--accent)" }}
                  >
                    {allSelected ? "Deselect all" : "Select all"}
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-0.5">
                  {SECTIONS.map((s) => {
                    const checked = selectedSections.has(s.id);
                    return (
                      <button
                        key={s.id} type="button" onClick={() => toggleSection(s.id)}
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
              </div>}

              {/* Error */}
              {createError && (
                <div className="flex items-start gap-2 text-xs text-red-400">
                  <AlertCircle size={13} className="mt-0.5 flex-shrink-0" />
                  <span>{createError}</span>
                </div>
              )}

              {/* Submit */}
              <button
                type="submit"
                disabled={creating}
                className="w-full flex items-center justify-center gap-1.5 text-xs rounded px-3 py-2 disabled:opacity-50 transition-opacity hover:opacity-90"
                style={{ background: "var(--accent)", color: "#fff" }}
              >
                <Plus size={12} />
                {creating
                  ? (editingId ? "Updating…" : "Scheduling…")
                  : (editingId ? "Update Schedule" : "Add Schedule")}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
