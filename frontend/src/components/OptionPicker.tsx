"use client";

import { useState, useEffect, useRef } from "react";
import { ChevronDown, Check } from "lucide-react";

export default function OptionPicker({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const selected = options.find((o) => o.value === value);

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          color: "var(--text-primary)",
        }}
        className="w-full flex items-center justify-between text-sm rounded px-2 py-1.5 focus:outline-none cursor-pointer hover:border-[var(--border-hover)] transition-colors"
      >
        <span className="truncate">{selected?.label ?? value}</span>
        <ChevronDown size={13} className={`flex-shrink-0 ml-1 transition-transform ${open ? "rotate-180" : ""}`} style={{ color: "var(--text-muted)" }} />
      </button>

      {open && (
        <div
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          }}
          className="absolute left-0 right-0 top-[calc(100%+4px)] z-50 rounded-lg overflow-hidden"
        >
          {options.map((o) => {
            const isSelected = o.value === value;
            return (
              <button
                key={o.value}
                onClick={() => { onChange(o.value); setOpen(false); }}
                style={{
                  color: isSelected ? "#006ddd" : "var(--text-primary)",
                  background: isSelected ? "rgba(0,109,221,0.08)" : "transparent",
                }}
                className="w-full flex items-center justify-between px-3 py-1.5 text-sm text-left hover:bg-[var(--bg-tertiary)] transition-colors"
              >
                <span>{o.label}</span>
                {isSelected && <Check size={13} className="flex-shrink-0 ml-2" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
