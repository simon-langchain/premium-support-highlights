import type { MessageActivitySyncStatus } from "./api";

export const SYNC_STAGE_LABELS: Record<string, string> = {
  "7d": "last 7 days", "1m": "last 30 days", "3m": "last 3 months", "6m": "last 6 months", "1y": "last 12 months",
};

/** Human-readable summary of the background ticket-update-history backfill's progress. */
export function formatSyncStatus(s: MessageActivitySyncStatus): string {
  if (s.complete) return "Update history: fully synced";
  const parts: string[] = [];
  const readyStage = s.stages_completed[s.stages_completed.length - 1];
  if (readyStage) parts.push(`${SYNC_STAGE_LABELS[readyStage] ?? readyStage} ready`);
  if (s.stage) {
    const progress = s.current_stage_total > 0 ? ` (${s.current_stage_synced}/${s.current_stage_total})` : "";
    parts.push(`backfilling ${SYNC_STAGE_LABELS[s.stage] ?? s.stage}${progress}`);
  }
  return parts.length > 0 ? `Update history: ${parts.join(" · ")}` : "Update history: starting sync…";
}
