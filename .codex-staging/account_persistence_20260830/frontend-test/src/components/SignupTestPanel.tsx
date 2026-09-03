import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  CalendarDays,
  CircleStop,
  KeyRound,
  MailCheck,
  Network,
  Plus,
  Play,
  ScanSearch,
  ShieldX,
  Trash2,
  UserRoundCheck,
} from "lucide-react";
import {
  cancelSignupTest,
  createSignupTest,
  getCurrentSignupTest,
} from "../features/sessions/api";
import type {
  BrowserSession,
  CreateSignupTestRequest,
  ManagedAccountRecord,
  SignupDraft,
  SignupTest,
  SignupTestPhase,
  SignupTestStatus,
} from "../features/sessions/types";
import { useLiveFrame } from "../features/sessions/useLiveFrame";

const DEFAULT_URL = "https://www.tiktok.com/tiktokstudio/upload?lang=en";
const ACTIVE = new Set<SignupTestStatus>(["queued", "running", "waiting_otp"]);
const PHASES: SignupTestPhase[] = [
  "opening", "sign_up", "method", "birthday", "email", "otp", "username", "complete",
];
const PHASE_LABEL: Record<SignupTestPhase, string> = {
  opening: "Open current page",
  sign_up: "Sign up",
  method: "Email method",
  birthday: "Birth date",
  email: "Email & password",
  otp: "Mailbox OTP",
  username: "Username",
  complete: "Complete",
  cleanup: "Cleanup",
};
const STATUS_LABEL: Record<SignupTestStatus, string> = {
  queued: "Queued",
  running: "Running",
  waiting_otp: "Waiting for OTP",
  completed: "Completed",
  captcha_required: "Stopped: CAPTCHA",
  email_rejected: "Email rejected",
  cancelled: "Cancelled",
  failed: "Failed",
};

interface Props {
  sessions: BrowserSession[];
  onSelectSession: (id: string) => void;
  onSessionsChanged: () => Promise<void>;
  prefill?: SignupDraft | null;
  onResult?: (record: ManagedAccountRecord) => void;
}

interface MailboxDraft {
  id: string;
  email: string;
  refreshToken: string;
  clientId: string;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The one-shot test failed.";
}

