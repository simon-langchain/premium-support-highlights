"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Loader2, ArrowLeft } from "lucide-react";
import TeamSidebar from "@/components/TeamSidebar";
import MetricCard from "@/components/MetricCard";
import OptionPicker from "@/components/OptionPicker";
import TrendMiniChart, { type TrendMiniChartPoint } from "@/components/TrendMiniChart";
import {
  fetchMe,
  fetchTeamDashboardData,
  fetchTeamOverview,
  fetchMessageActivitySyncStatus,
  type TeamDashboardData,
  type RepTrendPoint,
  type TeamMember,
  type MessageActivitySyncStatus,
} from "@/lib/api";
import { formatSyncStatus } from "@/lib/syncStatus";

function round1(n: number | null): number | null {
  return n === null ? null : Math.round(n * 10) / 10;
}

function delta(me: number | null, avg: number | null): number | undefined {
  if (me === null || avg === null) return undefined;
  return me - avg;
}

type TrendKey =
  | "tickets_taken"
  | "avg_response_time"
  | "median_response_time"
  | "avg_resolution_time"
  | "median_resolution_time"
  | "update_count"
  | "avg_reply_time"
  | "median_reply_time";

function zipTrend(
  meTrend: RepTrendPoint[],
  avgTrend: RepTrendPoint[],
  key: TrendKey
): TrendMiniChartPoint[] {
  return meTrend.map((point, i) => ({
    label: point.label,
    me: point[key],
    avg: avgTrend[i]?.[key] ?? null,
  }));
}

type Granularity = "day" | "week" | "month";

/** Which stat the three duration metrics (first response, resolution, reply
 * time) display — one global toggle rather than per-tile/per-chart, so
 * switching it keeps every affected tile and chart in sync. Defaults to
 * median: a single stuck/reopened ticket can swing the mean far from what a
 * typical ticket looked like, so median better represents "typical". */
type StatMode = "avg" | "median";

/** Matches the backend's default per-period bucketing (metrics.py's _default_granularity). */
function defaultGranularityFor(period: string): Granularity {
  return period === "7d" || period === "1m" ? "day" : "month";
}

const STATE_LABELS: Record<string, string> = {
  new: "New",
  waiting_on_you: "Waiting on You",
  on_hold: "On Hold",
  waiting_on_customer: "Waiting on Customer",
};

// Actionable states (a customer is waiting on a first response) shown first
// and visually grouped, so they're not lost among on_hold/waiting_on_customer
// — which don't have a wait time and aren't something the rep needs to act on.
const PENDING_STATES = ["new", "waiting_on_you"];
const STATE_ORDER = [...PENDING_STATES, "on_hold", "waiting_on_customer"];
const LONG_WAIT_THRESHOLD_HOURS = 24;

function formatWaitDuration(hours: number): string {
  if (hours < 24) return `${hours} hrs`;
  return `${(hours / 24).toFixed(1)} days`;
}

const SELF = "";

