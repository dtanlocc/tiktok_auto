import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";
import {
  Activity,
  Database,
  Eye,
  EyeOff,
  Gauge,
  Globe2,
  Layers3,
  Network,
  Play,
  RotateCw,
  ShieldCheck,
  Square,
  TimerReset,
  Upload,
} from "lucide-react";
import {
  cancelAutomationBatch,
  createAutomationBatch,
  getAutomationBatchPolicy,
  listAutomationBatches,
  retryAutomationBatch,
  startAutomationBatch,
} from "../features/sessions/api";
import type {
  AutomationBatch,
  AutomationBatchPolicy,
  BrowserMode,
  BrowserSession,
  CreateAutomationBatchRequest,
  ProxyConfig,
  SessionPhase,
} from "../features/sessions/types";
import { useLiveFrame } from "../features/sessions/useLiveFrame";
import { StatusBadge } from "./StatusBadge";

const DEFAULT_URL = "https://example.com";
const ACTIVE_BATCH_STATUSES = new Set(["running", "cancelling"]);
const RETRYABLE_BATCH_STATUSES = new Set([
  "completed", "completed_with_errors", "cancelled", "failed",
]);

const PHASE_LABELS: Record<SessionPhase, string> = {
  queued: "Queued",
  rotating_proxy: "Rotating IP",
  launching: "Launching",
  active: "Active",
  cleanup: "Cleaning",
  completed: "Completed",
  cancelled: "Cancelled",
  failed: "Failed",
};

interface Props {
  sessions: BrowserSession[];
  onSelectSession: (id: string) => void;
  onSessionsChanged: () => Promise<void>;
}

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : "The queue operation failed.";
}

function parseProxyRows(value: string): ProxyConfig[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line, index) => {
      const parts = line.split("|").map((part) => part.trim());
      if (parts.length !== 1 && parts.length !== 3) {
        throw new Error(`Proxy line ${index + 1} must be server or server|username|password.`);
      }
      const [server, username, password] = parts;
      if (!server) throw new Error(`Proxy line ${index + 1} has no server.`);
      if ((username && !password) || (!username && password)) {
        throw new Error(`Proxy line ${index + 1} must include both username and password.`);
      }
      return {
        server,
        username: username || undefined,
        password: password || undefined,
      };
    });
}

function BatchJobCard({
  session,
  onSelect,
}: {
  session: BrowserSession;
  onSelect: () => void;
}) {
  const frame = useLiveFrame(session.id, session.status === "running");
  return (
    <button className="batch-job-card" type="button" onClick={onSelect}>
      <span className="batch-job-card__preview">
        {frame.source ? (
          <img src={frame.source} alt="" />
        ) : (
          <span className="batch-job-card__placeholder" aria-hidden="true"><Activity /></span>
        )}
        <span className={"batch-stream-dot batch-stream-dot--" + frame.connection} />
      </span>
      <span className="batch-job-card__body">
        <span className="batch-job-card__title">{session.display_name}</span>
        <span className="batch-job-card__meta">{PHASE_LABELS[session.phase]}</span>
      </span>
      <StatusBadge status={session.status} />
    </button>
  );
}

