"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Trash2, Plus, Eye } from "lucide-react";
import TeamSidebar from "@/components/TeamSidebar";
import OptionPicker from "@/components/OptionPicker";
import {
  fetchMe,
  fetchTeamOverview,
  fetchAdmins,
  addAdmin,
  removeAdmin,
  type TeamOverview,
  type RepMetrics,
} from "@/lib/api";

function round1(n: number | null): string {
  return n === null ? "—" : (Math.round(n * 10) / 10).toString();
}

type StatMode = "avg" | "median";

function getMetricColumns(
  statMode: StatMode
): { key: keyof RepMetrics; label: string; unit?: string; decimals?: boolean }[] {
  return [
    { key: "tickets_taken", label: "Tickets" },
    {
      key: statMode === "median" ? "median_response_time" : "avg_response_time",
      label: "1st Resp",
      unit: "hrs",
      decimals: true,
    },
    { key: "sla_compliance_pct", label: "SLA", unit: "%" },
    {
      key: statMode === "median" ? "median_resolution_time" : "avg_resolution_time",
      label: "Res.",
      unit: "hrs",
      decimals: true,
    },
    { key: "backlog_count", label: "Backlog" },
    { key: statMode === "median" ? "median_csat" : "avg_csat", label: "CSAT", decimals: true },
  ];
}

function formatCell(decimals: boolean | undefined, value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "number") return String(value);
  if (decimals) return round1(value);
  return value.toString();
}

