"use client";

import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

export interface TrendMiniChartPoint {
  label: string;
  me: number | null;
  avg: number | null;
}

interface TrendMiniChartProps {
  title: string;
  data: TrendMiniChartPoint[];
  unit?: string;
  /** Label for the "avg" series in the tooltip/legend. Defaults to "Team avg" —
   * override when this chart is plotting a different stat (e.g. "Team median"). */
  avgLabel?: string;
}

/**
 * Recharts XAxis `interval`: show ~8 evenly-spaced labels regardless of how
 * many buckets there are, rather than special-casing by period — bucket
 * count now varies independently of period (day/week/month granularity is
 * user-selectable), so a fixed number of buckets can no longer be assumed
 * from the period alone.
 */
function labelInterval(bucketCount: number): number {
  if (bucketCount <= 8) return 0;
  return Math.ceil(bucketCount / 8) - 1;
}

interface TooltipPayload {
  value: number;
  name: string;
  color: string;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: TooltipPayload[];
  label?: string;
  unit?: string;
  avgLabel?: string;
}

function CustomTooltip({ active, payload, label, unit, avgLabel = "Team avg" }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div
      style={{
        background: "var(--bg-tertiary)",
        border: "1px solid var(--border-hover)",
        color: "var(--text-primary)",
      }}
      className="rounded px-3 py-2 text-xs shadow-lg"
    >
      <p className="font-semibold mb-1">{label}</p>
      {payload.map((entry) => (
        <p key={entry.name} style={{ color: entry.color }}>
          {entry.name === "me" ? "You" : avgLabel}:{" "}
          <span className="font-bold">{entry.value === null || entry.value === undefined ? "—" : entry.value}{unit ? ` ${unit}` : ""}</span>
        </p>
      ))}
    </div>
  );
}

export default function TrendMiniChart({ title, data, unit, avgLabel = "Team avg" }: TrendMiniChartProps) {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const isDark = !mounted || resolvedTheme === "dark";

  const gridColor = isDark ? "#2a3045" : "#e2e8f0";
  const tickColor = isDark ? "#64748b" : "#94a3b8";
  const axisColor = isDark ? "#1b2030" : "#e2e8f0";

  return (
    <div
      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
      className="rounded-lg px-4 pt-4 pb-2"
    >
      <h3 style={{ color: "var(--text-muted)" }} className="text-xs uppercase tracking-wider font-medium mb-4">
        {title}
      </h3>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
          <XAxis
            dataKey="label"
            tick={{ fill: tickColor, fontSize: 11 }}
            axisLine={{ stroke: axisColor }}
            tickLine={false}
            interval={labelInterval(data.length)}
          />
          <YAxis tick={{ fill: tickColor, fontSize: 11 }} axisLine={false} tickLine={false} />
          <Tooltip content={<CustomTooltip unit={unit} avgLabel={avgLabel} />} />
          <Legend
            wrapperStyle={{ fontSize: 11, color: tickColor, paddingTop: 4 }}
            formatter={(value) => value === "me" ? "You" : avgLabel}
          />
          <Line type="monotone" dataKey="me" stroke="#006ddd" strokeWidth={2} dot={{ fill: "#006ddd", r: 2.5 }} activeDot={{ r: 5, fill: "#006ddd" }} connectNulls />
          <Line type="monotone" dataKey="avg" stroke="#94a3b8" strokeWidth={2} strokeDasharray="4 3" dot={false} activeDot={{ r: 4, fill: "#94a3b8" }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
