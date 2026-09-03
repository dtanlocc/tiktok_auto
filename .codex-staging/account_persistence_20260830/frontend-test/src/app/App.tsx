import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, Layers3, MonitorUp, Plus, ServerCog, ShieldCheck, Users } from "lucide-react";
import { AccountDashboard } from "../components/AccountDashboard";
import { EventTimeline } from "../components/EventTimeline";
import { BlankBrowserPanel } from "../components/BlankBrowserPanel";
import { AutomationBatchPanel } from "../components/AutomationBatchPanel";
import { SignupTestPanel } from "../components/SignupTestPanel";
import { LiveViewer } from "../components/LiveViewer";
import { NewSessionDialog } from "../components/NewSessionDialog";
import { SessionList } from "../components/SessionList";
import {
  closeSession, createSession, eventSocketUrl, listSessions,
  navigateSession, parseEvent, uploadFile,
} from "../features/sessions/api";
import type {
  BrowserSession,
  CreateSessionRequest,
  ManagedAccountRecord,
  SessionEvent,
  SignupDraft,
} from "../features/sessions/types";

const ACCOUNT_RECORDS_KEY = "ibs.managed-account-records.v1";

function loadAccountRecords(): ManagedAccountRecord[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(ACCOUNT_RECORDS_KEY) ?? "[]") as unknown;
    return Array.isArray(parsed) ? parsed as ManagedAccountRecord[] : [];
  } catch {
    return [];
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The operation could not be completed.";
}

