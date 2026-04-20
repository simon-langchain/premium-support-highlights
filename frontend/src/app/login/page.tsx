"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

const Logo = () => (
  <svg width="36" height="36" viewBox="0 0 128 128" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M40.1024 85.0722C47.6207 77.5537 51.8469 67.3453 51.8469 56.7136C51.8469 46.0818 47.617 35.8734 40.1024 28.355L11.7446 0C4.22995 7.5185 0 17.7269 0 28.3586C0 38.9903 4.22995 49.1987 11.7446 56.7172L40.0987 85.0722H40.1024Z" fill="#006ddd" />
    <path d="M99.4385 87.698C91.9239 80.1832 81.7121 75.9531 71.0844 75.9531C60.4566 75.9531 50.2448 80.1832 42.7266 87.698L71.0844 116.057C78.599 123.571 88.8107 127.802 99.4421 127.802C110.074 127.802 120.282 123.571 127.8 116.057L99.4421 87.698H99.4385Z" fill="#006ddd" />
    <path d="M11.8146 115.987C19.3329 123.502 29.541 127.732 40.1724 127.732V87.6289H0.0664062C0.0700559 98.2606 4.29635 108.469 11.8146 115.987Z" fill="#006ddd" />
    <path d="M110.387 45.7684C102.869 38.2535 92.6608 34.0198 82.0258 34.0234C71.3943 34.0234 61.1863 38.2535 53.668 45.772L82.0258 74.1306L110.387 45.7684Z" fill="#006ddd" />
  </svg>
);

const GoogleIcon = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
    <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
    <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.258c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/>
    <path d="M3.964 10.707A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.707V4.961H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.039l3.007-2.332z" fill="#FBBC05"/>
    <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.961L3.964 7.293C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
  </svg>
);

type OtpStep = "email" | "code";
type OtpStatus = "idle" | "loading" | "not_authorized" | "rate_limited" | "error";

