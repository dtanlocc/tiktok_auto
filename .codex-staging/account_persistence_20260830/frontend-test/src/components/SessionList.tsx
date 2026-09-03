import { Eye, EyeOff, Globe2, MonitorUp, Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { BrowserSession } from "../features/sessions/types";
import { StatusBadge } from "./StatusBadge";

interface Props {
  sessions: BrowserSession[];
  selectedId: string | null;
  loading: boolean;
  onSelect: (id: string) => void;
}

export function SessionList({ sessions, selectedId, loading, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const value = query.trim().toLowerCase();
    if (!value) return sessions;
    return sessions.filter((session) =>
      [session.display_name, session.current_url, session.status].some((field) => field.toLowerCase().includes(value)),
    );
  }, [query, sessions]);

  return (
    <section className="panel sessions-panel" aria-labelledby="sessions-title">
      <div className="panel-header">
        <div><span className="eyebrow">Fleet</span><h2 id="sessions-title">Browser sessions</h2></div>
        <span className="count-chip">{sessions.length}</span>
      </div>
      <label className="search-field">
        <Search aria-hidden="true" /><span className="sr-only">Search sessions</span>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search sessions" />
      </label>
      <div className="session-list" aria-busy={loading}>
        {loading && sessions.length === 0 && (
          <div className="empty-state"><span className="skeleton skeleton--title" /><span className="skeleton" /><span className="skeleton" /></div>
        )}
        {!loading && filtered.length === 0 && (
          <div className="empty-state">
            <MonitorUp aria-hidden="true" />
            <strong>{sessions.length ? "No matching sessions" : "No sessions yet"}</strong>
            <p>{sessions.length ? "Try another search term." : "Create a hidden or visible session to begin."}</p>
          </div>
        )}
        {filtered.map((session) => (
          <button type="button" key={session.id} className={selectedId === session.id ? "session-row is-selected" : "session-row"} aria-pressed={selectedId === session.id} onClick={() => onSelect(session.id)}>
            <span className="session-row__icon" aria-hidden="true">{session.mode === "hidden" ? <EyeOff /> : <Eye />}</span>
            <span className="session-row__body">
              <span className="session-row__topline"><strong>{session.display_name}</strong><StatusBadge status={session.status} /></span>
              <span className="session-row__url"><Globe2 aria-hidden="true" />{session.current_url}</span>
              <span className="session-row__meta">{session.locale} · {session.timezone}</span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
