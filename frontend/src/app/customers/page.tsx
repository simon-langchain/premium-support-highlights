"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Copy, Loader2, ArrowUpDown, ChevronRight, Clock, Presentation, CheckCircle2, Circle, ExternalLink, X, RefreshCw, Trash2 } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import MetricCard from "@/components/MetricCard";
import TicketCard from "@/components/TicketCard";
import DuplicateGroupCard from "@/components/DuplicateGroupCard";
import DismissedDuplicates from "@/components/DismissedDuplicates";
import { memberLabels } from "@/lib/accountLabels";
import TrendChart from "@/components/TrendChart";
import SummaryPanel from "@/components/SummaryPanel";
import {
  fetchAccounts,
  fetchAccountSettings,
  updateAccountSettings,
  DEFAULT_ACCOUNT_SETTINGS,
  type AccountSettings,
  fetchDuplicates,
  analyzeDuplicates,
  dismissDuplicates,
  restoreDuplicates,
  markDuplicates,
  type DuplicatesState,
  type DuplicateGroup,
  fetchAccountData,
  fetchCachedTicketSummaries,
  fetchTiers,
  fetchModels,
  deleteQbrSlide,
  fetchQbrHistory,
  fetchSchedules,
  generateSummary,
  shareQbrSlide,
  streamQbrSlides,
  type QbrStepStatus,
  type QbrHistoryEntry,
  type Account,
  type AccountData,
  type AccountGroup,
  type Issue,
  type TicketSummary,
  type LlmModel,
} from "@/lib/api";
import DownloadMenu from "@/components/DownloadMenu";
import ShareButton from "@/components/ShareButton";
import ScheduleModal from "@/components/ScheduleModal";
import AccountPicker from "@/components/AccountPicker";
import OptionPicker from "@/components/OptionPicker";
import { downloadCsv, downloadPdf, emailReport, slackReport } from "@/lib/downloads";

const OPEN_STATES = ["new", "waiting_on_you", "on_hold", "waiting_on_customer"];

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs uppercase tracking-wider mr-0.5" style={{ color: "var(--text-caption)" }}>{label}</span>
      {children}
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-pressed={active}
      style={
        active
          ? { background: "rgba(0,109,221,0.12)", border: "1px solid rgba(0,109,221,0.55)", color: "var(--text-primary)" }
          : { background: "transparent", border: "1px solid var(--border)", color: "var(--text-caption)" }
      }
      className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full transition-colors hover:border-[var(--border-hover)]"
    >
      {children}
    </button>
  );
}

function toSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/['']/g, "")         // Dick's → dicks
    .replace(/[()[\]{}]/g, "")    // remove brackets
    .replace(/[^a-z0-9]+/g, "-")  // remaining non-alphanumeric → hyphen
    .replace(/^-+|-+$/g, "");     // trim leading/trailing hyphens
}

const PRIORITY_LABELS: Record<string, string> = {
  urgent: "Sev 1",
  high: "Sev 2",
  medium: "Sev 3",
  low: "Sev 4",
  none: "None",
};

function getStateLabels(accountName?: string): Record<string, string> {
  return {
    new: "New",
    waiting_on_you: "Waiting on LangChain",
    on_hold: "On Hold",
    waiting_on_customer: accountName ? `Waiting on ${accountName}` : "Waiting on Customer",
    closed: "Closed",
    resolved: "Resolved",
  };
}

const SORT_OPTIONS = [
  { value: "priority", label: "Priority" },
  { value: "state", label: "State" },
  { value: "created", label: "Created Date" },
];

const PRIORITY_ORDER: Record<string, number> = {
  urgent: 0,
  high: 1,
  medium: 2,
  low: 3,
  none: 4,
};

const STATE_ORDER: Record<string, number> = {
  waiting_on_you: 0,
  new: 1,
  on_hold: 2,
  waiting_on_customer: 3,
};

const PERIOD_LABELS: Record<string, string> = {
  "7d": "7d", "1m": "1mo", "3m": "3mo", "6m": "6mo", "1y": "1yr",
};

function formatResolutionTime(hours: number): string {
  const weeks = Math.floor(hours / 40);
  const afterWeeks = hours - weeks * 40;
  const days = Math.floor(afterWeeks / 8);
  const remainingHours = Math.round(afterWeeks - days * 8);
  if (weeks > 0) return days > 0 ? `${weeks}w ${days}d` : `${weeks}w`;
  if (days > 0) return remainingHours > 0 ? `${days}d ${remainingHours}h` : `${days}d`;
  return `${remainingHours}h`;
}

