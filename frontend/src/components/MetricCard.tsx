interface MetricCardProps {
  label: string;
  value: string | number | null;
  delta?: number;
  unit?: string;
  sub?: string;
  /** Trailing text after the delta value. Defaults to "vs last period". */
  deltaLabel?: string;
  /** Flip green/red coloring so a lower delta (e.g. faster response time) renders green. */
  lowerIsBetter?: boolean;
}

export default function MetricCard({ label, value, delta, unit, sub, deltaLabel, lowerIsBetter }: MetricCardProps) {
  const displayValue = value === null || value === undefined ? "—" : value;
  const isGood = delta !== undefined && (lowerIsBetter ? delta < 0 : delta > 0);
  const isBad = delta !== undefined && (lowerIsBetter ? delta > 0 : delta < 0);

  return (
    <div
      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
      className="rounded-lg px-4 py-4 flex flex-col items-center text-center"
    >
      <p style={{ color: "var(--text-muted)" }} className="text-xs uppercase tracking-wider font-medium mb-2">
        {label}
      </p>
      <div className="flex items-end gap-1.5">
        <span style={{ color: "var(--text-primary)" }} className="text-2xl font-bold leading-none">
          {displayValue}
        </span>
        {unit && displayValue !== "—" && (
          <span style={{ color: "var(--text-muted)" }} className="text-sm mb-0.5">{unit}</span>
        )}
      </div>
      {sub && (
        <p style={{ color: "var(--text-muted)" }} className="text-xs mt-1.5">{sub}</p>
      )}
      {delta !== undefined && (
        <p
          className={`text-xs mt-1.5 font-medium ${
            isGood ? "text-green-500" : isBad ? "text-red-500" : ""
          }`}
          style={!isGood && !isBad ? { color: "var(--text-muted)" } : undefined}
        >
          {delta > 0 ? "+" : ""}{delta.toFixed(1)}{unit ? ` ${unit}` : ""} {deltaLabel ?? "vs last period"}
        </p>
      )}
    </div>
  );
}
