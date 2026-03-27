interface MetricCardProps {
  label: string;
  value: string | number | null;
  delta?: number;
  unit?: string;
}

export default function MetricCard({ label, value, delta, unit }: MetricCardProps) {
  const displayValue = value === null || value === undefined ? "—" : value;

  return (
    <div
      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
      className="rounded-lg px-4 py-4"
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
      {delta !== undefined && (
        <p
          className={`text-xs mt-1.5 font-medium ${
            delta > 0 ? "text-green-500" : delta < 0 ? "text-red-500" : ""
          }`}
          style={delta === 0 ? { color: "var(--text-muted)" } : undefined}
        >
          {delta > 0 ? "+" : ""}{delta.toFixed(1)}{unit ? ` ${unit}` : ""} vs last period
        </p>
      )}
    </div>
  );
}
