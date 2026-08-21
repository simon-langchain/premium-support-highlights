"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Presentation, Users } from "lucide-react";
import { fetchMe } from "@/lib/api";

const Logo = () => (
  <svg width="32" height="32" viewBox="0 0 128 128" fill="none" xmlns="http://www.w3.org/2000/svg" className="flex-shrink-0">
    <path d="M40.1024 85.0722C47.6207 77.5537 51.8469 67.3453 51.8469 56.7136C51.8469 46.0818 47.617 35.8734 40.1024 28.355L11.7446 0C4.22995 7.5185 0 17.7269 0 28.3586C0 38.9903 4.22995 49.1987 11.7446 56.7172L40.0987 85.0722H40.1024Z" fill="#006ddd" />
    <path d="M99.4385 87.698C91.9239 80.1832 81.7121 75.9531 71.0844 75.9531C60.4566 75.9531 50.2448 80.1832 42.7266 87.698L71.0844 116.057C78.599 123.571 88.8107 127.802 99.4421 127.802C110.074 127.802 120.282 123.571 127.8 116.057L99.4421 87.698H99.4385Z" fill="#006ddd" />
    <path d="M11.8146 115.987C19.3329 123.502 29.541 127.732 40.1724 127.732V87.6289H0.0664062C0.0700559 98.2606 4.29635 108.469 11.8146 115.987Z" fill="#006ddd" />
    <path d="M110.387 45.7684C102.869 38.2535 92.6608 34.0198 82.0258 34.0234C71.3943 34.0234 61.1863 38.2535 53.668 45.772L82.0258 74.1306L110.387 45.7684Z" fill="#006ddd" />
  </svg>
);

export default function ChooserPage() {
  const router = useRouter();
  const [isSupportTeam, setIsSupportTeam] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (cancelled) return;
        if (me?.is_support_team_member) {
          setIsSupportTeam(true);
        } else {
          // Not on the Support team, or the call failed — fail open to the
          // customer dashboard rather than showing a chooser they can't use.
          router.replace("/customers");
        }
      })
      .catch(() => {
        if (!cancelled) router.replace("/customers");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (!isSupportTeam) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg-base)" }}>
        <Loader2 size={20} className="animate-spin" style={{ color: "var(--text-muted)" }} />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4" style={{ background: "var(--bg-base)" }}>
      <div className="w-full max-w-md">
        <div className="flex items-center justify-center gap-2.5 mb-8">
          <Logo />
          <span className="font-semibold text-lg" style={{ color: "var(--text-primary)" }}>
            Support Highlights
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <button
            onClick={() => router.push("/team")}
            className="flex flex-col items-start gap-3 rounded-xl px-6 py-6 text-left transition-colors hover:bg-[var(--bg-tertiary)] cursor-pointer"
            style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
          >
            <div
              className="flex items-center justify-center w-10 h-10 rounded-lg"
              style={{ background: "rgba(0,109,221,0.12)", color: "var(--accent)" }}
            >
              <Users size={20} />
            </div>
            <div>
              <h2 className="text-base font-semibold mb-1" style={{ color: "var(--text-primary)" }}>
                Internal Metrics
              </h2>
              <p className="text-sm leading-relaxed" style={{ color: "var(--text-muted)" }}>
                Your performance vs. the Support team average.
              </p>
            </div>
          </button>

          <button
            onClick={() => router.push("/customers")}
            className="flex flex-col items-start gap-3 rounded-xl px-6 py-6 text-left transition-colors hover:bg-[var(--bg-tertiary)] cursor-pointer"
            style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
          >
            <div
              className="flex items-center justify-center w-10 h-10 rounded-lg"
              style={{ background: "rgba(0,109,221,0.12)", color: "var(--accent)" }}
            >
              <Presentation size={20} />
            </div>
            <div>
              <h2 className="text-base font-semibold mb-1" style={{ color: "var(--text-primary)" }}>
                Customer Dashboards
              </h2>
              <p className="text-sm leading-relaxed" style={{ color: "var(--text-muted)" }}>
                Support metrics and AI summaries for premium accounts.
              </p>
            </div>
          </button>
        </div>
      </div>
    </div>
  );
}
