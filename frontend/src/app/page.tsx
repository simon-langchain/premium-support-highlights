"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Loader2, ArrowUpDown, ChevronRight } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import MetricCard from "@/components/MetricCard";
import TicketCard from "@/components/TicketCard";
import TrendChart from "@/components/TrendChart";
import SummaryPanel from "@/components/SummaryPanel";
import {
  fetchAccounts,
  fetchAccountData,
  fetchCachedTicketSummaries,
  generateSummary,
  type Account,
  type AccountData,
  type Issue,
} from "@/lib/api";
import DownloadMenu from "@/components/DownloadMenu";
import EmailButton from "@/components/EmailButton";
import SlackButton from "@/components/SlackButton";
import AccountPicker from "@/components/AccountPicker";
import OptionPicker from "@/components/OptionPicker";
import { downloadCsv, downloadPdf, emailReport, slackReport } from "@/lib/downloads";

const OPEN_STATES = ["new", "waiting_on_you", "on_hold", "waiting_on_customer"];

const PRIORITY_LABELS: Record<string, string> = {
  urgent: "Sev 1",
  high: "Sev 2",
  medium: "Sev 3",
  low: "Sev 4",
  none: "None",
};

const STATE_LABELS: Record<string, string> = {
  new: "New",
  waiting_on_you: "Waiting on LangChain",
  on_hold: "On Hold",
  waiting_on_customer: "Waiting on Customer",
  closed: "Closed",
  resolved: "Resolved",
};

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

