"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Loader2 } from "lucide-react";
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

function sortIssues(issues: Issue[], sortBy: string): Issue[] {
  return [...issues].sort((a, b) => {
    if (sortBy === "state") {
      return (STATE_ORDER[a.state] ?? 99) - (STATE_ORDER[b.state] ?? 99);
    }
    if (sortBy === "created") {
      return (
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
    }
    if (sortBy === "priority") {
      return (
        (PRIORITY_ORDER[a.priority] ?? 99) - (PRIORITY_ORDER[b.priority] ?? 99)
      );
    }
    return 0;
  });
}

export default function Home() {
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
  const [selectedStates, setSelectedStates] = useState<string[]>(OPEN_STATES);
  const [forceRefresh, setForceRefresh] = useState(0);
  const [ticketSummaries, setTicketSummaries] = useState<Record<number, string | null>>({});
  const [accountSummary, setAccountSummary] = useState<string | null>(null);
  const [summaryGeneratedAt, setSummaryGeneratedAt] = useState<Date | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  // Refs so pipeline callbacks always see current model/period without stale closures
  const modelRef = useRef(selectedModel);
  useEffect(() => { modelRef.current = selectedModel; }, [selectedModel]);
  const periodRef = useRef(period);
  useEffect(() => { periodRef.current = period; }, [period]);

  // Abort controller for any in-flight summary pipeline (ticket SSE + account summary)
  const summaryAbortRef = useRef<AbortController | null>(null);

  // Load accounts on mount
  useEffect(() => {
    setAccountsLoading(true);
    fetchAccounts()
      .then((data) => {
        setAccounts(data);
        if (data.length > 0) {
          setSelectedAccount(data[0]);
        }
      })
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
    if (selectedAccount) {
      const cleanup = loadAccountData(selectedAccount);
      return cleanup;
    }
  }, [selectedAccount, loadAccountData]);

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
        sortBy
      )
    : [];

  const totalRaised =
    accountData?.monthly_metrics.reduce((sum, m) => sum + m.tickets_raised, 0) ?? null;

  const totalClosed =
    accountData?.monthly_metrics.reduce((sum, m) => sum + m.closed_tickets, 0) ?? null;

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
            <div className="mb-6">
              <h1 className="text-xl font-bold" style={{ color: "var(--text-primary)" }}>
                {selectedAccount.name}
              </h1>
              <p className="text-sm mt-0.5" style={{ color: "var(--text-muted)" }}>
                Premium account overview
              </p>
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
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
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
                      value={accountData.csat.toFixed(1)}
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
                  <div className="flex flex-wrap gap-2 mb-4">
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
                    <select
                      value={sortBy}
                      onChange={(e) => setSortBy(e.target.value)}
                      style={{
                        background: "var(--bg-secondary)",
                        border: "1px solid var(--border)",
                        color: "var(--text-primary)",
                      }}
                      className="text-sm rounded px-2 py-1.5 focus:outline-none cursor-pointer"
                    >
                      {SORT_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>Sort: {opt.label}</option>
                      ))}
                    </select>
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