export default function TeamAdminPage() {
  const router = useRouter();
  const [period, setPeriod] = useState("1m");
  const [overview, setOverview] = useState<TeamOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("__all__");
  const [dataUpdatedAt, setDataUpdatedAt] = useState<Date | null>(null);
  const [statMode, setStatMode] = useState<StatMode>("median");

  const [admins, setAdmins] = useState<string[]>([]);
  const [adminsLoading, setAdminsLoading] = useState(true);
  const [newAdminEmail, setNewAdminEmail] = useState("");
  const [adminActionError, setAdminActionError] = useState<string | null>(null);
  const [adminActionBusy, setAdminActionBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (cancelled) return;
        if (!me?.is_support_team_member) {
          router.replace("/customers");
          return;
        }
        if (!me.is_admin) {
          router.replace("/team");
        }
      })
      .catch(() => {
        if (!cancelled) router.replace("/customers");
      });
    return () => { cancelled = true; };
  }, [router]);

  const load = useCallback(async (p: string, force = false) => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchTeamOverview(p, undefined, force);
      setOverview(result);
      setDataUpdatedAt(new Date());
    } catch (e) {
      const message = e instanceof Error ? e.message : "Failed to load";
      if (message === "Not a member of the Support team") {
        router.replace("/customers");
        return;
      }
      if (message === "Not a team-dashboard admin") {
        router.replace("/team");
        return;
      }
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load(period);
  }, [period, load]);

  const handleRefresh = useCallback(() => {
    load(period, true);
  }, [load, period]);

  useEffect(() => {
    setAdminsLoading(true);
    fetchAdmins()
      .then(setAdmins)
      .catch(() => {})
      .finally(() => setAdminsLoading(false));
  }, []);

  async function handleAddAdmin() {
    const email = newAdminEmail.trim();
    if (!email) return;
    setAdminActionBusy(true);
    setAdminActionError(null);
    try {
      const result = await addAdmin(email);
      setAdmins(result);
      setNewAdminEmail("");
    } catch (e) {
      setAdminActionError(e instanceof Error ? e.message : "Failed to add admin");
    } finally {
      setAdminActionBusy(false);
    }
  }

  async function handleRemoveAdmin(email: string) {
    setAdminActionBusy(true);
    setAdminActionError(null);
    try {
      const result = await removeAdmin(email);
      setAdmins(result);
    } catch (e) {
      setAdminActionError(e instanceof Error ? e.message : "Failed to remove admin");
    } finally {
      setAdminActionBusy(false);
    }
  }

  const members = overview?.members ?? [];
  const filteredMembers = filter === "__all__" ? members : members.filter((m) => m.email === filter);
  const metricColumns = getMetricColumns(statMode);
  const teamRow = overview ? (statMode === "median" ? overview.team_median : overview.team_average) : null;

  return (
    <div className="flex h-screen overflow-hidden">
      <TeamSidebar
        period={period}
        onPeriodChange={setPeriod}
        isAdmin={true}
        onRefresh={handleRefresh}
        dataUpdatedAt={dataUpdatedAt}
      />
      <main className="flex-1 overflow-y-auto px-6 py-6" style={{ background: "var(--bg-base)" }}>
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold" style={{ color: "var(--text-primary)" }}>Team View</h1>
            <p className="text-sm mt-0.5 flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
              Every Support team member&apos;s metrics vs. the team {statMode}
              {loading && overview && (
                <span className="flex items-center gap-1">
                  <Loader2 size={11} className="animate-spin" />
                  Updating…
                </span>
              )}
            </p>
          </div>
          <div className="w-56">
            <OptionPicker
              options={[{ value: "__all__", label: "All members" }, ...members.map((m) => ({ value: m.email, label: m.name }))]}
              value={filter}
              onChange={setFilter}
            />
          </div>
        </div>

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

        {error && (
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/40 rounded-lg px-4 py-3 mb-4 text-red-600 dark:text-red-300 text-sm">
            {error}
          </div>
        )}

        {loading && !overview ? (
          <div className="flex items-center gap-2 mt-8" style={{ color: "var(--text-muted)" }}>
            <Loader2 size={18} className="animate-spin" />
            <span className="text-sm">Loading team data…</span>
          </div>
        ) : overview ? (
          <div className="overflow-x-auto rounded-lg mb-8" style={{ border: "1px solid var(--border)" }}>
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr style={{ background: "var(--bg-secondary)", borderBottom: "1px solid var(--border)" }}>
                  <th className="text-left px-4 py-2.5 font-medium" style={{ color: "var(--text-muted)" }}>Rep</th>
                  {metricColumns.map((col) => (
                    <th key={col.key} className="text-right px-4 py-2.5 font-medium whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                      {col.label}{col.unit ? ` (${col.unit})` : ""}
                    </th>
                  ))}
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                <tr style={{ background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)" }}>
                  <td className="px-4 py-2.5 font-semibold" style={{ color: "var(--text-primary)" }}>
                    {statMode === "median" ? "Team Median" : "Team Average"}
                  </td>
                  {metricColumns.map((col) => (
                    <td key={col.key} className="text-right px-4 py-2.5 font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                      {formatCell(col.decimals, teamRow?.[col.key])}
                    </td>
                  ))}
                  <td className="px-4 py-2.5" />
                </tr>
                {filteredMembers.map((m) => (
                  <tr key={m.email} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td className="px-4 py-2.5" style={{ color: "var(--text-primary)" }}>
                      <div className="flex items-center gap-1.5">
                        {m.name}
                        {m.is_admin && (
                          <span
                            className="text-[10px] uppercase tracking-wider font-medium px-1.5 py-0.5 rounded"
                            style={{ background: "var(--bg-tertiary)", color: "var(--text-muted)" }}
                            title={`Excluded from the team ${statMode}`}
                          >
                            Admin
                          </span>
                        )}
                      </div>
                    </td>
                    {metricColumns.map((col) => (
                      <td key={col.key} className="text-right px-4 py-2.5 font-mono" style={{ color: "var(--text-primary)" }}>
                        {formatCell(col.decimals, m.metrics[col.key])}
                      </td>
                    ))}
                    <td className="px-4 py-2.5 text-right">
                      <button
                        onClick={() => router.push(`/team?as=${encodeURIComponent(m.email)}`)}
                        title="View as this rep"
                        className="inline-flex items-center gap-1 text-xs rounded px-2 py-1 transition-colors hover:bg-[var(--bg-tertiary)] cursor-pointer"
                        style={{ color: "var(--accent)" }}
                      >
                        <Eye size={12} />
                        View
                      </button>
                    </td>
                  </tr>
                ))}
                {filteredMembers.length === 0 && (
                  <tr>
                    <td colSpan={metricColumns.length + 2} className="px-4 py-6 text-center text-sm" style={{ color: "var(--text-muted)" }}>
                      No members match this filter for this period.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        ) : null}
        <p className="text-xs mb-8 -mt-6" style={{ color: "var(--text-muted)" }}>
          Admins are excluded from the Team {statMode === "median" ? "Median" : "Average"} calculation.
        </p>

        <div className="max-w-md rounded-lg px-4 py-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <h3 className="text-xs uppercase tracking-wider font-medium mb-3" style={{ color: "var(--text-muted)" }}>
            Team-Dashboard Admins
          </h3>

          {adminsLoading ? (
            <div className="flex items-center gap-2 py-2" style={{ color: "var(--text-muted)" }}>
              <Loader2 size={14} className="animate-spin" />
              <span className="text-xs">Loading admins…</span>
            </div>
          ) : (
            <div className="space-y-1.5 mb-3">
              {admins.map((email) => (
                <div key={email} className="flex items-center justify-between gap-2">
                  <span className="text-sm" style={{ color: "var(--text-primary)" }}>{email}</span>
                  <button
                    onClick={() => handleRemoveAdmin(email)}
                    disabled={adminActionBusy}
                    title="Remove admin"
                    className="p-1 rounded transition-colors hover:bg-[var(--bg-tertiary)] disabled:opacity-40 cursor-pointer"
                    style={{ color: "var(--text-muted)" }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {adminActionError && (
            <p className="text-xs mb-2 text-red-500">{adminActionError}</p>
          )}

          <div className="flex items-center gap-2">
            <input
              type="email"
              placeholder="name@langchain.dev"
              value={newAdminEmail}
              onChange={(e) => setNewAdminEmail(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleAddAdmin(); }}
              style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              className="flex-1 text-sm rounded px-3 py-1.5 focus:outline-none placeholder:text-[var(--text-muted)]"
            />
            <button
              onClick={handleAddAdmin}
              disabled={adminActionBusy || !newAdminEmail.trim()}
              style={{ background: "var(--accent)", color: "white" }}
              className="flex items-center gap-1 text-sm rounded px-3 py-1.5 transition-opacity hover:opacity-80 disabled:opacity-40 cursor-pointer"
            >
              <Plus size={14} />
              Add
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