function StateBreakdownCard({
  breakdown,
  pendingWait,
  statMode,
  className = "",
}: {
  breakdown: Record<string, number>;
  pendingWait: Record<string, { count: number; avg_wait_hours: number; median_wait_hours: number }>;
  statMode: "avg" | "median";
  className?: string;
}) {
  const states = Object.keys(breakdown);
  const orderedStates = [
    ...STATE_ORDER.filter((s) => states.includes(s)),
    ...states.filter((s) => !STATE_ORDER.includes(s)),
  ];
  const pendingRows = orderedStates.filter((s) => PENDING_STATES.includes(s));
  const otherRows = orderedStates.filter((s) => !PENDING_STATES.includes(s));

  const renderRow = (state: string) => {
    const wait = pendingWait[state];
    const waitHours = wait ? (statMode === "median" ? wait.median_wait_hours : wait.avg_wait_hours) : null;
    const isLongWait = waitHours !== null && waitHours >= LONG_WAIT_THRESHOLD_HOURS;
    return (
      <div key={state} className="flex items-center justify-between gap-3">
        <span className="text-sm" style={{ color: "var(--text-primary)" }}>
          {STATE_LABELS[state] ?? state.replace(/_/g, " ")}
        </span>
        <div className="flex items-baseline gap-2">
          {waitHours !== null && (
            <span
              className={`text-xs font-medium ${isLongWait ? "text-red-500" : ""}`}
              style={isLongWait ? undefined : { color: "var(--text-muted)" }}
            >
              {statMode} wait {formatWaitDuration(waitHours)}
            </span>
          )}
          <span className="text-sm font-mono" style={{ color: "var(--text-muted)" }}>{breakdown[state]}</span>
        </div>
      </div>
    );
  };

  return (
    <div
      className={`rounded-lg px-4 py-4 ${className}`}
      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
    >
      <h3 className="text-xs uppercase tracking-wider font-medium mb-3" style={{ color: "var(--text-muted)" }}>
        Currently Open (by state)
      </h3>
      {orderedStates.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>No open tickets</p>
      ) : (
        <div className="space-y-1.5">
          {pendingRows.map(renderRow)}
          {pendingRows.length > 0 && otherRows.length > 0 && (
            <div className="!my-2" style={{ borderTop: "1px solid var(--border)" }} />
          )}
          {otherRows.map(renderRow)}
        </div>
      )}
    </div>
  );
}

