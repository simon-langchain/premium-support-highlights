"use client";

import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import type { MonthlyMetric } from "@/lib/api";

const PERIOD_TITLES: Record<string, string> = {
  "7d": "7-Day Ticket Trend",
  "1m": "30-Day Ticket Trend",
  "6m": "6-Month Ticket Trend",
  "1y": "12-Month Ticket Trend",
};

interface TrendChartProps {
  data: MonthlyMetric[];
  period?: string;
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
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
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
          {entry.name === "tickets_raised" ? "Tickets Raised" : "Closed Tickets"}:{" "}
          <span className="font-bold">{entry.value}</span>
        </p>
      ))}
    </div>
  );
}

export default function TrendChart({ data, period = "6m" }: TrendChartProps) {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const isDark = !mounted || resolvedTheme === "dark"; // default dark until mounted

  const gridColor  = isDark ? "#2a3045" : "#e2e8f0";
  const tickColor  = isDark ? "#64748b" : "#94a3b8";
  const axisColor  = isDark ? "#1b2030" : "#e2e8f0";

  return (
    <div
      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
      className="rounded-lg px-4 pt-4 pb-2"
    >
      <h3 style={{ color: "var(--text-muted)" }} className="text-xs uppercase tracking-wider font-medium mb-4">
        {PERIOD_TITLES[period] ?? "Ticket Trend"}
      </h3>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
          <defs>
            <linearGradient id="colorRaised" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="#006ddd" stopOpacity={0.25} />
              <stop offset="95%" stopColor="#006ddd" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="colorOpen" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="#22c55e" stopOpacity={0.2} />
              <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
          <XAxis
            dataKey="month"
            tick={{ fill: tickColor, fontSize: 11 }}
            axisLine={{ stroke: axisColor }}
            tickLine={false}
            interval={period === "1m" ? 4 : "preserveStartEnd"}
          />
          <YAxis tick={{ fill: tickColor, fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
          <Tooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: 11, color: tickColor, paddingTop: 8 }}
            formatter={(value) => value === "tickets_raised" ? "Tickets Raised" : "Closed Tickets"}
          />
          <Area type="monotone" dataKey="tickets_raised" stroke="#006ddd" strokeWidth={2} fill="url(#colorRaised)" dot={{ fill: "#006ddd", r: 3 }} activeDot={{ r: 5, fill: "#006ddd" }} />
          <Area type="monotone" dataKey="closed_tickets" stroke="#22c55e" strokeWidth={2} fill="url(#colorOpen)"   dot={{ fill: "#22c55e", r: 3 }} activeDot={{ r: 5, fill: "#22c55e" }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