export function App() {
  const [sessions, setSessions] = useState<BrowserSession[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [newDialogOpen, setNewDialogOpen] = useState(false);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [blankError, setBlankError] = useState<string | null>(null);
  const [signupPrefill, setSignupPrefill] = useState<SignupDraft | null>(null);
  const [accountRecords, setAccountRecords] = useState<ManagedAccountRecord[]>(loadAccountRecords);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const values = await listSessions();
      if (!alive.current) return;
      setSessions(values);
      setSelectedId((current) => {
        if (current && values.some((item) => item.id === current)) return current;
        return values.find((item) => item.status !== "closed")?.id ?? values[0]?.id ?? null;
      });
      setPageError(null);
    } catch (error) {
      if (alive.current) setPageError(errorMessage(error));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 5_000);
    return () => { alive.current = false; window.clearInterval(timer); };
  }, [refresh]);

  useEffect(() => {
    window.localStorage.setItem(ACCOUNT_RECORDS_KEY, JSON.stringify(accountRecords));
  }, [accountRecords]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let disposed = false;
    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(eventSocketUrl());
      socket.onmessage = (message) => {
        const event = typeof message.data === "string" ? parseEvent(message.data) : null;
        if (!event) return;
        setEvents((current) => [event, ...current].slice(0, 80));
        void refresh();
      };
      socket.onclose = () => { if (!disposed) timer = window.setTimeout(connect, 2_500); };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => { disposed = true; window.clearTimeout(timer); socket?.close(); };
  }, [refresh]);

  const selected = useMemo(() => sessions.find((session) => session.id === selectedId) ?? null, [selectedId, sessions]);
  const blankSession = useMemo(
    () => sessions.find(
      (session) => session.tenant_id === "blank-browser" && session.status !== "closed",
    ) ?? null,
    [sessions],
  );
  const readyCount = sessions.filter((session) => session.status === "running").length;
  const hiddenCount = sessions.filter((session) => session.mode === "hidden" && session.status !== "closed").length;
  const failedCount = sessions.filter((session) => session.status === "failed").length;

  const handleCreate = async (payload: CreateSessionRequest) => {
    setDialogBusy(true);
    setDialogError(null);
    try {
      const session = await createSession(payload);
      setNewDialogOpen(false);
      setSelectedId(session.id);
      await refresh();
    } catch (error) {
      setDialogError(errorMessage(error));
    } finally {
      setDialogBusy(false);
    }
  };

  const act = async (name: string, operation: () => Promise<unknown>) => {
    setBusyAction(name);
    setPageError(null);
    try {
      await operation();
      await refresh();
    } catch (error) {
      setPageError(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Primary navigation">
        <div className="brand-mark" aria-hidden="true"><Layers3 /></div>
        <nav>
          <a className="nav-item is-active" href="#main-content" aria-current="page"><MonitorUp aria-hidden="true" /><span>Sessions</span></a>
          <a className="nav-item" href="#automation-batches"><Layers3 aria-hidden="true" /><span>Batches</span></a>
          <a className="nav-item" href="#accounts"><Users aria-hidden="true" /><span>Accounts</span></a>
          <a className="nav-item" href="#signup-test"><ShieldCheck aria-hidden="true" /><span>Signup test</span></a>
          <a className="nav-item" href="#events-title"><Activity aria-hidden="true" /><span>Activity</span></a>
        </nav>
        <div className="sidebar-footer" title="Runtime isolation enabled"><ShieldCheck aria-hidden="true" /><span>Isolated</span></div>
      </aside>
      <div className="page">
        <header className="topbar">
          <div className="wordmark"><span>Invisible</span><strong>Browser Studio</strong></div>
          <div className="topbar-actions">
            <span className="runtime-chip"><ServerCog aria-hidden="true" />Local runtime</span>
            <button className="button button--primary" type="button" onClick={() => { setDialogError(null); setNewDialogOpen(true); }}>
              <Plus aria-hidden="true" />New session
            </button>
          </div>
        </header>
        <main id="main-content" tabIndex={-1}>
          <section className="page-intro" aria-labelledby="page-title">
            <div>
              <span className="eyebrow">Operations console</span>
              <h1 id="page-title">Browser fleet</h1>
              <p>Run isolated background sessions, inspect live output and keep automation work off your desktop.</p>
            </div>
            <div className="metric-strip" aria-label="Session metrics">
              <div><span>Running</span><strong>{readyCount}</strong></div>
              <div><span>Hidden</span><strong>{hiddenCount}</strong></div>
              <div><span>Failed</span><strong>{failedCount}</strong></div>
            </div>
          </section>
          {pageError && <div className="page-alert" role="alert"><span>{pageError}</span><button type="button" onClick={() => void refresh()}>Try again</button></div>}
          <BlankBrowserPanel
            session={blankSession}
            busy={busyAction === "blank"}
            error={blankError}
            onStart={async (payload) => {
              setBusyAction("blank");
              setBlankError(null);
              try {
                const created = await createSession(payload);
                setSelectedId(created.id);
                await refresh();
              } catch (error) {
                setBlankError(errorMessage(error));
              } finally {
                setBusyAction(null);
              }
            }}
            onStop={async () => {
              if (!blankSession) return;
              setBusyAction("blank");
              setBlankError(null);
              try {
                await closeSession(blankSession.id);
                await refresh();
              } catch (error) {
                setBlankError(errorMessage(error));
              } finally {
                setBusyAction(null);
              }
            }}
          />
          <AutomationBatchPanel
            sessions={sessions}
            onSelectSession={setSelectedId}
            onSessionsChanged={refresh}
          />
          <AccountDashboard
            records={accountRecords}
            onUseMailbox={(draft) => {
              setSignupPrefill(draft);
              window.location.hash = "signup-test";
            }}
          />
          <SignupTestPanel
            sessions={sessions}
            onSelectSession={setSelectedId}
            onSessionsChanged={refresh}
            prefill={signupPrefill}
            onResult={(record) => setAccountRecords((current) => [
              record,
              ...current.filter((item) => item.id !== record.id),
            ].slice(0, 100))}
          />
          <div className="workspace-grid">
            <SessionList sessions={sessions} selectedId={selectedId} loading={loading} onSelect={setSelectedId} />
            <LiveViewer
              session={selected}
              busyAction={busyAction}
              onNavigate={(url) => selected ? act("navigate", () => navigateSession(selected.id, url)).then(() => undefined) : Promise.resolve()}
              onUpload={(file) => selected ? act("upload", () => uploadFile(selected.id, file)).then(() => undefined) : Promise.resolve()}
              onClose={async () => {
                if (!selected || !window.confirm("Close “" + selected.display_name + "”? The browser process will be stopped.")) return;
                await act("close", () => closeSession(selected.id));
              }}
              onRefresh={refresh}
            />
            <EventTimeline events={events} />
          </div>
        </main>
      </div>
      <NewSessionDialog open={newDialogOpen} busy={dialogBusy} error={dialogError} onClose={() => setNewDialogOpen(false)} onSubmit={handleCreate} />
    </div>
  );
}
