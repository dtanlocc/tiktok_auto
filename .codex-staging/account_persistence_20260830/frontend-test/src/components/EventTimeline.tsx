import { Activity, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import type { SessionEvent } from "../features/sessions/types";

const icons = { info: Info, success: CheckCircle2, warning: AlertTriangle, error: AlertTriangle };

export function EventTimeline({ events }: { events: SessionEvent[] }) {
  return (
    <section className="panel event-panel" aria-labelledby="events-title">
      <div className="panel-header">
        <div><span className="eyebrow">Audit trail</span><h2 id="events-title">Recent activity</h2></div>
        <Activity aria-hidden="true" />
      </div>
      <div className="event-list" aria-live="polite" aria-atomic="false">
        {events.length === 0 && <div className="empty-state empty-state--compact"><p>Session events appear here without moving your scroll position.</p></div>}
        {events.slice(0, 80).map((event) => {
          const Icon = icons[event.severity];
          return (
            <article className={"event-row event-row--" + event.severity} key={event.id}>
              <Icon aria-hidden="true" />
              <div><strong>{event.message}</strong><span>{new Date(event.occurred_at).toLocaleTimeString()} · {event.type}</span></div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
