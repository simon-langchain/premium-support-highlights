"use client";

import { AlertCircle } from "lucide-react";

interface SlackInviteWarningProps {
  message: string;
}

/**
 * Renders a "bot isn't in this channel" message from the backend.
 *
 * Backend messages that follow the standard format (see
 * _slack_not_in_channel_message in main.py) are split into a summary line
 * plus a copyable `/invite @bot` code block; anything else falls back to a
 * plain error line.
 */
export default function SlackInviteWarning({ message }: SlackInviteWarningProps) {
  const inviteMatch = message.match(/(.*?\.).*?(\/invite\s+\S+)/);

  if (!inviteMatch) {
    return (
      <p className="text-xs mt-1.5 flex items-center gap-1" style={{ color: "#ef4444" }}>
        <AlertCircle size={11} />
        {message}
      </p>
    );
  }

  return (
    <div
      className="mt-2 rounded-md px-2.5 py-2 text-xs"
      style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)" }}
    >
      <div className="flex items-start gap-1.5 mb-2" style={{ color: "#ef4444" }}>
        <AlertCircle size={11} className="mt-0.5 flex-shrink-0" />
        <span>{inviteMatch[1]}</span>
      </div>
      <div style={{ color: "var(--text-muted)" }} className="mb-1">Run in that channel:</div>
      <code className="block px-2 py-1 rounded text-xs" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)" }}>
        {inviteMatch[2]}
      </code>
    </div>
  );
}
