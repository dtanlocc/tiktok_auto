import { useEffect, useState, type FormEvent } from "react";
import {
  Eye,
  FlaskConical,
  Globe2,
  Play,
  ShieldCheck,
  Square,
} from "lucide-react";
import type {
  BrowserSession,
  CreateSessionRequest,
} from "../features/sessions/types";
import { StatusBadge } from "./StatusBadge";

interface Props {
  session: BrowserSession | null;
  busy: boolean;
  error: string | null;
  onStart: (payload: CreateSessionRequest) => Promise<void>;
  onStop: () => Promise<void>;
}

const DEFAULT_URL = "https://www.tiktok.com/tiktokstudio/upload?lang=en";

export function BlankBrowserPanel({
  session,
  busy,
  error,
  onStart,
  onStop,
}: Props) {
  const [url, setUrl] = useState(DEFAULT_URL);
  const [useProxy, setUseProxy] = useState(false);
  const [proxyServer, setProxyServer] = useState("");
  const [proxyUsername, setProxyUsername] = useState("");
  const [proxyPassword, setProxyPassword] = useState("");
  const active = Boolean(session && !["closed", "failed"].includes(session.status));

  useEffect(() => {
    if (session?.current_url) setUrl(session.current_url);
  }, [session?.current_url]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (active) {
      await onStop();
      return;
    }
    await onStart({
      tenant_id: "blank-browser",
      display_name: "TD trắng",
      initial_url: url,
      mode: "visible",
      locale: "en-US",
      timezone: "auto",
      priority: 100,
      proxy: useProxy ? {
        server: proxyServer.trim(),
        username: proxyUsername.trim() || undefined,
        password: proxyPassword || undefined,
      } : undefined,
    });
  };

  return (
    <section className="panel blank-browser-panel" aria-labelledby="blank-browser-title">
      <div className="blank-browser-heading">
        <span className="blank-browser-icon" aria-hidden="true">
          <FlaskConical />
        </span>
        <div>
          <span className="eyebrow">Manual test workspace</span>
          <div className="blank-browser-title-line">
            <h2 id="blank-browser-title">TD trắng</h2>
            {session ? <StatusBadge status={session.status} /> : (
              <span className="status-badge">
                <span className="status-badge__dot" aria-hidden="true" />
                Stopped
              </span>
            )}
          </div>
          <p>No account or cookies. OmoCaptcha is loaded from the server configuration.</p>
        </div>
      </div>

      <form className="blank-browser-form" onSubmit={submit}>
        {error && <div className="form-alert blank-browser-error" role="alert">{error}</div>}
        <label className="field blank-url-field">
          <span>Open URL</span>
          <span className="blank-input-with-icon">
            <Globe2 aria-hidden="true" />
            <input
              required
              disabled={active || busy}
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
          </span>
        </label>

        <fieldset className="blank-mode-field">
          <legend>Window</legend>
          <span className="compact-choice is-selected" aria-label="Visible window">
            <Eye aria-hidden="true" />Visible
          </span>
        </fieldset>

        <label className="blank-proxy-toggle">
          <input
            type="checkbox"
            disabled={active || busy}
            checked={useProxy}
            onChange={(event) => setUseProxy(event.target.checked)}
          />
          <span>
            <strong>{useProxy ? "Session proxy" : "Direct network"}</strong>
            <small>Language stays en-US; geography follows the real egress.</small>
          </span>
        </label>

        {useProxy && !active && (
          <div className="blank-proxy-fields">
            <label className="field">
              <span>Proxy server</span>
              <input
                required
                value={proxyServer}
                onChange={(event) => setProxyServer(event.target.value)}
                placeholder="socks5://host:port"
              />
            </label>
            <label className="field">
              <span>Username</span>
              <input
                autoComplete="username"
                value={proxyUsername}
                onChange={(event) => setProxyUsername(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={proxyPassword}
                onChange={(event) => setProxyPassword(event.target.value)}
              />
            </label>
          </div>
        )}

        <div className="blank-browser-actions">
          <span className="extension-chip">
            <ShieldCheck aria-hidden="true" />
            OmoCaptcha 1.7.7
          </span>
          <button
            className={active ? "button button--danger-ghost" : "button button--primary"}
            type="submit"
            disabled={busy}
          >
            {active ? <Square aria-hidden="true" /> : <Play aria-hidden="true" />}
            {busy ? "Working…" : active ? "Stop TD trắng" : "Start TD trắng"}
          </button>
        </div>
      </form>
    </section>
  );
}