export function SignupTestPanel({
  sessions,
  onSelectSession,
  onSessionsChanged,
  prefill = null,
  onResult,
}: Props) {
  const [test, setTest] = useState<SignupTest | null>(null);
  const [url, setUrl] = useState(DEFAULT_URL);
  const [email, setEmail] = useState("");
  const [accountPassword, setAccountPassword] = useState("Virgo_09");
  const [refreshToken, setRefreshToken] = useState("");
  const [clientId, setClientId] = useState("");
  const [fallbackMailboxes, setFallbackMailboxes] = useState<MailboxDraft[]>([]);
  const [username, setUsername] = useState("");
  const [birthDate, setBirthDate] = useState("2000-01-01");
  const [useProxy, setUseProxy] = useState(false);
  const [proxyServer, setProxyServer] = useState("");
  const [proxyUsername, setProxyUsername] = useState("");
  const [proxyPassword, setProxyPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reportedTestId = useRef<string | null>(null);

  useEffect(() => {
    if (!prefill) return;
    setEmail(prefill.email);
    setRefreshToken(prefill.refresh_token);
    setClientId(prefill.client_id);
    setUsername(prefill.username);
    setAccountPassword(prefill.account_password);
  }, [prefill]);

  useEffect(() => {
    if (!test?.finished_at || reportedTestId.current === test.id) return;
    reportedTestId.current = test.id;
    onResult?.({
      id: test.id,
      email_masked: test.email_masked,
      username: test.requested_username,
      status: test.status,
      updated_at: test.finished_at,
    });
  }, [onResult, test]);

  const refresh = useCallback(async () => {
    try {
      setTest(await getCurrentSignupTest());
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const session = useMemo(
    () => sessions.find((item) => item.id === test?.session_id) ?? null,
    [sessions, test?.session_id],
  );
  const frame = useLiveFrame(session?.id ?? "", session?.status === "running");
  const active = Boolean(test && ACTIVE.has(test.status));
  const currentPhase = test ? PHASES.indexOf(test.phase) : -1;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const payload: CreateSignupTestRequest = {
      start_url: url,
      email,
      account_password: accountPassword,
      refresh_token: refreshToken,
      client_id: clientId,
      username,
      birth_date: birthDate,
      proxy: useProxy ? {
        server: proxyServer.trim(),
        username: proxyUsername.trim() || undefined,
        password: proxyPassword || undefined,
      } : undefined,
      fallback_mailboxes: fallbackMailboxes.map((mailbox) => ({
        email: mailbox.email,
        refresh_token: mailbox.refreshToken,
        client_id: mailbox.clientId,
      })),
    };
    try {
      const created = await createSignupTest(payload);
      setTest(created);
      setAccountPassword("Virgo_09");
      setRefreshToken("");
      setClientId("");
      setProxyPassword("");
      setFallbackMailboxes((current) => current.map((mailbox) => ({
        ...mailbox,
        refreshToken: "",
        clientId: "",
      })));
      await onSessionsChanged();
      onSelectSession(created.session_id);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const addFallbackMailbox = () => {
    setFallbackMailboxes((current) => current.length >= 9 ? current : [
      ...current,
      { id: crypto.randomUUID(), email: "", refreshToken: "", clientId: "" },
    ]);
  };

  const updateFallbackMailbox = (
    id: string,
    field: "email" | "refreshToken" | "clientId",
    value: string,
  ) => {
    setFallbackMailboxes((current) => current.map((mailbox) => (
      mailbox.id === id ? { ...mailbox, [field]: value } : mailbox
    )));
  };

  const removeFallbackMailbox = (id: string) => {
    setFallbackMailboxes((current) => current.filter((mailbox) => mailbox.id !== id));
  };

  const cancel = async () => {
    if (!test) return;
    setBusy(true);
    setError(null);
    try {
      setTest(await cancelSignupTest(test.id));
      await onSessionsChanged();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel signup-test-panel" id="signup-test" aria-labelledby="signup-test-title">
      <div className="signup-test-heading">
        <span className="signup-test-icon" aria-hidden="true"><ScanSearch /></span>
        <div>
          <span className="eyebrow">One controlled run</span>
          <h2 id="signup-test-title">Signup flow test</h2>
          <p>Opens the current page, selects an adult birthday, retries owned Hotmail addresses, reads OTP and completes signup.</p>
        </div>
        <span className="signup-safety-chip"><ShieldX aria-hidden="true" />OmoCaptcha · humanized · proxy-ready · up to 10 mailboxes</span>
      </div>

      {error && <div className="form-alert signup-test-alert" role="alert">{error}</div>}

      {!test ? (
        <form className="signup-test-form" onSubmit={submit}>
          <label className="field field--full">
            <span>Current start URL</span>
            <input required value={url} onChange={(event) => setUrl(event.target.value)} />
          </label>
          <label className="field">
            <span>Primary Hotmail email</span>
            <input required type="email" autoComplete="off" value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label className="field">
            <span>TikTok account password</span>
            <input required minLength={8} type="password" autoComplete="new-password" value={accountPassword} onChange={(event) => setAccountPassword(event.target.value)} />
          </label>
          <label className="field">
            <span>Requested username</span>
            <input required minLength={6} maxLength={18} pattern="(?=.*_)(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9._]+" autoComplete="off" value={username} onChange={(event) => setUsername(event.target.value)} />
            <small>6–18 characters with letters, numbers and at least one underscore.</small>
          </label>
          <label className="field">
            <span>Birth date</span>
            <input required type="date" value={birthDate} onChange={(event) => setBirthDate(event.target.value)} />
          </label>
          <label className="field field--wide">
            <span>Microsoft OAuth refresh token</span>
            <input required minLength={8} type="password" autoComplete="off" value={refreshToken} onChange={(event) => setRefreshToken(event.target.value)} />
          </label>
          <label className="field">
            <span>Microsoft client ID</span>
            <input required minLength={8} type="password" autoComplete="off" value={clientId} onChange={(event) => setClientId(event.target.value)} />
          </label>
          <label className="blank-proxy-toggle field--full">
            <input type="checkbox" checked={useProxy} onChange={(event) => setUseProxy(event.target.checked)} />
            <span><strong>{useProxy ? "Signup session proxy" : "Direct network"}</strong><small>When enabled, this signup browser is launched through the configured proxy.</small></span>
          </label>
          {useProxy && (
            <div className="blank-proxy-fields field--full">
              <label className="field">
                <span>Proxy server</span>
                <input required placeholder="socks5://host:port" value={proxyServer} onChange={(event) => setProxyServer(event.target.value)} />
              </label>
              <label className="field">
                <span>Proxy username</span>
                <input autoComplete="username" value={proxyUsername} onChange={(event) => setProxyUsername(event.target.value)} />
              </label>
              <label className="field">
                <span>Proxy password</span>
                <input type="password" autoComplete="current-password" value={proxyPassword} onChange={(event) => setProxyPassword(event.target.value)} />
              </label>
            </div>
          )}
          <div className="signup-mailboxes field--full">
            <div className="signup-mailboxes__heading">
              <div>
                <strong>Fallback Hotmail mailboxes</strong>
                <span>If TikTok shows that an email is used, the next mailbox is tried automatically.</span>
              </div>
              <button
                className="button button--secondary"
                type="button"
                disabled={fallbackMailboxes.length >= 9}
                onClick={addFallbackMailbox}
              >
                <Plus aria-hidden="true" />Add mailbox
              </button>
            </div>
            {fallbackMailboxes.map((mailbox, index) => (
              <fieldset className="signup-mailbox" key={mailbox.id}>
                <legend>Fallback {index + 1}</legend>
                <label className="field">
                  <span>Hotmail email</span>
                  <input required type="email" autoComplete="off" value={mailbox.email} onChange={(event) => updateFallbackMailbox(mailbox.id, "email", event.target.value)} />
                </label>
                <label className="field">
                  <span>OAuth refresh token</span>
                  <input required minLength={8} type="password" autoComplete="off" value={mailbox.refreshToken} onChange={(event) => updateFallbackMailbox(mailbox.id, "refreshToken", event.target.value)} />
                </label>
                <label className="field">
                  <span>Microsoft client ID</span>
                  <input required minLength={8} type="password" autoComplete="off" value={mailbox.clientId} onChange={(event) => updateFallbackMailbox(mailbox.id, "clientId", event.target.value)} />
                </label>
                <button className="button button--danger-ghost" type="button" onClick={() => removeFallbackMailbox(mailbox.id)}>
                  <Trash2 aria-hidden="true" />Remove
                </button>
              </fieldset>
            ))}
          </div>
          <div className="signup-test-form__footer field--full">
            <span><KeyRound aria-hidden="true" />Secrets stay in backend memory only and are never returned.</span>
            <button className="button button--primary" disabled={busy} type="submit"><Play aria-hidden="true" />{busy ? "Starting…" : "Run one test"}</button>
          </div>
        </form>
      ) : (
        <div className="signup-test-runtime" aria-live="polite">
          <div className="signup-test-statusbar">
            <div>
              <span className="eyebrow">{STATUS_LABEL[test.status]}</span>
              <strong>{test.email_masked} · @{test.requested_username} · email {test.email_attempts}/{test.total_email_candidates}</strong>
              <p>{test.message}</p>
            </div>
            {active && <button className="button button--danger-ghost" type="button" disabled={busy} onClick={() => void cancel()}><CircleStop aria-hidden="true" />Stop test</button>}
          </div>
          <ol className="signup-stepper" aria-label="Signup flow progress">
            {PHASES.map((phase, index) => (
              <li key={phase} className={index < currentPhase ? "is-complete" : index === currentPhase ? "is-current" : ""}>
                <span>{index + 1}</span><small>{PHASE_LABEL[phase]}</small>
              </li>
            ))}
          </ol>
          <div className="signup-test-monitor">
            <button type="button" className="signup-test-preview" disabled={!session} onClick={() => session && onSelectSession(session.id)}>
              {frame.source ? <img src={frame.source} alt="Live signup test browser" /> : <span><ScanSearch aria-hidden="true" />Waiting for live frame</span>}
            </button>
            <div className="signup-test-facts">
              <span><CalendarDays aria-hidden="true" />Configured adult birth date</span>
              <span><MailCheck aria-hidden="true" />Microsoft Graph reads one fresh OTP</span>
              <span><UserRoundCheck aria-hidden="true" />Used emails automatically advance to the next mailbox</span>
              <span><ShieldX aria-hidden="true" />Supported CAPTCHA waits for OmoCaptcha; unresolved challenges stop safely</span>
              <span><Network aria-hidden="true" />{session?.proxy_server ?? "Direct network"}</span>
            </div>
          </div>
          {test.finished_at && <div className="signup-test-finished">This backend has consumed its one signup test. Restart it before another controlled run.</div>}
        </div>
      )}
    </section>
  );
}