export function AutomationBatchPanel({
  sessions,
  onSelectSession,
  onSessionsChanged,
}: Props) {
  const [batches, setBatches] = useState<AutomationBatch[]>([]);
  const [policy, setPolicy] = useState<AutomationBatchPolicy>({
    max_jobs: 50,
    max_concurrency: 8,
  });
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState("Persistent browser queue");
  const [url, setUrl] = useState(DEFAULT_URL);
  const [totalJobs, setTotalJobs] = useState(4);
  const [concurrency, setConcurrency] = useState(4);
  const [activeSeconds, setActiveSeconds] = useState(30);
  const [mode, setMode] = useState<BrowserMode>("hidden");
  const [useProxy, setUseProxy] = useState(false);
  const [proxyRows, setProxyRows] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const proxyFileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextBatches, nextPolicy] = await Promise.all([
        listAutomationBatches(),
        getAutomationBatchPolicy(),
      ]);
      setBatches(nextBatches);
      setPolicy(nextPolicy);
      setTotalJobs((value) => Math.min(value, nextPolicy.max_jobs));
      setConcurrency((value) => Math.min(value, nextPolicy.max_concurrency));
      setSelectedBatchId((current) => (
        current && nextBatches.some((batch) => batch.id === current)
          ? current
          : nextBatches[0]?.id ?? null
      ));
    } catch (reason) {
      setError(messageFrom(reason));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const visibleBatch = useMemo(
    () => batches.find((batch) => batch.id === selectedBatchId) ?? batches[0] ?? null,
    [batches, selectedBatchId],
  );
  const visibleJobs = useMemo(() => {
    if (!visibleBatch) return [];
    const ids = new Set(visibleBatch.session_ids);
    return sessions
      .filter((session) => ids.has(session.id))
      .sort((left, right) => left.created_at.localeCompare(right.created_at));
  }, [sessions, visibleBatch]);

  const queueCounts = useMemo(() => ({
    queued: batches.filter((batch) => batch.queue_status === "queued").length,
    running: batches.filter((batch) => batch.queue_status === "running").length,
    succeeded: batches.filter((batch) => batch.queue_status === "succeeded").length,
    failed: batches.filter((batch) => batch.queue_status === "failed").length,
  }), [batches]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusyAction("create");
    setError(null);
    try {
      const proxies = useProxy ? parseProxyRows(proxyRows) : [];
      if (useProxy && proxies.length === 0) {
        throw new Error("Add at least one static proxy.");
      }
      const payload: CreateAutomationBatchRequest = {
        tenant_id: "automation",
        display_name: displayName,
        start_url: url,
        mode,
        total_jobs: totalJobs,
        concurrency: Math.min(concurrency, totalJobs),
        active_seconds: activeSeconds,
        locale: "en-US",
        timezone: "auto",
        priority: 50,
        proxies: proxies.length ? proxies : undefined,
        auto_start: false,
      };
      const created = await createAutomationBatch(payload);
      setBatches((current) => [created, ...current]);
      setSelectedBatchId(created.id);
      setExpanded(false);
      setProxyRows("");
      await onSessionsChanged();
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusyAction(null);
    }
  };

  const importProxyFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    const contents = await Promise.all(files.map((file) => file.text()));
    setProxyRows((current) => [current, ...contents].filter(Boolean).join("\n"));
    event.target.value = "";
  };

  const runAction = async (
    name: string,
    operation: () => Promise<AutomationBatch>,
  ) => {
    setBusyAction(name);
    setError(null);
    try {
      const updated = await operation();
      setBatches((current) => [
        updated,
        ...current.filter((batch) => batch.id !== updated.id),
      ]);
      setSelectedBatchId(updated.id);
      await onSessionsChanged();
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="panel batch-panel" id="automation-batches" aria-labelledby="batch-title">
      <div className="batch-panel__header">
        <div className="batch-panel__title">
          <span className="batch-panel__icon" aria-hidden="true"><Layers3 /></span>
          <div>
            <span className="eyebrow">Persistent worker queue</span>
            <h2 id="batch-title">Parallel browser sessions</h2>
            <p>Queue independent browser profiles, assign static proxies round-robin and control each run.</p>
          </div>
        </div>
        <div className="batch-panel__header-actions">
          <span className="capacity-chip"><Database aria-hidden="true" />SQLite history</span>
          <span className="capacity-chip"><Gauge aria-hidden="true" />Global cap {policy.max_concurrency}</span>
          <button className="button button--primary" type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} aria-controls="batch-form">
            <Play aria-hidden="true" />New queue
          </button>
        </div>
      </div>

      {error && <div className="form-alert batch-alert" role="alert">{error}</div>}

      <div className="batch-queue-metrics" aria-label="Persistent queue status">
        <span><small>Queued</small><strong>{queueCounts.queued}</strong></span>
        <span><small>Running</small><strong>{queueCounts.running}</strong></span>
        <span><small>Succeeded</small><strong>{queueCounts.succeeded}</strong></span>
        <span><small>Failed</small><strong>{queueCounts.failed}</strong></span>
      </div>

      {expanded && (
        <form className="batch-form" id="batch-form" onSubmit={submit}>
          <label className="field field--full">
            <span>Queue name</span>
            <input required maxLength={128} value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
          </label>
          <label className="field field--full">
            <span>Start URL</span>
            <input required value={url} onChange={(event) => setUrl(event.target.value)} />
          </label>
          <label className="field">
            <span>Total jobs</span>
            <input type="number" min={1} max={policy.max_jobs} value={totalJobs} onChange={(event) => setTotalJobs(Number(event.target.value))} />
          </label>
          <label className="field">
            <span>Concurrency</span>
            <input type="number" min={1} max={Math.min(totalJobs, policy.max_concurrency)} value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} />
          </label>
          <label className="field">
            <span>Active time per browser</span>
            <span className="field-with-suffix"><input type="number" min={1} max={86400} value={activeSeconds} onChange={(event) => setActiveSeconds(Number(event.target.value))} /><small>seconds</small></span>
          </label>
          <fieldset className="batch-mode-picker">
            <legend>Window mode</legend>
            <button type="button" className={mode === "hidden" ? "compact-choice is-selected" : "compact-choice"} aria-pressed={mode === "hidden"} onClick={() => setMode("hidden")}><EyeOff aria-hidden="true" />Hidden</button>
            <button type="button" className={mode === "visible" ? "compact-choice is-selected" : "compact-choice"} aria-pressed={mode === "visible"} onClick={() => setMode("visible")}><Eye aria-hidden="true" />Visible</button>
          </fieldset>
          <label className="check-field field--full">
            <input type="checkbox" checked={useProxy} onChange={(event) => setUseProxy(event.target.checked)} />
            <span><strong>Assign static proxies</strong><small>Jobs use the list in round-robin order; one proxy may serve multiple workers.</small></span>
          </label>
          {useProxy && (
            <div className="batch-static-proxies field--full">
              <label className="field field--full">
                <span>One proxy per line</span>
                <textarea required rows={5} placeholder={"http://host:port\nsocks5://host:port|username|password"} value={proxyRows} onChange={(event) => setProxyRows(event.target.value)} />
                <small>Accepted formats: server or server|username|password.</small>
              </label>
              <button className="button button--secondary" type="button" onClick={() => proxyFileInput.current?.click()}><Upload aria-hidden="true" />Import .txt</button>
              <input ref={proxyFileInput} className="visually-hidden" type="file" accept=".txt,text/plain" multiple onChange={(event) => void importProxyFile(event)} />
            </div>
          )}
          <div className="batch-form__footer field--full">
            <span><ShieldCheck aria-hidden="true" />Proxy passwords remain in process memory and are not written to SQLite.</span>
            <button className="button button--secondary" type="button" onClick={() => setExpanded(false)}>Cancel</button>
            <button className="button button--primary" type="submit" disabled={busyAction !== null}><Play aria-hidden="true" />{busyAction === "create" ? "Queueing…" : "Add to queue"}</button>
          </div>
        </form>
      )}

      {batches.length > 0 && (
        <div className="batch-history" aria-label="Queue history">
          {batches.map((batch) => (
            <button key={batch.id} type="button" className={batch.id === visibleBatch?.id ? "batch-history__item is-selected" : "batch-history__item"} onClick={() => setSelectedBatchId(batch.id)}>
              <span><strong>{batch.display_name}</strong><small>{batch.total_jobs} jobs · {batch.concurrency} workers · {batch.proxy_servers.length || 0} proxies</small></span>
              <em className={"queue-state queue-state--" + batch.queue_status}>{batch.queue_status}</em>
            </button>
          ))}
        </div>
      )}

      {visibleBatch ? (
        <div className="batch-runtime" aria-live="polite">
          <div className="batch-summary">
            <div>
              <span className="eyebrow">Selected queue</span>
              <strong>{visibleBatch.display_name}</strong>
              <span className="batch-summary__meta"><Network aria-hidden="true" />{visibleBatch.proxy_servers.length ? `${visibleBatch.proxy_servers.length} static proxies` : "Direct network"}</span>
            </div>
            <div className="batch-progress-block">
              <div className="batch-progress-label"><span>{visibleBatch.finished_jobs}/{visibleBatch.total_jobs} finished</span><strong>{Math.round((visibleBatch.finished_jobs / visibleBatch.total_jobs) * 100)}%</strong></div>
              <progress max={visibleBatch.total_jobs} value={visibleBatch.finished_jobs}>{visibleBatch.finished_jobs}</progress>
              <span>{visibleBatch.completed_jobs} completed · {visibleBatch.failed_jobs} failed · {visibleBatch.cancelled_jobs} cancelled</span>
            </div>
            <div className="batch-summary__actions">
              {visibleBatch.status === "queued" && (
                <button className="button button--primary" type="button" disabled={busyAction !== null} onClick={() => void runAction("start", () => startAutomationBatch(visibleBatch.id))}><Play aria-hidden="true" />Start all</button>
              )}
              {ACTIVE_BATCH_STATUSES.has(visibleBatch.status) && (
                <button className="button button--danger-ghost" type="button" disabled={busyAction !== null || visibleBatch.status === "cancelling"} onClick={() => void runAction("stop", () => cancelAutomationBatch(visibleBatch.id))}><Square aria-hidden="true" />Stop</button>
              )}
              {RETRYABLE_BATCH_STATUSES.has(visibleBatch.status) && (
                <button className="button button--secondary" type="button" disabled={busyAction !== null} onClick={() => void runAction("retry", () => retryAutomationBatch(visibleBatch.id))}><RotateCw aria-hidden="true" />Retry</button>
              )}
            </div>
          </div>
          <div className="batch-job-grid" aria-label="Batch browser streams">
            {visibleJobs.map((session) => <BatchJobCard key={session.id} session={session} onSelect={() => onSelectSession(session.id)} />)}
            {visibleJobs.length === 0 && <div className="batch-jobs-empty"><RotateCw aria-hidden="true" /><span>Session records are unavailable or still loading.</span></div>}
          </div>
          <div className="batch-runtime__footer"><TimerReset aria-hidden="true" />Each browser stays active for {visibleBatch.active_seconds}s, then closes its temporary profile.</div>
        </div>
      ) : (
        <div className="batch-empty"><Globe2 aria-hidden="true" /><strong>No browser queues yet</strong><p>Create a queue, review its worker/proxy assignment, then press Start all.</p></div>
      )}
    </section>
  );
}