function sortIssues(issues: Issue[], sortBy: string, sortOrder: "asc" | "desc"): Issue[] {
  const dir = sortOrder === "desc" ? -1 : 1;
  return [...issues].sort((a, b) => {
    if (sortBy === "state") {
      return dir * ((STATE_ORDER[a.state] ?? 99) - (STATE_ORDER[b.state] ?? 99));
    }
    if (sortBy === "created") {
      return dir * (new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    }
    if (sortBy === "priority") {
      return dir * ((PRIORITY_ORDER[a.priority] ?? 99) - (PRIORITY_ORDER[b.priority] ?? 99));
    }
    return 0;
  });
}

const Logo = () => (
  <svg width="32" height="32" viewBox="0 0 128 128" fill="none" xmlns="http://www.w3.org/2000/svg" className="flex-shrink-0">
    <path d="M40.1024 85.0722C47.6207 77.5537 51.8469 67.3453 51.8469 56.7136C51.8469 46.0818 47.617 35.8734 40.1024 28.355L11.7446 0C4.22995 7.5185 0 17.7269 0 28.3586C0 38.9903 4.22995 49.1987 11.7446 56.7172L40.0987 85.0722H40.1024Z" fill="#006ddd" />
    <path d="M99.4385 87.698C91.9239 80.1832 81.7121 75.9531 71.0844 75.9531C60.4566 75.9531 50.2448 80.1832 42.7266 87.698L71.0844 116.057C78.599 123.571 88.8107 127.802 99.4421 127.802C110.074 127.802 120.282 123.571 127.8 116.057L99.4421 87.698H99.4385Z" fill="#006ddd" />
    <path d="M11.8146 115.987C19.3329 123.502 29.541 127.732 40.1724 127.732V87.6289H0.0664062C0.0700559 98.2606 4.29635 108.469 11.8146 115.987Z" fill="#006ddd" />
    <path d="M110.387 45.7684C102.869 38.2535 92.6608 34.0198 82.0258 34.0234C71.3943 34.0234 61.1863 38.2535 53.668 45.772L82.0258 74.1306L110.387 45.7684Z" fill="#006ddd" />
  </svg>
);

const QBR_STEPS: { id: string; label: string }[] = [
  { id: "fetch",    label: "Fetching ticket data" },
  { id: "insights", label: "Generating AI insights" },
  { id: "hex",      label: "Fetching usage data" },
  { id: "roadmap",  label: "Finding roadmap items" },
  { id: "slides",   label: "Creating slide deck" },
  { id: "chart",    label: "Generating metrics chart" },
  { id: "share",    label: "Sharing with you" },
];

export default function Home() {
  const [configured, setConfigured] = useState(false);
  const [tiers, setTiers] = useState<string[]>([]);
  const [selectedTier, setSelectedTier] = useState("Premium");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null);
  const [accountData, setAccountData] = useState<AccountData | null>(null);
  const [dataUpdatedAt, setDataUpdatedAt] = useState<Date | null>(null);
  const [loading, setLoading] = useState(false);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProvider, setSelectedProvider] = useState("");
  const [selectedModelName, setSelectedModelName] = useState("");
  const [models, setModels] = useState<LlmModel[]>([]);
  const [period, setPeriod] = useState("6m");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState("priority");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");
  const [selectedStates, setSelectedStates] = useState<string[]>(OPEN_STATES);
  // Per-account sidebar settings (server-side, remembered per account): linked Linear/GitHub
  // IDs (off by default) and possible-duplicate flagging (on by default).
  const [accountSettings, setAccountSettings] = useState<AccountSettings>(DEFAULT_ACCOUNT_SETTINGS);
  const [settingsLoadedFor, setSettingsLoadedFor] = useState<string | null>(null);
  const selectedAccountId = selectedAccount?.id;
  const showLinkedIds = accountSettings.show_linked_ids;
  useEffect(() => {
    setAccountSettings(DEFAULT_ACCOUNT_SETTINGS);
    setSettingsLoadedFor(null);
    if (!selectedAccountId) return;
    let cancelled = false;
    fetchAccountSettings(selectedAccountId)
      .then((s) => { if (!cancelled) setAccountSettings(s); })
      .catch(() => {}) // non-fatal: defaults
      .finally(() => { if (!cancelled) setSettingsLoadedFor(selectedAccountId); });
    return () => { cancelled = true; };
  }, [selectedAccountId]);

  const selectedAccountIdRef = useRef(selectedAccountId);
  selectedAccountIdRef.current = selectedAccountId;

  function handleAccountSettingChange(key: keyof AccountSettings, value: boolean) {
    const accountId = selectedAccountId;
    if (!accountId) return;
    setAccountSettings((s) => ({ ...s, [key]: value }));
    updateAccountSettings(accountId, { [key]: value }).catch((err) => {
      // Roll back only if the user is still on that account
      if (selectedAccountIdRef.current === accountId) setAccountSettings((s) => ({ ...s, [key]: !value }));
      setError(err.message);
    });
  }

  // Account-group view: member accounts toggled off (same on/off chips as the status filter)
  const [hiddenMembers, setHiddenMembers] = useState<string[]>([]);
  const [ticketSummaries, setTicketSummaries] = useState<Record<number, TicketSummary | null>>({});
  const [accountSummary, setAccountSummary] = useState<string | null>(null);
  const [summaryGeneratedAt, setSummaryGeneratedAt] = useState<Date | null>(null);
  // Account + model whose summary pipeline last finished (success or error). The duplicates
  // AI pass waits for this to be the selected account and uses that model's summaries.
  const [summariesDoneFor, setSummariesDoneFor] = useState<{ accountId: string; model: string } | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);



  const [slackChannelName, setSlackChannelName] = useState<string | null>(null);
  const [slackChannelId, setSlackChannelId] = useState<string | null>(null);
  const [slackAvailableChannels, setSlackAvailableChannels] = useState<{ id: string; name: string }[]>([]);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleOpenToNewQbr, setScheduleOpenToNewQbr] = useState(false);
  const [qbrOpen, setQbrOpen] = useState(false);
  const [qbrScheduleCount, setQbrScheduleCount] = useState<number | null>(null);
  const [qbrHistory, setQbrHistory] = useState<QbrHistoryEntry[] | null>(null);
  const [qbrHistoryLoading, setQbrHistoryLoading] = useState(false);
  const [qbrGenerating, setQbrGenerating] = useState(false);
  const [qbrSteps, setQbrSteps] = useState<Record<string, QbrStepStatus>>({});
  const [qbrError, setQbrError] = useState<string | null>(null);
  const [qbrTemplateType, setQbrTemplateType] = useState<"full_deck" | "support_highlights">("full_deck");
  const [qbrConfirmRegenerate, setQbrConfirmRegenerate] = useState(false);
  const [qbrDeletingMonth, setQbrDeletingMonth] = useState<string | null>(null);
  const [qbrConfirmDeleteMonth, setQbrConfirmDeleteMonth] = useState<string | null>(null);

  const qbrContainerRef = useRef<HTMLDivElement>(null);

  const runQbrGeneration = useCallback(async (account: Account, templateType: "full_deck" | "support_highlights" = "support_highlights") => {
    setQbrSteps({});
    setQbrError(null);
    setQbrGenerating(true);
    setQbrOpen(true);
    try {
      await streamQbrSlides(
        account.id,
        account.name,
        (step, _label, status) => setQbrSteps(prev => ({ ...prev, [step]: status })),
        templateType,
        modelRef.current,
      );
      const updated = await fetchQbrHistory(account.id, account.name);
      setQbrHistory(updated);
      const newEntry = updated.find(e => e.is_current);
      if (newEntry?.slide?.url) window.open(newEntry.slide.url, "_blank");
    } catch (e) {
      setQbrError(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setQbrGenerating(false);
    }
  }, []);

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (qbrContainerRef.current && !qbrContainerRef.current.contains(e.target as Node)) {
        setQbrOpen(false);
        setQbrConfirmDeleteMonth(null);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  // Derived: combined model ID sent to the backend (provider:model format)
  const selectedModel = selectedModelName;

  // Possible-duplicate groups (link + manual immediately; AI pass once ticket summaries are in)
  const [dupes, setDupes] = useState<DuplicatesState | null>(null);
  const [dupesOnly, setDupesOnly] = useState(false);
  const summariesSettled = !summaryLoading && summariesDoneFor?.accountId === selectedAccountId;
  // Duplicates read cached ticket summaries per model: follow the model the summaries were
  // made with (switching model doesn't re-run the summaries, so the new one has none yet)
  const dupesModel = summariesSettled && summariesDoneFor ? summariesDoneFor.model : selectedModel;
  const flagDuplicates = settingsLoadedFor === selectedAccountId && accountSettings.flag_duplicates;
  useEffect(() => {
    setDupes(null);
    setDupesOnly(false);
  }, [selectedAccountId, flagDuplicates]);
  useEffect(() => {
    // Refetches (summaries settled, Refresh) keep the current groups and the
    // "Showing possible duplicates" view until the new result arrives
    if (!selectedAccountId || !accountData || !flagDuplicates) return;
    let cancelled = false;
    fetchDuplicates(selectedAccountId, dupesModel)
      .then((d) => { if (!cancelled) setDupes(d); })
      .catch(() => {}); // non-fatal: no grouping
    return () => { cancelled = true; };
  }, [selectedAccountId, accountData, dupesModel, summariesSettled, flagDuplicates]);
  useEffect(() => {
    if (!selectedAccountId || !flagDuplicates || dupes?.ai_status !== "pending" || !summariesSettled) return;
    let cancelled = false;
    analyzeDuplicates(selectedAccountId, dupesModel)
      .then((d) => { if (!cancelled) setDupes(d); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [selectedAccountId, flagDuplicates, dupes?.ai_status, summariesSettled, dupesModel]);

  async function applyDupes(request: Promise<DuplicatesState>) {
    try {
      setDupes(await request);
    } catch (err) {
      setError((err as Error).message);
      throw err;
    }
  }
  // Refs so pipeline callbacks always see current model/period without stale closures
  const modelRef = useRef(selectedModel);
  useEffect(() => { modelRef.current = selectedModel; }, [selectedModel]);
  const periodRef = useRef(period);
  useEffect(() => { periodRef.current = period; }, [period]);

  // Abort controller for any in-flight summary pipeline (ticket SSE + account summary)
  const summaryAbortRef = useRef<AbortController | null>(null);

  // Load available tiers and models once on mount
  useEffect(() => {
    fetchTiers()
      .then((data) => setTiers(data))
      .catch(() => {}); // non-fatal — tier selector will still show default
    fetchModels()
      .then((data) => {
        setModels(data);
        if (data.length > 0 && !selectedProvider) {
          const first = data[0];
          setSelectedProvider(first.provider);
          setSelectedModelName(first.id);
        }
      })
      .catch(() => {}); // non-fatal — model selector will be empty
  }, []);

  // Load accounts when tier changes — auto-select deep link account on first load
  useEffect(() => {
    setAccountsLoading(true);
    setSelectedAccount(null);
    setAccountData(null);
    fetchAccounts(selectedTier)
      .then((data) => {
        setAccounts(data);
        const param = new URLSearchParams(window.location.search).get("account");
        if (param) {
          const match = data.find((a) => toSlug(a.name) === param.toLowerCase());
          if (match) {
            setSelectedAccount(match);
            setConfigured(true);
          }
        }
      })
      .catch((err) => setError(err.message))
      .finally(() => setAccountsLoading(false));
  }, [selectedTier]);

  // Run the summary pipeline: agent generates ticket summaries + account summary.
  // Polls cached ticket summaries every 2s while the agent runs so cards appear
  // progressively as parallel ticket summaries complete. Does NOT reset ticket
  // summaries to null — callers pre-populate them (initial load shows cached;
  // regenerate keeps showing old summaries until new ones arrive).
  const runSummaryPipeline = useCallback(
    async (account: Account, openNumbers: number[], force: boolean, signal: AbortSignal) => {
      const pipelineModel = modelRef.current;
      setAccountSummary(null);
      setSummaryGeneratedAt(null);
      setSummaryLoading(true);
      setSummaryError(null);
      setSummariesDoneFor(null);

      // Poll for ticket summaries while the agent runs
      const applysummaries = (summaries: Record<number, TicketSummary>) => {
        setTicketSummaries((prev) => {
          const next = { ...prev };
          for (const [num, summary] of Object.entries(summaries)) {
            next[Number(num)] = summary;
          }
          return next;
        });
      };
      const pollInterval = setInterval(() => {
        if (signal.aborted) { clearInterval(pollInterval); return; }
        fetchCachedTicketSummaries(account.id, modelRef.current)
          .then((s) => { if (!signal.aborted) applysummaries(s); })
          .catch(() => {});
      }, 2000);

      try {
        const text = await generateSummary(account.id, account.name, modelRef.current, periodRef.current, force, signal);
        if (!signal.aborted) {
          setAccountSummary(text);
          setSummaryGeneratedAt(new Date());
        }
      } catch (err) {
        if (!signal.aborted) setSummaryError(err instanceof Error ? err.message : "Failed to generate summary");
      } finally {
        clearInterval(pollInterval);
        if (!signal.aborted) {
          setSummaryLoading(false);
          setSummariesDoneFor({ accountId: account.id, model: pipelineModel });
        }
      }

      if (signal.aborted) return;

      // Final fetch to catch any summaries that completed between the last poll and agent finish
      try {
        const summaries = await fetchCachedTicketSummaries(account.id, modelRef.current);
        if (!signal.aborted) {
          setTicketSummaries((prev) => {
            const next = { ...prev };
            for (const [num, summary] of Object.entries(summaries)) {
              next[Number(num)] = summary;
            }
            for (const n of openNumbers) {
              if (next[n] === null) next[n] = { summary: "", next_steps: "" };
            }
            return next;
          });
        }
      } catch {
        if (!signal.aborted) {
          setTicketSummaries((prev) => {
            const next = { ...prev };
            for (const n of openNumbers) {
              if (next[n] === null) next[n] = { summary: "", next_steps: "" };
            }
            return next;
          });
        }
      }
    },
    [] // eslint-disable-line react-hooks/exhaustive-deps
  );

  // Load account data when selection changes or refresh is triggered
  const loadAccountData = useCallback(
    (account: Account, force = false) => {
      summaryAbortRef.current?.abort();
      const abortCtrl = new AbortController();
      summaryAbortRef.current = abortCtrl;

      setLoading(true);
      setError(null);
      setAccountData(null);
      setDataUpdatedAt(null);
      setTicketSummaries({});
      setAccountSummary(null);
      setSummaryLoading(false);
      setSummaryError(null);

      fetchAccountData(account.id, account.name, period, force)
        .then((data) => {
          setAccountData(data);
          setDataUpdatedAt(new Date());
          const openNumbers = data.open_issues.map((i) => i.number);

          // Initialise all tickets as loading (null), then immediately show
          // any previously cached summaries so returning visits feel instant.
          // The pipeline will fill in the rest progressively via polling.
          const initial: Record<number, TicketSummary | null> = {};
          for (const n of openNumbers) initial[n] = null;
          setTicketSummaries(initial);
          fetchCachedTicketSummaries(account.id, modelRef.current)
            .then((cached) => {
              if (!abortCtrl.signal.aborted) {
                setTicketSummaries((prev) => {
                  const next = { ...prev };
                  for (const [num, summary] of Object.entries(cached)) {
                    next[Number(num)] = summary;
                  }
                  return next;
                });
              }
            })
            .catch(() => {});

          runSummaryPipeline(account, openNumbers, false, abortCtrl.signal);
        })
        .catch((err) => setError(err.message))
        .finally(() => setLoading(false));

      return () => abortCtrl.abort();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [period, runSummaryPipeline]
  );

  useEffect(() => {
    if (configured && selectedAccount) {
      return loadAccountData(selectedAccount);
    }
  }, [configured, selectedAccount, loadAccountData]);

  useEffect(() => {
    if (!selectedAccount) {
      setSlackChannelName(null);
      setSlackChannelId(null);
      setSlackAvailableChannels([]);
      return;
    }
    fetch(`/api/accounts/${selectedAccount.id}/slack-channel`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        setSlackChannelName(d?.channel_name ?? null);
        setSlackChannelId(d?.channel_id ?? null);
        setSlackAvailableChannels(d?.available_channels ?? []);
      })
      .catch(() => { setSlackChannelName(null); setSlackChannelId(null); setSlackAvailableChannels([]); });
  }, [selectedAccount]);

  function handleRefresh() {
    if (selectedAccount) loadAccountData(selectedAccount, true);
  }

  function handleAccountSelect(account: Account) {
    setSelectedAccount(account);
    setSearchQuery("");
    setSortBy("priority");
    setSelectedStates(OPEN_STATES);
    setHiddenMembers([]);
    setDupesOnly(false);
    setQbrOpen(false);
    setQbrGenerating(false);
    setQbrSteps({});
    setQbrError(null);
    setQbrTemplateType("full_deck");
    setQbrHistory(null);
    setQbrScheduleCount(null);
    setQbrConfirmDeleteMonth(null);
    setQbrDeletingMonth(null);
    window.history.replaceState(null, "", `/customers?account=${toSlug(account.name)}`);
  }

  // After an account group is created/removed: reload the list, and keep the
  // selection meaningful — move to the new group if the open account was just
  // grouped into it, or clear it if the open group was just ungrouped.
  function handleGroupsChanged(created: AccountGroup | null, removedId?: string) {
    fetchAccounts(selectedTier)
      .then((data) => {
        setAccounts(data);
        if (!selectedAccount) return;
        const group = created && data.find((a) => a.id === created.id);
        if (group && created.members.some((m) => m.id === selectedAccount.id)) {
          if (configured) handleAccountSelect(group);
          else setSelectedAccount(group);
        } else if (removedId && selectedAccount.id === removedId) {
          setSelectedAccount(null);
          setAccountData(null);
          window.history.replaceState(null, "", "/customers");
        }
      })
      .catch((err) => setError(err.message));
  }

  function handleRegenerate() {
    if (!selectedAccount || !accountData) return;
    summaryAbortRef.current?.abort();
    const abortCtrl = new AbortController();
    summaryAbortRef.current = abortCtrl;
    const openNumbers = accountData.open_issues.map((i) => i.number);
    runSummaryPipeline(selectedAccount, openNumbers, true, abortCtrl.signal);
  }

  // ticket number -> its possible-duplicate group
  // Only tickets in the loaded list: /duplicates reads Pylon fresh, while the ticket list can
  // come from a cached payload, so a group may mention tickets this page doesn't have
  const loadedTickets = new Set((accountData?.open_issues ?? []).map((i) => i.number));
  const dupeGroups = (dupes?.groups ?? [])
    .map((g) => ({ ...g, tickets: g.tickets.filter((n) => loadedTickets.has(n)), allTickets: g.tickets }))
    .filter((g) => g.tickets.length >= 2);
  const dupeGroupOf = new Map(dupeGroups.flatMap((g) => g.tickets.map((n) => [n, g] as const)));

  // Filtering + sorting open issues
  // Only offer per-account labels/filter when the tickets say which account they're from.
  // Members are read from the reloaded accounts list (not selectedAccount) so a
  // colour change shows immediately without re-selecting — and re-fetching — the account.
  const groupMembers = (accounts.find((a) => a.id === selectedAccount?.id) ?? selectedAccount)?.members;
  const members =
    groupMembers && accountData?.open_issues.some((i) => i.account_id)
      ? memberLabels(groupMembers)
      : null;
  const filteredIssues: Issue[] = accountData
    ? sortIssues(
        accountData.open_issues.filter((issue) => {
          const matchesState =
            selectedStates.length === 0 ||
            selectedStates.includes(issue.state);
          // Like the status chips, turning every account off means "no filter"
          const matchesMember =
            !members ||
            hiddenMembers.length >= members.size ||
            !issue.account_id ||
            !hiddenMembers.includes(issue.account_id);
          const member = members && issue.account_id ? members.get(issue.account_id) : undefined;
          const q = searchQuery.toLowerCase();
          const matchesSearch =
            !q ||
            issue.title.toLowerCase().includes(q) ||
            String(issue.number).includes(q) ||
            issue.tags.some((t) => t.toLowerCase().includes(q)) ||
            (issue.external_issues ?? []).some((ei) => ei.display_id?.toLowerCase().includes(q)) ||
            !!member?.fullName.toLowerCase().includes(q);
          const matchesDupes = !dupesOnly || dupeGroupOf.has(issue.number);
          return matchesState && matchesMember && matchesSearch && matchesDupes;
        }),
        sortBy,
        sortOrder
      )
    : [];

  const totalRaised =
    accountData?.monthly_metrics.reduce((sum, m) => sum + m.tickets_raised, 0) ?? null;

  const totalClosed =
    accountData?.monthly_metrics.reduce((sum, m) => sum + m.closed_tickets, 0) ?? null;

  // ── Setup screen ──────────────────────────────────────────────────────────
  if (!configured) {
    const setupAccount = selectedAccount ?? (accounts.length > 0 ? accounts[0] : null);

    return (
      <div
        className="min-h-screen flex items-center justify-center px-4"
        style={{ background: "var(--bg-base)" }}
      >
        <div
          className="w-full max-w-sm rounded-xl px-8 py-8"
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
        >
          <div className="flex items-center gap-2.5 mb-6">
            <Logo />
            <span className="font-semibold text-base" style={{ color: "var(--text-primary)" }}>
              Support Highlights
            </span>
          </div>

          <h1 className="text-xl font-bold mb-1" style={{ color: "var(--text-primary)" }}>
            Welcome
          </h1>
          <p className="text-sm mb-6" style={{ color: "var(--text-muted)" }}>
            Choose an account and settings to get started.
          </p>

          {accountsLoading ? (
            <div className="flex items-center gap-2 py-4" style={{ color: "var(--text-muted)" }}>
              <Loader2 size={16} className="animate-spin" />
              <span className="text-sm">Loading accounts...</span>
            </div>
          ) : error ? (
            <p className="text-sm mb-4 text-red-500">{error}</p>
          ) : (
            <div className="flex flex-col gap-4">
              <div>
                <label className="block text-xs uppercase tracking-wider mb-1.5 font-medium" style={{ color: "var(--text-muted)" }}>
                  Support Tier
                </label>
                <OptionPicker
                  options={tiers.length > 0
                    ? tiers.map((t) => ({ value: t, label: t }))
                    : [{ value: "Premium", label: "Premium" }]}
                  value={selectedTier}
                  onChange={setSelectedTier}
                />
              </div>

              <div>
                <label className="block text-xs uppercase tracking-wider mb-1.5 font-medium" style={{ color: "var(--text-muted)" }}>
                  Account
                </label>
                <AccountPicker
                  accounts={accounts}
                  selected={setupAccount}
                  onSelect={setSelectedAccount}
                  onGroupsChanged={handleGroupsChanged}
                />
              </div>

              <div>
                <label className="block text-xs uppercase tracking-wider mb-1.5 font-medium" style={{ color: "var(--text-muted)" }}>
                  Time Period
                </label>
                <OptionPicker
                  options={[
                    { value: "7d", label: "7 Days" },
                    { value: "1m", label: "1 Month" },
                    { value: "3m", label: "3 Months" },
                    { value: "6m", label: "6 Months" },
                    { value: "1y", label: "1 Year" },
                  ]}
                  value={period}
                  onChange={setPeriod}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs uppercase tracking-wider mb-1.5 font-medium" style={{ color: "var(--text-muted)" }}>
                    Provider
                  </label>
                  <OptionPicker
                    options={[...new Set(models.map((m) => m.provider))].map((p) => ({
                      value: p,
                      label: p.charAt(0).toUpperCase() + p.slice(1),
                    }))}
                    value={selectedProvider}
                    onChange={(p) => {
                      setSelectedProvider(p);
                      const firstForProvider = models.find((m) => m.provider === p);
                      if (firstForProvider) setSelectedModelName(firstForProvider.id);
                    }}
                  />
                </div>
                <div>
                  <label className="block text-xs uppercase tracking-wider mb-1.5 font-medium" style={{ color: "var(--text-muted)" }}>
                    Model
                  </label>
                  <OptionPicker
                    options={models.filter((m) => m.provider === selectedProvider).map((m) => ({ value: m.id, label: m.label }))}
                    value={selectedModelName}
                    onChange={setSelectedModelName}
                  />
                </div>
              </div>

              <button
                onClick={() => {
                  if (!setupAccount) return;
                  setSelectedAccount(setupAccount);
                  setConfigured(true);
                  window.history.replaceState(null, "", `/customers?account=${toSlug(setupAccount.name)}`);
                }}
                disabled={!setupAccount}
                className="w-full flex items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-medium text-white bg-[#006ddd] hover:bg-[#0058b8] disabled:opacity-40 disabled:cursor-not-allowed transition-colors mt-2"
              >
                View Dashboard
                <ChevronRight size={15} />
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  // Possible-duplicate groups render as one container at the position of their first
  // visible ticket (current sort order); a group with only one visible ticket shows
  // that ticket normally with a "hidden by filters" link back to the group.
  function revealDuplicateGroup(groupId: string) {
    setSelectedStates(OPEN_STATES);
    setSearchQuery("");
    setHiddenMembers([]);
    setDupesOnly(false);
    setTimeout(() => document.getElementById(`dupe-${groupId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
  }

  function renderTicketList() {
    const accountId = selectedAccountId;
    const allTickets = (accountData?.open_issues ?? []).map((i) => ({ number: i.number, title: i.title }));
    const card = (issue: Issue, hiddenDuplicates?: number[], groupId?: string) => (
      <TicketCard
        key={issue.number}
        issue={issue}
        accountName={selectedAccount?.name}
        ticketSummary={ticketSummaries[issue.number]}
        memberAccount={members && issue.account_id ? members.get(issue.account_id) : undefined}
        showLinkedIds={showLinkedIds}
        duplicateCandidates={allTickets.filter((t) => t.number !== issue.number)}
        onMarkDuplicate={accountId && flagDuplicates ? (other) => applyDupes(markDuplicates(accountId, [issue.number, other], dupesModel)) : undefined}
        hiddenDuplicates={hiddenDuplicates}
        onRevealDuplicates={groupId ? () => revealDuplicateGroup(groupId) : undefined}
      />
    );
    const shownGroups = new Set<string>();
    return filteredIssues.flatMap((issue) => {
      const group = dupeGroupOf.get(issue.number);
      if (!group) return [card(issue)];
      const visible = filteredIssues.filter((i) => group.tickets.includes(i.number));
      if (visible.length < 2) {
        return [card(issue, group.tickets.filter((n) => n !== issue.number), group.id)];
      }
      if (shownGroups.has(group.id)) return [];
      shownGroups.add(group.id);
      return [
        <DuplicateGroupCard
          key={`dupe-${group.id}`}
          group={group}
          visibleCount={visible.length}
          onDismiss={() => (accountId ? applyDupes(dismissDuplicates(accountId, group.allTickets, dupesModel)) : Promise.resolve())}
        >
          {visible.map((i) => card(i))}
        </DuplicateGroupCard>,
      ];
    });
  }

  // ── Dashboard ──────────────────────────────────────────────────────────────
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        accounts={accounts}
        selected={selectedAccount}
        onSelect={handleAccountSelect}
        onGroupsChanged={handleGroupsChanged}
        onRefresh={handleRefresh}
        dataUpdatedAt={dataUpdatedAt}
        selectedProvider={selectedProvider}
        selectedModelName={selectedModelName}
        onProviderChange={(p) => {
          setSelectedProvider(p);
          const firstForProvider = models.find((m) => m.provider === p);
          if (firstForProvider) setSelectedModelName(firstForProvider.id);
        }}
        onModelChange={setSelectedModelName}
        models={models}
        period={period}
        onPeriodChange={setPeriod}
        onSetup={() => setConfigured(false)}
        tiers={tiers}
        selectedTier={selectedTier}
        onTierChange={setSelectedTier}
        accountSettings={selectedAccount ? accountSettings : null}
        onAccountSettingChange={handleAccountSettingChange}
      />

      {/* Main content */}
      <main className="flex-1 overflow-y-auto px-6 py-6" style={{ background: "var(--bg-base)" }}>
        {accountsLoading ? (
          <div className="flex items-center gap-2 mt-8" style={{ color: "var(--text-muted)" }}>
            <Loader2 size={18} className="animate-spin" />
            <span className="text-sm">Loading accounts...</span>
          </div>
        ) : !selectedAccount ? (
          <div className="flex flex-col items-center justify-center h-full min-h-[60vh] text-center px-6">
            <svg width="48" height="48" viewBox="0 0 128 128" fill="none" xmlns="http://www.w3.org/2000/svg" className="mb-5 opacity-30">
              <path d="M40.1024 85.0722C47.6207 77.5537 51.8469 67.3453 51.8469 56.7136C51.8469 46.0818 47.617 35.8734 40.1024 28.355L11.7446 0C4.22995 7.5185 0 17.7269 0 28.3586C0 38.9903 4.22995 49.1987 11.7446 56.7172L40.0987 85.0722H40.1024Z" fill="currentColor" />
              <path d="M99.4385 87.698C91.9239 80.1832 81.7121 75.9531 71.0844 75.9531C60.4566 75.9531 50.2448 80.1832 42.7266 87.698L71.0844 116.057C78.599 123.571 88.8107 127.802 99.4421 127.802C110.074 127.802 120.282 123.571 127.8 116.057L99.4421 87.698H99.4385Z" fill="currentColor" />
              <path d="M11.8146 115.987C19.3329 123.502 29.541 127.732 40.1724 127.732V87.6289H0.0664062C0.0700559 98.2606 4.29635 108.469 11.8146 115.987Z" fill="currentColor" />
              <path d="M110.387 45.7684C102.869 38.2535 92.6608 34.0198 82.0258 34.0234C71.3943 34.0234 61.1863 38.2535 53.668 45.772L82.0258 74.1306L110.387 45.7684Z" fill="currentColor" />
            </svg>
            <h2 className="text-lg font-semibold mb-2" style={{ color: "var(--text-primary)" }}>
              {accounts.length === 0
                ? `No ${selectedTier} accounts found`
                : "Select an account"}
            </h2>
            <p className="text-sm max-w-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
              {accounts.length === 0
                ? `There are no ${selectedTier.toLowerCase()} tier accounts in Pylon. Try a different support tier.`
                : `Choose a ${selectedTier.toLowerCase()} account from the sidebar to view open issues, metrics, and AI-generated summaries.`}
            </p>
          </div>
        ) : (
          <>
            {/* Page header */}
            <div className="mb-6 flex items-start justify-between">
              <div>
                <h1 className="text-xl font-bold" style={{ color: "var(--text-primary)" }}>
                  {selectedAccount.name}
                </h1>
                <p className="text-sm mt-0.5" style={{ color: "var(--text-muted)" }}>
                  Support Highlights
                </p>
              </div>
              {accountData && (
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setScheduleOpen(true)}
                    style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                    className="flex items-center gap-1.5 text-sm rounded px-3 py-1.5 hover:bg-[var(--bg-tertiary)] transition-colors focus:outline-none cursor-pointer print:hidden"
                  >
                    <Clock size={14} />
                    Schedule
                  </button>
                  <div ref={qbrContainerRef} className="relative print:hidden">
                    <button
                      onClick={async () => {
                        if (!selectedAccount) return;
                        setQbrOpen(o => !o);
                        if (!qbrHistory && !qbrHistoryLoading) {
                          setQbrHistoryLoading(true);
                          fetchQbrHistory(selectedAccount.id, selectedAccount.name)
                            .then(h => setQbrHistory(h))
                            .catch(() => setQbrHistory([]))
                            .finally(() => setQbrHistoryLoading(false));
                        }
                        if (qbrScheduleCount === null) {
                          fetchSchedules(selectedAccount.id)
                            .then(ss => setQbrScheduleCount(ss.filter(s => s.destination_type === "qbr").length))
                            .catch(() => setQbrScheduleCount(0));
                        }
                      }}
                      disabled={!selectedAccount}
                      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                      className="flex items-center gap-1.5 text-sm rounded px-3 py-1.5 hover:bg-[var(--bg-tertiary)] transition-colors focus:outline-none cursor-pointer disabled:opacity-50"
                    >
                      {qbrGenerating ? <Loader2 size={14} className="animate-spin" /> : <Presentation size={14} />}
                      QBR Slides
                    </button>

                    {qbrOpen && (
                      <div
                        className="absolute right-0 top-[calc(100%+4px)] z-50 rounded-lg p-3 w-72"
                        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}
                      >
                        <div className="flex items-center justify-between mb-3">
                          <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>QBR Slides</span>
                          <button onClick={() => setQbrOpen(false)} className="p-0.5 rounded hover:bg-[var(--bg-tertiary)] transition-colors" style={{ color: "var(--text-muted)" }}>
                            <X size={11} />
                          </button>
                        </div>

                        {/* Template picker */}
                        {!qbrGenerating && (
                          <div className="flex rounded overflow-hidden mb-3 text-xs" style={{ border: "1px solid var(--border)" }}>
                            {(["full_deck", "support_highlights"] as const).map((t) => (
                              <button
                                key={t}
                                onClick={() => setQbrTemplateType(t)}
                                className="flex-1 py-1 transition-colors cursor-pointer"
                                style={{
                                  background: qbrTemplateType === t ? "var(--accent)" : "transparent",
                                  color: qbrTemplateType === t ? "white" : "var(--text-muted)",
                                  fontWeight: qbrTemplateType === t ? 500 : 400,
                                }}
                              >
                                {t === "support_highlights" ? "Support Slides" : "Full Deck"}
                              </button>
                            ))}
                          </div>
                        )}

                        {/* Generation progress — shown while generating */}
                        {qbrGenerating && (
                          <div className="space-y-2 mb-3">
                            {QBR_STEPS.map(({ id, label }) => {
                              const status = qbrSteps[id] ?? "pending";
                              return (
                                <div key={id} className="flex items-center gap-2.5">
                                  <div className="shrink-0 w-4">
                                    {status === "done" ? (
                                      <CheckCircle2 size={14} className="text-green-500" />
                                    ) : status === "running" ? (
                                      <Loader2 size={14} className="animate-spin" style={{ color: "var(--accent)" }} />
                                    ) : (
                                      <Circle size={14} style={{ color: "var(--text-muted)", opacity: 0.35 }} />
                                    )}
                                  </div>
                                  <span className="text-xs" style={{ color: status === "pending" ? "var(--text-muted)" : "var(--text-primary)" }}>
                                    {label}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        )}

                        {qbrError && (
                          <div className="text-xs mb-3" style={{ color: "#ef4444" }}>{qbrError}</div>
                        )}

                        {/* QBR schedule indicator */}
                        {qbrScheduleCount !== null && (
                          <div
                            className="flex items-center justify-between mb-3 px-2 py-1.5 rounded cursor-pointer hover:bg-[var(--bg-tertiary)] transition-colors"
                            style={{ border: "1px solid var(--border)" }}
                            onClick={() => {
                              setQbrOpen(false);
                              setScheduleOpenToNewQbr(qbrScheduleCount === 0);
                              setScheduleOpen(true);
                            }}
                          >
                            <span className="text-xs italic" style={{ color: "var(--text-muted)" }}>
                              {qbrScheduleCount === 0
                                ? "No active QBR schedules"
                                : `${qbrScheduleCount} active QBR schedule${qbrScheduleCount > 1 ? "s" : ""}`}
                            </span>
                            <span className="text-xs font-medium" style={{ color: "var(--accent)" }}>
                              {qbrScheduleCount === 0 ? "Set up" : "View"} →
                            </span>
                          </div>
                        )}

                        {/* Month history list — hidden while generating */}
                        {!qbrGenerating && <div className="-mx-3">
                          {qbrHistoryLoading ? (
                            <div className="flex items-center justify-center py-4">
                              <Loader2 size={16} className="animate-spin" style={{ color: "var(--text-muted)" }} />
                            </div>
                          ) : (qbrHistory ?? []).filter(entry => entry.is_current || entry.slide).map(entry => (
                            <div key={entry.month} className="flex items-center justify-between px-3 py-2">
                              <div>
                                <div className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{entry.month_label}</div>
                                {entry.slide && (
                                  <div className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
                                    Generated {new Date(entry.slide.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                                  </div>
                                )}
                              </div>
                              <div className="flex items-center gap-2 shrink-0">
                                {entry.slide && !entry.is_current && (
                                  qbrConfirmDeleteMonth === entry.month ? (
                                    <button
                                      disabled={qbrDeletingMonth === entry.month}
                                      onClick={async () => {
                                        if (!selectedAccount) return;
                                        setQbrDeletingMonth(entry.month);
                                        try {
                                          await deleteQbrSlide(selectedAccount.id, entry.month);
                                          setQbrHistory(h => h ? h.filter(e => e.month !== entry.month) : h);
                                        } finally {
                                          setQbrDeletingMonth(null);
                                          setQbrConfirmDeleteMonth(null);
                                        }
                                      }}
                                      className="flex items-center gap-1 text-xs rounded px-2 py-1 transition-opacity hover:opacity-80 disabled:opacity-40 cursor-pointer"
                                      style={{ background: "#ef4444", color: "white" }}
                                    >
                                      {qbrDeletingMonth === entry.month ? <Loader2 size={11} className="animate-spin" /> : "Confirm"}
                                    </button>
                                  ) : (
                                    <button
                                      onClick={() => setQbrConfirmDeleteMonth(entry.month)}
                                      className="p-1 rounded transition-colors hover:bg-[var(--bg-tertiary)] cursor-pointer"
                                      style={{ color: "var(--text-muted)" }}
                                      title="Delete"
                                    >
                                      <Trash2 size={13} />
                                    </button>
                                  )
                                )}
                                {entry.slide && (
                                  <button
                                    onClick={() => {
                                      if (!selectedAccount || !entry.slide) return;
                                      shareQbrSlide(selectedAccount.id, entry.slide.pres_id).catch(() => {});
                                      window.open(entry.slide.url, "_blank");
                                    }}
                                    className="flex items-center gap-1 text-xs rounded px-2 py-1 transition-opacity hover:opacity-80 cursor-pointer"
                                    style={{ background: "var(--accent)", color: "white" }}
                                  >
                                    <ExternalLink size={11} />
                                    Open
                                  </button>
                                )}
                                {entry.is_current && (
                                  <button
                                    disabled={qbrGenerating}
                                    onClick={() => {
                                      if (!selectedAccount) return;
                                      if (entry.slide) { setQbrConfirmRegenerate(true); return; }
                                      runQbrGeneration(selectedAccount, qbrTemplateType);
                                    }}
                                    className="flex items-center gap-1 text-xs rounded px-2 py-1 transition-opacity hover:opacity-80 disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                                    style={entry.slide
                                      ? { background: "var(--bg-secondary)", border: "1px solid var(--text-muted)", color: "var(--text-primary)" }
                                      : { background: "var(--accent)", color: "white" }}
                                    title={entry.slide ? "Regenerate" : "Generate"}
                                  >
                                    {qbrGenerating ? <Loader2 size={11} className="animate-spin" /> : entry.slide ? <RefreshCw size={13} /> : "Generate"}
                                  </button>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>}
                      </div>
                    )}
                  </div>
                  <ShareButton
                    onSlackReport={(channelId, sections, options) => slackReport(selectedAccount.id, selectedAccount.name, period, channelId, sections, options)}
                    onEmailReport={(email, sections, options) => emailReport(selectedAccount.id, selectedAccount.name, email, period, sortBy, sortOrder, sections, selectedModel, options)}
                    channelName={slackChannelName}
                    channelId={slackChannelId}
                    availableChannels={slackAvailableChannels}
                  />
                  <DownloadMenu
                    onDownloadPdf={(sections, options) => downloadPdf(selectedAccount.id, selectedAccount.name, period, sortBy, sortOrder, sections, selectedModel, options)}
                    onDownloadCsv={async (sections, options) => {
                      // CSV is built client-side: use the groups on screen, else the latest saved
                      // result (like PDF/email, never waits on a fresh AI pass)
                      let groups: DuplicateGroup[] | undefined;
                      if (options.duplicates) {
                        try {
                          groups = dupes?.ai_status === "done"
                            ? dupeGroups
                            : (await fetchDuplicates(selectedAccount.id, dupesModel, true)).groups;
                        } catch (err) {
                          setError(`Couldn't load possible duplicates for the CSV: ${(err as Error).message}`);
                          return;
                        }
                      }
                      downloadCsv(selectedAccount.name, period, accountData, filteredIssues, ticketSummaries, sections, members, options, groups);
                    }}
                  />
                </div>
              )}
            </div>

            {error && (
              <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/40 rounded-lg px-4 py-3 mb-4 text-red-600 dark:text-red-300 text-sm">
                {error}
              </div>
            )}

            {loading ? (
              <div className="flex items-center gap-2 mt-8" style={{ color: "var(--text-muted)" }}>
                <Loader2 size={18} className="animate-spin" />
                <span className="text-sm">Loading account data...</span>
              </div>
            ) : accountData ? (
              <>
                {/* Metric cards */}
                <div className="flex flex-col gap-3 mb-5">
                  <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
                    <MetricCard label="Open Issues (Current)" value={accountData.open_issues.length} />
                    <MetricCard label={`Tickets Raised (${PERIOD_LABELS[period] ?? period})`} value={totalRaised} />
                    <MetricCard label={`Tickets Closed (${PERIOD_LABELS[period] ?? period})`} value={totalClosed} />
                  </div>
                  <div className={`grid grid-cols-2 gap-3 ${accountData.csat !== null ? "lg:grid-cols-4" : "lg:grid-cols-3"}`}>
                    <MetricCard
                      label={`Avg Time to First Response (${PERIOD_LABELS[period] ?? period})`}
                      value={accountData.avg_response_time !== null ? accountData.avg_response_time.toFixed(1) : null}
                      unit="hrs"
                    />
                    <MetricCard
                      label={`Avg Resolution Time (${PERIOD_LABELS[period] ?? period})`}
                      value={accountData.avg_resolution_time !== null ? formatResolutionTime(accountData.avg_resolution_time) : null}
                    />
                    <MetricCard
                      label={`SLA Compliance (${PERIOD_LABELS[period] ?? period})`}
                      value={accountData.sla_compliance_pct !== null ? String(accountData.sla_compliance_pct) : null}
                      unit="%"
                    />
                    {accountData.csat !== null && (
                      <MetricCard
                        label="CSAT"
                        value={accountData.csat % 1 === 0 ? String(accountData.csat) : accountData.csat.toFixed(1)}
                        unit="/ 5"
                      />
                    )}
                  </div>
                </div>

                {/* Trend chart */}
                <div className="mb-5">
                  <TrendChart data={accountData.monthly_metrics} period={period} />
                </div>

                {/* Breakdown row */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-5">
                  {(["priority_breakdown", "state_breakdown", "disposition_breakdown"] as const).map((key) => (
                    <div
                      key={key}
                      className="rounded-lg px-4 py-4"
                      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
                    >
                      <h3 className="text-xs uppercase tracking-wider font-medium mb-3" style={{ color: "var(--text-muted)" }}>
                        {{ priority_breakdown: "Priority Breakdown", state_breakdown: "State Breakdown", disposition_breakdown: "Disposition Breakdown" }[key]}
                      </h3>
                      {Object.keys(accountData[key]).length === 0 ? (
                        <p className="text-sm" style={{ color: "var(--text-caption)" }}>No data</p>
                      ) : (
                        <div className="space-y-1.5">
                          {Object.entries(accountData[key]).map(([label, count]) => (
                            <div key={label} className="flex items-center justify-between">
                              <span className="text-sm" style={{ color: "var(--text-primary)" }}>
                                {key === "state_breakdown"
                                  ? (getStateLabels(selectedAccount?.name)[label] ?? label.replace(/_/g, " "))
                                  : key === "priority_breakdown"
                                    ? (PRIORITY_LABELS[label] ?? label.replace(/_/g, " "))
                                    : label}
                              </span>
                              <span className="text-sm font-mono" style={{ color: "var(--text-muted)" }}>
                                {count}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                {/* AI Summary */}
                <div className="mb-5">
                  <SummaryPanel
                    summary={accountSummary}
                    generatedAt={summaryGeneratedAt}
                    loading={summaryLoading}
                    error={summaryError}
                    onRegenerate={handleRegenerate}
                    ticketUrls={Object.fromEntries(
                      (accountData?.open_issues ?? [])
                        .filter((i) => i.portal_url)
                        .map((i) => [String(i.number), i.portal_url!])
                    )}
                  />
                </div>

                {/* Open tickets */}
                <div>
                  <h2 className="text-base font-semibold mb-3" style={{ color: "var(--text-primary)" }}>
                    Open Issues ({filteredIssues.length})
                  </h2>

                  {/* Filters row */}
                  <div className="flex flex-wrap items-center gap-2 mb-3 print:hidden" data-print-hide>
                    <input
                      type="text"
                      placeholder="Search tickets..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      style={{
                        background: "var(--bg-secondary)",
                        border: "1px solid var(--border)",
                        color: "var(--text-primary)",
                      }}
                      className="text-sm rounded px-3 py-1.5 focus:outline-none w-56 placeholder:text-[var(--text-caption)]"
                    />
                    <div className="flex items-center gap-0">
                      <select
                        value={sortBy}
                        onChange={(e) => setSortBy(e.target.value)}
                        style={{
                          background: "var(--bg-secondary)",
                          border: "1px solid var(--border)",
                          borderRight: "none",
                          color: "var(--text-primary)",
                        }}
                        className="text-sm rounded-l px-2 py-1.5 focus:outline-none cursor-pointer"
                      >
                        {SORT_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>Sort: {opt.label}</option>
                        ))}
                      </select>
                      <button
                        onClick={() => setSortOrder((o) => o === "asc" ? "desc" : "asc")}
                        title={sortOrder === "asc" ? "Ascending" : "Descending"}
                        style={{
                          background: "var(--bg-secondary)",
                          border: "1px solid var(--border)",
                          color: sortOrder === "desc" ? "var(--text-primary)" : "var(--text-muted)",
                        }}
                        className="px-2 py-1.5 rounded-r text-sm hover:bg-[var(--bg-tertiary)] transition-colors focus:outline-none cursor-pointer"
                      >
                        <ArrowUpDown size={13} style={{ transform: sortOrder === "desc" ? "scaleY(-1)" : "none" }} />
                      </button>
                    </div>

                    {/* Possible duplicates: a view toggle rather than a filter chip */}
                    {dupes && (dupeGroups.length > 0 || dupes.dismissed.length > 0 || dupes.ai_status === "pending") && (
                      <div className="flex items-center gap-3 ml-auto">
                        {dupes.ai_status === "pending" && (
                          <span className="flex items-center gap-1 text-xs" style={{ color: "var(--text-caption)" }}>
                            <Loader2 size={11} className="animate-spin" />
                            Checking for duplicates…
                          </span>
                        )}
                        {selectedAccountId && (
                          <DismissedDuplicates
                            dismissed={dupes.dismissed}
                            onRestore={(id) => applyDupes(restoreDuplicates(selectedAccountId, id, dupesModel))}
                          />
                        )}
                        {dupeGroups.length > 0 && (
                          <button
                            onClick={() => setDupesOnly((v) => !v)}
                            aria-pressed={dupesOnly}
                            title={dupesOnly ? "Show all tickets" : "Show only tickets in a possible-duplicate group"}
                            className="flex items-center gap-1.5 text-sm rounded px-2.5 py-1.5 transition-colors hover:bg-[var(--bg-tertiary)]"
                            style={
                              dupesOnly
                                ? { background: "rgba(0,109,221,0.12)", border: "1px solid rgba(0,109,221,0.55)", color: "var(--text-primary)" }
                                : { background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }
                            }
                          >
                            <Copy size={13} style={{ color: "#006ddd" }} />
                            {dupesOnly ? "Showing possible duplicates" : "Possible duplicates"}
                            <span
                              className="text-xs rounded-full px-1.5 min-w-5 text-center"
                              style={{ background: "rgba(0,109,221,0.15)", color: "#006ddd" }}
                            >
                              {dupeGroups.length}
                            </span>
                          </button>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2 mb-4 print:hidden" data-print-hide>
                    <FilterGroup label="Status">
                      {OPEN_STATES.map((state) => {
                        const active = selectedStates.includes(state);
                        return (
                          <FilterChip
                            key={state}
                            active={active}
                            onClick={() =>
                              setSelectedStates((prev) =>
                                active ? prev.filter((s) => s !== state) : [...prev, state]
                              )
                            }
                          >
                            {getStateLabels(selectedAccount?.name)[state] ?? state.replace(/_/g, " ")}
                          </FilterChip>
                        );
                      })}
                    </FilterGroup>
                    {members && (
                      <span className="hidden sm:block w-px h-4" style={{ background: "var(--border-hover)" }} aria-hidden />
                    )}
                    {members && (
                      <FilterGroup label="Account">
                        {[...members].map(([id, member]) => {
                          const active = !hiddenMembers.includes(id);
                          return (
                            <FilterChip
                              key={id}
                              active={active}
                              title={member.fullName}
                              onClick={() =>
                                setHiddenMembers((prev) => (active ? [...prev, id] : prev.filter((m) => m !== id)))
                              }
                            >
                              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: member.color }} />
                              {member.label}
                            </FilterChip>
                          );
                        })}
                      </FilterGroup>
                    )}
                  </div>

                  {filteredIssues.length === 0 ? (
                    <p className="text-sm" style={{ color: "var(--text-caption)" }}>No tickets match the current filters.</p>
                  ) : (
                    <div className="flex flex-col gap-2">{renderTicketList()}</div>
                  )}
                </div>
              </>
            ) : null}
          </>
        )}
      </main>

      {qbrConfirmRegenerate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setQbrConfirmRegenerate(false)}>
          <div
            className="rounded-xl px-6 py-5 w-80 shadow-xl"
            style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
            onClick={e => e.stopPropagation()}
          >
            <p className="text-sm font-medium mb-1" style={{ color: "var(--text-primary)" }}>Regenerate slides?</p>
            <p className="text-sm mb-5" style={{ color: "var(--text-muted)" }}>This will overwrite the existing slides for this month.</p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setQbrConfirmRegenerate(false)}
                className="text-sm px-3 py-1.5 rounded transition-opacity hover:opacity-80 cursor-pointer"
                style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >Cancel</button>
              <button
                onClick={() => {
                  if (!selectedAccount) return;
                  setQbrConfirmRegenerate(false);
                  runQbrGeneration(selectedAccount, qbrTemplateType);
                }}
                className="text-sm px-3 py-1.5 rounded transition-opacity hover:opacity-80 cursor-pointer"
                style={{ background: "var(--accent)", color: "white" }}
              >Regenerate</button>
            </div>
          </div>
        </div>
      )}

      {scheduleOpen && selectedAccount && (
        <ScheduleModal
          accountId={selectedAccount.id}
          accountName={selectedAccount.name}
          defaultPeriod={period}
          model={selectedModel}
          channelId={slackChannelId}
          availableChannels={slackAvailableChannels}
          openToNewQbr={scheduleOpenToNewQbr}
          onClose={() => { setScheduleOpen(false); setScheduleOpenToNewQbr(false); }}
        />
      )}
    </div>
  );
}