const MODELS = [
  { value: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
  { value: "claude-opus-4-6", label: "Claude Opus 4.6" },
  { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
];

const Logo = () => (
  <svg width="32" height="32" viewBox="0 0 128 128" fill="none" xmlns="http://www.w3.org/2000/svg" className="flex-shrink-0">
    <path d="M40.1024 85.0722C47.6207 77.5537 51.8469 67.3453 51.8469 56.7136C51.8469 46.0818 47.617 35.8734 40.1024 28.355L11.7446 0C4.22995 7.5185 0 17.7269 0 28.3586C0 38.9903 4.22995 49.1987 11.7446 56.7172L40.0987 85.0722H40.1024Z" fill="#006ddd" />
    <path d="M99.4385 87.698C91.9239 80.1832 81.7121 75.9531 71.0844 75.9531C60.4566 75.9531 50.2448 80.1832 42.7266 87.698L71.0844 116.057C78.599 123.571 88.8107 127.802 99.4421 127.802C110.074 127.802 120.282 123.571 127.8 116.057L99.4421 87.698H99.4385Z" fill="#006ddd" />
    <path d="M11.8146 115.987C19.3329 123.502 29.541 127.732 40.1724 127.732V87.6289H0.0664062C0.0700559 98.2606 4.29635 108.469 11.8146 115.987Z" fill="#006ddd" />
    <path d="M110.387 45.7684C102.869 38.2535 92.6608 34.0198 82.0258 34.0234C71.3943 34.0234 61.1863 38.2535 53.668 45.772L82.0258 74.1306L110.387 45.7684Z" fill="#006ddd" />
  </svg>
);

export default function Home() {
  const [configured, setConfigured] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null);
  const [accountData, setAccountData] = useState<AccountData | null>(null);
  const [loading, setLoading] = useState(false);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState("claude-sonnet-4-6");
  const [period, setPeriod] = useState("6m");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState("priority");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");
  const [selectedStates, setSelectedStates] = useState<string[]>(OPEN_STATES);
  const [forceRefresh, setForceRefresh] = useState(0);
  const [ticketSummaries, setTicketSummaries] = useState<Record<number, string | null>>({});
  const [accountSummary, setAccountSummary] = useState<string | null>(null);
  const [summaryGeneratedAt, setSummaryGeneratedAt] = useState<Date | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [slackChannelName, setSlackChannelName] = useState<string | null>(null);
  const [slackChannelId, setSlackChannelId] = useState<string | null>(null);
  const [slackAvailableChannels, setSlackAvailableChannels] = useState<{ id: string; name: string }[]>([]);

  // Refs so pipeline callbacks always see current model/period without stale closures
  const modelRef = useRef(selectedModel);
  useEffect(() => { modelRef.current = selectedModel; }, [selectedModel]);
  const periodRef = useRef(period);
  useEffect(() => { periodRef.current = period; }, [period]);

  // Abort controller for any in-flight summary pipeline (ticket SSE + account summary)
  const summaryAbortRef = useRef<AbortController | null>(null);

  // Load accounts on mount — do not auto-select; user picks on setup screen
  useEffect(() => {
    setAccountsLoading(true);
    fetchAccounts()
      .then((data) => setAccounts(data))
      .catch((err) => setError(err.message))
      .finally(() => setAccountsLoading(false));
  }, []);

  // Run the summary pipeline: agent generates ticket summaries + account summary.
  // Polls cached ticket summaries every 2s while the agent runs so cards appear
  // progressively as parallel ticket summaries complete. Does NOT reset ticket
  // summaries to null — callers pre-populate them (initial load shows cached;
  // regenerate keeps showing old summaries until new ones arrive).
  const runSummaryPipeline = useCallback(
    async (account: Account, openNumbers: number[], force: boolean, signal: AbortSignal) => {
      setAccountSummary(null);
      setSummaryGeneratedAt(null);
      setSummaryLoading(true);
      setSummaryError(null);

      // Poll for ticket summaries while the agent runs
      const applysummaries = (summaries: Record<number, string>) => {
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
        fetchCachedTicketSummaries(account.id)
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
        if (!signal.aborted) setSummaryLoading(false);
      }

      if (signal.aborted) return;

      // Final fetch to catch any summaries that completed between the last poll and agent finish
      try {
        const summaries = await fetchCachedTicketSummaries(account.id);
        if (!signal.aborted) {
          setTicketSummaries((prev) => {
            const next = { ...prev };
            for (const [num, summary] of Object.entries(summaries)) {
              next[Number(num)] = summary;
            }
            for (const n of openNumbers) {
              if (next[n] === null) next[n] = "";
            }
            return next;
          });
        }
      } catch {
        if (!signal.aborted) {
          setTicketSummaries((prev) => {
            const next = { ...prev };
            for (const n of openNumbers) {
              if (next[n] === null) next[n] = "";
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
    (account: Account) => {
      summaryAbortRef.current?.abort();
      const abortCtrl = new AbortController();
      summaryAbortRef.current = abortCtrl;

      setLoading(true);
      setError(null);
      setAccountData(null);
      setTicketSummaries({});
      setAccountSummary(null);
      setSummaryLoading(false);
      setSummaryError(null);

      fetchAccountData(account.id, account.name, period)
        .then((data) => {
          setAccountData(data);
          const openNumbers = data.open_issues.map((i) => i.number);

          // Initialise all tickets as loading (null), then immediately show
          // any previously cached summaries so returning visits feel instant.
          // The pipeline will fill in the rest progressively via polling.
          const initial: Record<number, string | null> = {};
          for (const n of openNumbers) initial[n] = null;
          setTicketSummaries(initial);
          fetchCachedTicketSummaries(account.id)
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
    [forceRefresh, period, runSummaryPipeline]
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
    setForceRefresh((n) => n + 1);
  }

  function handleAccountSelect(account: Account) {
    setSelectedAccount(account);
    setSearchQuery("");
    setSortBy("priority");
    setSelectedStates(OPEN_STATES);
  }

  function handleRegenerate() {
    if (!selectedAccount || !accountData) return;
    summaryAbortRef.current?.abort();
    const abortCtrl = new AbortController();
    summaryAbortRef.current = abortCtrl;
    const openNumbers = accountData.open_issues.map((i) => i.number);
    runSummaryPipeline(selectedAccount, openNumbers, true, abortCtrl.signal);
  }

  // Filtering + sorting open issues
  const filteredIssues: Issue[] = accountData
    ? sortIssues(
        accountData.open_issues.filter((issue) => {
          const matchesState =
            selectedStates.length === 0 ||
            selectedStates.includes(issue.state);
          const q = searchQuery.toLowerCase();
          const matchesSearch =
            !q ||
            issue.title.toLowerCase().includes(q) ||
            String(issue.number).includes(q) ||
            issue.tags.some((t) => t.toLowerCase().includes(q));
          return matchesState && matchesSearch;
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
                  Account
                </label>
                <AccountPicker
                  accounts={accounts}
                  selected={setupAccount}
                  onSelect={setSelectedAccount}
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

              <div>
                <label className="block text-xs uppercase tracking-wider mb-1.5 font-medium" style={{ color: "var(--text-muted)" }}>
                  Summary Model
                </label>
                <OptionPicker
                  options={MODELS}
                  value={selectedModel}
                  onChange={setSelectedModel}
                />
              </div>

              <button
                onClick={() => {
                  if (!setupAccount) return;
                  setSelectedAccount(setupAccount);
                  setConfigured(true);
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

  // ── Dashboard ──────────────────────────────────────────────────────────────
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        accounts={accounts}
        selected={selectedAccount}
        onSelect={handleAccountSelect}
        onRefresh={handleRefresh}
        selectedModel={selectedModel}
        onModelChange={setSelectedModel}
        period={period}
        onPeriodChange={setPeriod}
        onSetup={() => setConfigured(false)}
      />

      {/* Main content */}
      <main className="flex-1 overflow-y-auto px-6 py-6" style={{ background: "var(--bg-base)" }}>
        {accountsLoading ? (
          <div className="flex items-center gap-2 mt-8" style={{ color: "var(--text-muted)" }}>
            <Loader2 size={18} className="animate-spin" />
            <span className="text-sm">Loading accounts...</span>
          </div>
        ) : !selectedAccount ? (
          <p className="text-sm mt-8" style={{ color: "var(--text-muted)" }}>
            No premium accounts found.
          </p>
        ) : (
          <>
            {/* Page header */}
            <div className="mb-6 flex items-start justify-between">
              <div>
                <h1 className="text-xl font-bold" style={{ color: "var(--text-primary)" }}>
                  {selectedAccount.name}
                </h1>
                <p className="text-sm mt-0.5" style={{ color: "var(--text-muted)" }}>
                  Premium account overview
                </p>
              </div>
              {accountData && (
                <div className="flex items-center gap-2">
                  <SlackButton
                    onSlackReport={(channelId) => slackReport(selectedAccount.id, selectedAccount.name, period, channelId)}
                    channelName={slackChannelName}
                    channelId={slackChannelId}
                    availableChannels={slackAvailableChannels}
                  />
                  <EmailButton
                    onEmailReport={(email) => emailReport(selectedAccount.id, selectedAccount.name, email, period, sortBy, sortOrder)}
                  />
                  <DownloadMenu
                    onDownloadPdf={() => downloadPdf(selectedAccount.id, selectedAccount.name, period, sortBy, sortOrder)}
                    onDownloadCsv={() => downloadCsv(selectedAccount.name, period, accountData, filteredIssues, ticketSummaries)}
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
                <div className={`grid grid-cols-2 gap-3 mb-5 ${accountData.csat !== null ? "lg:grid-cols-5" : "lg:grid-cols-4"}`}>
                  <MetricCard label="Open Issues" value={accountData.open_issues.length} />
                  <MetricCard label={`Tickets Raised (${PERIOD_LABELS[period] ?? period})`} value={totalRaised} />
                  <MetricCard label={`Tickets Closed (${PERIOD_LABELS[period] ?? period})`} value={totalClosed} />
                  <MetricCard
                    label="Avg Response"
                    value={accountData.avg_response_time !== null ? accountData.avg_response_time.toFixed(1) : null}
                    unit="hrs"
                  />
                  {accountData.csat !== null && (
                    <MetricCard
                      label="CSAT"
                      value={accountData.csat % 1 === 0 ? String(accountData.csat) : accountData.csat.toFixed(1)}
                      unit="/ 5"
                    />
                  )}
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
                                  ? (STATE_LABELS[label] ?? label.replace(/_/g, " "))
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
                  />
                </div>

                {/* Open tickets */}
                <div>
                  <h2 className="text-base font-semibold mb-3" style={{ color: "var(--text-primary)" }}>
                    Open Issues ({filteredIssues.length})
                  </h2>

                  {/* Filters row */}
                  <div className="flex flex-wrap gap-2 mb-4 print:hidden" data-print-hide>
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
                      className="text-sm rounded px-3 py-1.5 focus:outline-none w-48 placeholder:text-[var(--text-caption)]"
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
                    <div className="flex flex-wrap gap-1">
                      {OPEN_STATES.map((state) => {
                        const active = selectedStates.includes(state);
                        return (
                          <button
                            key={state}
                            onClick={() =>
                              setSelectedStates((prev) =>
                                active ? prev.filter((s) => s !== state) : [...prev, state]
                              )
                            }
                            style={active ? undefined : {
                              background: "var(--bg-secondary)",
                              border: "1px solid var(--border)",
                              color: "var(--text-muted)",
                            }}
                            className={`text-xs px-2 py-1 rounded border transition-colors ${
                              active ? "bg-[#006ddd]/20 border-[#006ddd] text-[#006ddd]" : ""
                            }`}
                          >
                            {STATE_LABELS[state] ?? state.replace(/_/g, " ")}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {filteredIssues.length === 0 ? (
                    <p className="text-sm" style={{ color: "var(--text-caption)" }}>No tickets match the current filters.</p>
                  ) : (
                    <div className="flex flex-col gap-2">
                      {filteredIssues.map((issue) => (
                        <TicketCard
                          key={issue.number}
                          issue={issue}
                          ticketSummary={ticketSummaries[issue.number]}
                        />
                      ))}
                    </div>
                  )}
                </div>
              </>
            ) : null}
          </>
        )}
      </main>
    </div>
  );
}