export default function LoginPage() {
  const router = useRouter();

  // Google OAuth state
  const [googleLoading, setGoogleLoading] = useState(false);
  const [googleError, setGoogleError] = useState("");

  // OTP state
  const [showOtp, setShowOtp] = useState(false);
  const [otpStep, setOtpStep] = useState<OtpStep>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [otpStatus, setOtpStatus] = useState<OtpStatus>("idle");
  const [otpError, setOtpError] = useState("");

  async function handleGoogleSignIn() {
    setGoogleLoading(true);
    setGoogleError("");
    try {
      const res = await fetch("/api/auth/google/start");
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json() as { url: string };
      window.location.href = data.url;
    } catch (err) {
      setGoogleLoading(false);
      setGoogleError(err instanceof Error ? err.message : "Something went wrong.");
    }
  }

  async function handleEmailSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setOtpStatus("loading");
    setOtpError("");
    try {
      const res = await fetch("/api/auth/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase() }),
      });
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json() as { status: string };
      if (data.status === "sent") {
        setOtpStatus("idle");
        setOtpStep("code");
      } else if (data.status === "not_authorized") {
        setOtpStatus("not_authorized");
      } else if (data.status === "rate_limited") {
        setOtpStatus("rate_limited");
      } else {
        setOtpStatus("error");
        setOtpError("Unexpected response. Please try again.");
      }
    } catch (err) {
      setOtpStatus("error");
      setOtpError(err instanceof Error ? err.message : "Something went wrong.");
    }
  }

  async function handleCodeSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setOtpStatus("loading");
    setOtpError("");
    try {
      const res = await fetch("/api/auth/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase(), code: code.trim() }),
      });
      if (res.status === 401) {
        setOtpStatus("error");
        setOtpError("Invalid or expired code. Please try again.");
        return;
      }
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      router.push("/");
    } catch (err) {
      setOtpStatus("error");
      setOtpError(err instanceof Error ? err.message : "Something went wrong.");
    }
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center px-4"
      style={{ background: "var(--bg-base)" }}
    >
      <div
        className="w-full max-w-sm rounded-xl px-8 py-8"
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
      >
        <div className="flex items-center gap-2.5 mb-6">
          <Logo />
          <span className="font-semibold text-base" style={{ color: "var(--text-primary)" }}>
            Support Highlights
          </span>
        </div>

        <h1 className="text-xl font-bold mb-6" style={{ color: "var(--text-primary)" }}>
          Sign in
        </h1>

        {/* Google sign-in — primary method */}
        <button
          onClick={handleGoogleSignIn}
          disabled={googleLoading}
          className="w-full flex items-center justify-center gap-3 rounded-lg py-2.5 text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          style={{
            background: "var(--bg-tertiary)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
          }}
        >
          {googleLoading ? <Loader2 size={16} className="animate-spin" /> : <GoogleIcon />}
          {googleLoading ? "Redirecting..." : "Sign in with Google"}
        </button>

        {googleError && (
          <p className="mt-3 text-sm rounded-lg px-3 py-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/40 text-red-700 dark:text-red-400">
            {googleError}
          </p>
        )}

        {/* Divider */}
        <div className="flex items-center gap-3 my-5">
          <div className="flex-1 h-px" style={{ background: "var(--border)" }} />
          <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>or</span>
          <div className="flex-1 h-px" style={{ background: "var(--border)" }} />
        </div>

        {/* OTP sign-in — secondary method */}
        {!showOtp ? (
          <button
            onClick={() => setShowOtp(true)}
            className="w-full text-sm py-2 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
            style={{
              background: "transparent",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
            }}
          >
            Sign in with email code
          </button>
        ) : otpStep === "email" ? (
          <>
            <form onSubmit={handleEmailSubmit} className="flex flex-col gap-3">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@langchain.dev"
                required
                autoFocus
                className="w-full rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[#006ddd]"
                style={{
                  background: "var(--bg-tertiary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                }}
              />
              <button
                type="submit"
                disabled={otpStatus === "loading"}
                className="w-full rounded-lg py-2 text-sm font-medium text-white bg-[#006ddd] hover:bg-[#0058b8] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {otpStatus === "loading" ? "Sending..." : "Send code"}
              </button>
            </form>

            {otpStatus === "not_authorized" && (
              <p className="mt-3 text-sm rounded-lg px-3 py-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/40 text-amber-800 dark:text-amber-300">
                Your email isn&apos;t on the access list. Reach out to{" "}
                <a href="mailto:support@langchain.dev" className="underline">support@langchain.dev</a>{" "}
                to request access.
              </p>
            )}
            {otpStatus === "rate_limited" && (
              <p className="mt-3 text-sm rounded-lg px-3 py-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/40 text-amber-800 dark:text-amber-300">
                Too many requests. Please wait 15 minutes before trying again.
              </p>
            )}
            {otpStatus === "error" && (
              <p className="mt-3 text-sm rounded-lg px-3 py-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/40 text-red-700 dark:text-red-400">
                {otpError}
              </p>
            )}

          </>
        ) : (
          <>
            <p className="text-sm mb-3" style={{ color: "var(--text-muted)" }}>
              We sent a 6-digit code to{" "}
              <strong style={{ color: "var(--text-primary)" }}>{email}</strong>.
              It expires in 15 minutes.
            </p>
            <form onSubmit={handleCodeSubmit} className="flex flex-col gap-3">
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                placeholder="000000"
                required
                autoFocus
                className="w-full rounded-lg px-3 py-2 text-sm text-center tracking-[0.4em] outline-none focus:ring-2 focus:ring-[#006ddd]"
                style={{
                  background: "var(--bg-tertiary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                  fontSize: "1.25rem",
                }}
              />
              <button
                type="submit"
                disabled={otpStatus === "loading" || code.length !== 6}
                className="w-full rounded-lg py-2 text-sm font-medium text-white bg-[#006ddd] hover:bg-[#0058b8] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {otpStatus === "loading" ? "Verifying..." : "Sign in"}
              </button>
            </form>

            {otpStatus === "error" && (
              <p className="mt-3 text-sm rounded-lg px-3 py-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/40 text-red-700 dark:text-red-400">
                {otpError}
              </p>
            )}

            <button
              onClick={() => { setOtpStep("email"); setCode(""); setOtpStatus("idle"); setOtpError(""); }}
              className="mt-4 w-full text-sm text-center"
              style={{ color: "var(--text-caption)" }}
            >
              Use a different email
            </button>
          </>
        )}
      </div>
    </div>
  );
}
