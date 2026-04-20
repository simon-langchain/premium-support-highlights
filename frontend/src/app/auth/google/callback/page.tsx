"use client";

import { useEffect, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function CallbackHandler() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = useState("");

  useEffect(() => {
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    const errorParam = searchParams.get("error");

    if (errorParam) {
      setError("Google sign-in was cancelled or failed. Please try again.");
      return;
    }

    if (!code || !state) {
      setError("Missing authentication parameters.");
      return;
    }

    fetch("/api/auth/google/callback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, state }),
    })
      .then(async (res) => {
        if (res.ok) {
          router.push("/");
        } else {
          const data = await res.json().catch(() => ({})) as { detail?: string };
          setError(data.detail || "Authentication failed. Please try again.");
        }
      })
      .catch(() => {
        setError("Something went wrong. Please try again.");
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) {
    return (
      <div
        className="min-h-screen flex items-center justify-center px-4"
        style={{ background: "var(--bg-base)" }}
      >
        <div
          className="w-full max-w-sm rounded-xl px-8 py-8"
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
        >
          <p className="text-sm text-red-500 mb-4">{error}</p>
          <a
            href="/login"
            className="text-sm text-[#006ddd] hover:underline"
          >
            Back to sign in
          </a>
        </div>
      </div>
    );
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center"
      style={{ background: "var(--bg-base)" }}
    >
      <p className="text-sm" style={{ color: "var(--text-muted)" }}>
        Signing you in...
      </p>
    </div>
  );
}

export default function GoogleCallbackPage() {
  return (
    <Suspense
      fallback={
        <div
          className="min-h-screen flex items-center justify-center"
          style={{ background: "var(--bg-base)" }}
        >
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            Signing you in...
          </p>
        </div>
      }
    >
      <CallbackHandler />
    </Suspense>
  );
}
