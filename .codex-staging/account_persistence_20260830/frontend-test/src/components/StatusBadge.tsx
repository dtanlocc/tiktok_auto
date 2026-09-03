import type { SessionStatus } from "../features/sessions/types";

const LABELS: Record<SessionStatus, string> = {
  queued: "Queued",
  starting: "Starting",
  running: "Running",
  closing: "Stopping",
  closed: "Stopped",
  failed: "Failed",
};

export function StatusBadge({ status }: { status: SessionStatus }) {
  return (
    <span className={"status-badge status-badge--" + status}>
      <span className="status-badge__dot" aria-hidden="true" />
      {LABELS[status]}
    </span>
  );
}