export default function TeamDashboardPage() {
  const router = useRouter();
  const [period, setPeriod] = useState("1m");
  const [granularity, setGranularity] = useState<Granularity>(() => defaultGranularityFor("1m"));
  const [statMode, setStatMode] = useState<StatMode>("median");
  const [isAdmin, setIsAdmin] = useState(false);
  const [viewAsEmail, setViewAsEmail] = useState(SELF);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [data, setData] = useState<TeamDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dataUpdatedAt, setDataUpdatedAt] = useState<Date | null>(null);
  const [syncStatus, setSyncStatus] = useState<MessageActivitySyncStatus | null>(null);
  // Gates the data-fetch effect until the URL's `?as=` param (if any) has been
  // applied to viewAsEmail — otherwise the fetch effect fires once with the
  // default SELF value before that state update lands, then again with the
  // correct value, and the first (stale) response can race the second and
  // clobber it depending on resolve order.
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    const param = new URLSearchParams(window.location.search).get("as");
    if (param) setViewAsEmail(param);
    setInitialized(true);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (cancelled) return;
        if (!me?.is_support_team_member) {
          router.replace("/customers");
          return;
        }
        setIsAdmin(me.is_admin);
        if (me.is_admin) {
          fetchTeamOverview(period).then((ov) => { if (!cancelled) setMembers(ov.members); }).catch(() => {});
        }
      })
      .catch(() => {
        if (!cancelled) router.replace("/customers");
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  const load = useCallback(async (p: string, asEmail: string, force = false, g?: Granularity) => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchTeamDashboardData(p, asEmail || undefined, force, g);
      setData(result);
      setDataUpdatedAt(new Date());
    } catch (e) {
      const message = e instanceof Error ? e.message : "Failed to load";
      // Defense-in-depth: covers the rare race where Support-team membership
      // (or admin status, for the `as` param) changed since the last check.
      if (message === "Not a member of the Support team") {
        router.replace("/customers");
        return;
      }
      if (message === "Only admins can view another member's metrics") {
        setViewAsEmail(SELF);
        return;
      }
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    if (!initialized) return;
    load(period, viewAsEmail, false, granularity);
    const url = viewAsEmail ? `/team?as=${encodeURIComponent(viewAsEmail)}` : "/team";
    window.history.replaceState(null, "", url);
  }, [period, viewAsEmail, granularity, initialized, load]);

  // Resets granularity to the new period's default in the same event handler
  // that changes the period (rather than a separate effect watching period),
  // so both land in the same React batch and the reload effect above fires
  // once with the correct pair instead of once with a stale granularity
  // followed by a second fetch once it catches up.
  const handlePeriodChange = useCallback((p: string) => {
    setPeriod(p);
    setGranularity(defaultGranularityFor(p));
  }, []);

  const handleRefresh = useCallback(() => {
    load(period, viewAsEmail, true, granularity);
  }, [load, period, viewAsEmail, granularity]);

  // Polls backfill progress for the "Ticket Updates Over Time" chart until
  // it's fully synced, then stops — no need to keep polling once there's
  // nothing left to report.
  useEffect(() => {
    if (!initialized) return;
    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | undefined;
    const check = () => {
      fetchMessageActivitySyncStatus().then((status) => {
        if (cancelled || !status) return;
        setSyncStatus(status);
        if (status.complete && intervalId) {
          clearInterval(intervalId);
          intervalId = undefined;
        }
      }).catch(() => {});
    };
    check();
    intervalId = setInterval(check, 60_000);
    return () => { cancelled = true; if (intervalId) clearInterval(intervalId); };
  }, [initialized]);

  const me = data?.me;
  const avg = statMode === "median" ? data?.team_median : data?.team_average;

  const memberOptions = useMemo(
    () => [{ value: SELF, label: "Me" }, ...members
      .filter((m) => !m.is_admin)
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((m) => ({ value: m.email, label: m.name }))],
    [members]
  );

  const trendCharts = useMemo(() => {
    if (!data || data.me_trend.length === 0) return null;
    const avgTrend = statMode === "median" ? data.team_median_trend : data.team_average_trend;
    const responseKey: TrendKey = statMode === "median" ? "median_response_time" : "avg_response_time";
    const resolutionKey: TrendKey = statMode === "median" ? "median_resolution_time" : "avg_resolution_time";
    const replyTimeKey: TrendKey = statMode === "median" ? "median_reply_time" : "avg_reply_time";
    return {
      tickets: zipTrend(data.me_trend, avgTrend, "tickets_taken"),
      response: zipTrend(data.me_trend, avgTrend, responseKey),
      resolution: zipTrend(data.me_trend, avgTrend, resolutionKey),
      updates: zipTrend(data.me_trend, avgTrend, "update_count"),
      replyTime: zipTrend(data.me_trend, avgTrend, replyTimeKey),
    };
  }, [data, statMode]);

  return (
    <div className="flex h-screen overflow-hidden">
      <TeamSidebar
        period={period}
        onPeriodChange={handlePeriodChange}
        isAdmin={isAdmin}
        onRefresh={handleRefresh}
        dataUpdatedAt={dataUpdatedAt}
      />
      <main className="flex-1 overflow-y-auto px-6 py-6" style={{ background: "var(--bg-base)" }}>
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            {data?.viewing_as ? (
              <>
                <button
                  onClick={() => setViewAsEmail(SELF)}
                  className="flex items-center gap-1.5 text-xs mb-1.5 hover:opacity-80 transition-opacity cursor-pointer"
                  style={{ color: "var(--accent)" }}
                >
                  <ArrowLeft size={12} />
                  Back to my metrics
                </button>
                <h1 className="text-xl font-bold" style={{ color: "var(--text-primary)" }}>
                  {data.viewing_as.name}&apos;s Metrics
                </h1>
              </>
            ) : (
              <h1 className="text-xl font-bold" style={{ color: "var(--text-primary)" }}>My Metrics</h1>
            )}
            <p className="text-sm mt-0.5 flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
              {data ? `Compared against ${data.team_member_count} Support team members` : "Loading…"}
              {loading && data && (
                <span className="flex items-center gap-1">
                  <Loader2 size={11} className="animate-spin" />
                  Updating…
                </span>
              )}
            </p>
          </div>

          {isAdmin && memberOptions.length > 1 && (
            <div className="w-52 shrink-0">
              <label style={{ color: "var(--text-muted)" }} className="block text-xs uppercase tracking-wider mb-1">
                Viewing As
              </label>
              <OptionPicker options={memberOptions} value={viewAsEmail} onChange={setViewAsEmail} />
            </div>
          )}
        </div>

        {error && (
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/40 rounded-lg px-4 py-3 mb-4 text-red-600 dark:text-red-300 text-sm">
            {error}
          </div>
        )}

        {loading && !data ? (
          <div className="flex items-center gap-2 mt-8" style={{ color: "var(--text-muted)" }}>
            <Loader2 size={18} className="animate-spin" />
            <span className="text-sm">Loading metrics…</span>
          </div>
        ) : me && avg ? (
          <>
            <div className="flex items-center justify-end gap-1 mb-3">
              <span className="text-xs mr-1" style={{ color: "var(--text-muted)" }}>
                Stats:
              </span>
              {(["median", "avg"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setStatMode(m)}
                  className="text-xs rounded px-2.5 py-1 transition-colors"
                  style={{
                    background: statMode === m ? "var(--accent)" : "var(--bg-primary)",
                    color: statMode === m ? "#fff" : "var(--text-muted)",
                    border: `1px solid ${statMode === m ? "var(--accent)" : "var(--border)"}`,
                  }}
                >
                  {m === "median" ? "Median" : "Average"}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
              <MetricCard
                label="Tickets Taken"
                value={me.tickets_taken}
                sub={`Team ${statMode}: ${avg.tickets_taken}`}
                delta={delta(me.tickets_taken, avg.tickets_taken)}
                deltaLabel={`vs team ${statMode}`}
              />
              <MetricCard
                label="First Response Time"
                value={round1(statMode === "median" ? me.median_response_time : me.avg_response_time)}
                unit="hrs"
                sub={
                  (statMode === "median" ? avg.median_response_time : avg.avg_response_time) !== null
                    ? `Team ${statMode}: ${round1(statMode === "median" ? avg.median_response_time : avg.avg_response_time)} hrs`
                    : undefined
                }
                delta={delta(
                  statMode === "median" ? me.median_response_time : me.avg_response_time,
                  statMode === "median" ? avg.median_response_time : avg.avg_response_time
                )}
                deltaLabel={`vs team ${statMode}`}
                lowerIsBetter
              />
              <MetricCard
                label="First Response SLA"
                value={me.sla_compliance_pct}
                unit="%"
                sub={avg.sla_compliance_pct !== null ? `Team ${statMode}: ${avg.sla_compliance_pct}%` : undefined}
                delta={delta(me.sla_compliance_pct, avg.sla_compliance_pct)}
                deltaLabel={`vs team ${statMode}`}
              />
              <MetricCard
                label="Reply Time"
                value={round1(statMode === "median" ? me.median_reply_time : me.avg_reply_time)}
                unit="hrs"
                sub={
                  (statMode === "median" ? avg.median_reply_time : avg.avg_reply_time) !== null
                    ? `Team ${statMode}: ${round1(statMode === "median" ? avg.median_reply_time : avg.avg_reply_time)} hrs`
                    : undefined
                }
                delta={delta(
                  statMode === "median" ? me.median_reply_time : me.avg_reply_time,
                  statMode === "median" ? avg.median_reply_time : avg.avg_reply_time
                )}
                deltaLabel={`vs team ${statMode}`}
                lowerIsBetter
              />
            </div>

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
              <MetricCard
                label="Resolution Time"
                value={round1(statMode === "median" ? me.median_resolution_time : me.avg_resolution_time)}
                unit="hrs"
                sub={
                  (statMode === "median" ? avg.median_resolution_time : avg.avg_resolution_time) !== null
                    ? `Team ${statMode}: ${round1(statMode === "median" ? avg.median_resolution_time : avg.avg_resolution_time)} hrs`
                    : undefined
                }
                delta={delta(
                  statMode === "median" ? me.median_resolution_time : me.avg_resolution_time,
                  statMode === "median" ? avg.median_resolution_time : avg.avg_resolution_time
                )}
                deltaLabel={`vs team ${statMode}`}
                lowerIsBetter
              />
              <MetricCard
                label="Backlog (Waiting on You)"
                value={me.backlog_count}
                sub={`Team ${statMode}: ${avg.backlog_count}`}
                delta={delta(me.backlog_count, avg.backlog_count)}
                deltaLabel={`vs team ${statMode}`}
                lowerIsBetter
              />
              <MetricCard
                label="Ticket Updates"
                value={me.update_count}
                sub={`Team ${statMode}: ${avg.update_count}`}
                delta={delta(me.update_count, avg.update_count)}
                deltaLabel={`vs team ${statMode}`}
              />
              <MetricCard
                label="CSAT"
                value={round1(statMode === "median" ? me.median_csat : me.avg_csat)}
                unit="/ 5"
                sub={
                  (statMode === "median" ? avg.median_csat : avg.avg_csat) !== null
                    ? `Team ${statMode}: ${round1(statMode === "median" ? avg.median_csat : avg.avg_csat)} / 5`
                    : undefined
                }
                delta={delta(
                  statMode === "median" ? me.median_csat : me.avg_csat,
                  statMode === "median" ? avg.median_csat : avg.avg_csat
                )}
                deltaLabel={`vs team ${statMode}`}
              />
            </div>

            <div className="mb-5">
              {trendCharts && (
                <>
                  <div className="flex items-center justify-end gap-1 mb-2">
                    {(["day", "week", "month"] as const).map((g) => (
                      <button
                        key={g}
                        type="button"
                        onClick={() => setGranularity(g)}
                        className="text-xs rounded px-2.5 py-1 capitalize transition-colors"
                        style={{
                          background: granularity === g ? "var(--accent)" : "var(--bg-primary)",
                          color: granularity === g ? "#fff" : "var(--text-muted)",
                          border: `1px solid ${granularity === g ? "var(--accent)" : "var(--border)"}`,
                        }}
                      >
                        {g}
                      </button>
                    ))}
                  </div>
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                    <TrendMiniChart
                      title="Tickets Taken Over Time"
                      data={trendCharts.tickets}
                      avgLabel={statMode === "median" ? "Team median" : "Team avg"}
                    />
                    <TrendMiniChart
                      title="First Response Time Over Time"
                      data={trendCharts.response}
                      unit="hrs"
                      avgLabel={statMode === "median" ? "Team median" : "Team avg"}
                    />
                    <TrendMiniChart
                      title="Reply Time Over Time"
                      data={trendCharts.replyTime}
                      unit="hrs"
                      avgLabel={statMode === "median" ? "Team median" : "Team avg"}
                    />
                    <TrendMiniChart
                      title="Resolution Time Over Time"
                      data={trendCharts.resolution}
                      unit="hrs"
                      avgLabel={statMode === "median" ? "Team median" : "Team avg"}
                    />
                    <TrendMiniChart
                      title="Ticket Updates Over Time"
                      data={trendCharts.updates}
                      avgLabel={statMode === "median" ? "Team median" : "Team avg"}
                    />
                    <StateBreakdownCard breakdown={me.state_breakdown} pendingWait={me.pending_wait} statMode={statMode} />
                  </div>
                  {syncStatus && (
                    <p className="text-xs mt-1.5 pl-0.5" style={{ color: "var(--text-muted)" }}>
                      {formatSyncStatus(syncStatus)}
                    </p>
                  )}
                </>
              )}

              {!trendCharts && (
                <StateBreakdownCard
                  breakdown={me.state_breakdown}
                  pendingWait={me.pending_wait}
                  statMode={statMode}
                  className="max-w-md"
                />
              )}
            </div>
          </>
        ) : null}
      </main>
    </div>
  );
}
