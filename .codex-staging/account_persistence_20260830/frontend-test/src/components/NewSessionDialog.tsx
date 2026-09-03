import { useEffect, useRef, useState, type FormEvent } from "react";
import { Eye, EyeOff, Plus, X } from "lucide-react";
import type { CreateSessionRequest } from "../features/sessions/types";

interface Props {
  open: boolean;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (payload: CreateSessionRequest) => Promise<void>;
}

const defaults: CreateSessionRequest = {
  display_name: "Research session",
  initial_url: "about:blank",
  mode: "hidden",
  locale: "auto",
  timezone: "auto",
  priority: 50,
  tenant_id: "local",
};

export function NewSessionDialog({ open, busy, error, onClose, onSubmit }: Props) {
  const [form, setForm] = useState<CreateSessionRequest>(defaults);
  const [proxyEnabled, setProxyEnabled] = useState(false);
  const [proxyServer, setProxyServer] = useState("");
  const [proxyUsername, setProxyUsername] = useState("");
  const [proxyPassword, setProxyPassword] = useState("");
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const close = () => { if (!busy) onClose(); };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await onSubmit({
      ...form,
      proxy: proxyEnabled ? {
        server: proxyServer.trim(),
        username: proxyUsername.trim() || undefined,
        password: proxyPassword || undefined,
      } : undefined,
    });
  };

  return (
    <dialog
      ref={dialogRef}
      className="session-dialog"
      aria-labelledby="new-session-title"
      onCancel={(event) => { event.preventDefault(); close(); }}
      onClose={() => { if (open && !busy) onClose(); }}
    >
      <form onSubmit={submit}>
        <header className="dialog-header">
          <div>
            <span className="eyebrow">Isolated workspace</span>
            <h2 id="new-session-title">Create browser session</h2>
            <p>Each session receives an independent profile, proxy and lifecycle.</p>
          </div>
          <button className="icon-button" type="button" onClick={close} aria-label="Close dialog">
            <X aria-hidden="true" />
          </button>
        </header>
        {error && <div className="form-alert" role="alert">{error}</div>}
        <div className="form-grid">
          <label className="field field--full">
            <span>Session name</span>
            <input required value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} />
          </label>
          <label className="field field--full">
            <span>Initial URL</span>
            <input required inputMode="url" value={form.initial_url} onChange={(event) => setForm({ ...form, initial_url: event.target.value })} aria-describedby="initial-url-help" />
            <small id="initial-url-help">Use about:blank or a full http(s) URL.</small>
          </label>
          <fieldset className="mode-picker field--full">
            <legend>Window mode</legend>
            <button type="button" className={form.mode === "hidden" ? "mode-option is-selected" : "mode-option"} aria-pressed={form.mode === "hidden"} onClick={() => setForm({ ...form, mode: "hidden" })}>
              <EyeOff aria-hidden="true" />
              <span><strong>Hidden</strong><small>Runs in the background; live stream stays available.</small></span>
            </button>
            <button type="button" className={form.mode === "visible" ? "mode-option is-selected" : "mode-option"} aria-pressed={form.mode === "visible"} onClick={() => setForm({ ...form, mode: "visible" })}>
              <Eye aria-hidden="true" />
              <span><strong>Visible</strong><small>Useful for manual inspection and debugging.</small></span>
            </button>
          </fieldset>
          <label className="field">
            <span>Locale</span>
            <select value={form.locale} onChange={(event) => setForm({ ...form, locale: event.target.value })}>
              <option value="auto">Auto from egress</option>
              <option value="en-US">English (US)</option>
              <option value="id-ID">Bahasa Indonesia</option>
              <option value="vi-VN">Tiếng Việt</option>
            </select>
          </label>
          <label className="field">
            <span>Timezone</span>
            <input value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} placeholder="auto" />
          </label>
          <label className="check-field field--full">
            <input type="checkbox" checked={proxyEnabled} onChange={(event) => setProxyEnabled(event.target.checked)} />
            <span><strong>Route through a session proxy</strong><small>Credentials remain server-side and never return in API responses.</small></span>
          </label>
          {proxyEnabled && (
            <div className="proxy-fields field--full">
              <label className="field field--full">
                <span>Proxy server</span>
                <input required value={proxyServer} onChange={(event) => setProxyServer(event.target.value)} placeholder="socks5://127.0.0.1:1080" />
              </label>
              <label className="field">
                <span>Username</span>
                <input autoComplete="username" value={proxyUsername} onChange={(event) => setProxyUsername(event.target.value)} />
              </label>
              <label className="field">
                <span>Password</span>
                <input autoComplete="current-password" type="password" value={proxyPassword} onChange={(event) => setProxyPassword(event.target.value)} />
              </label>
            </div>
          )}
        </div>
        <footer className="dialog-actions">
          <button className="button button--secondary" type="button" onClick={close}>Cancel</button>
          <button className="button button--primary" type="submit" disabled={busy}>
            <Plus aria-hidden="true" />{busy ? "Creating…" : "Create session"}
          </button>
        </footer>
      </form>
    </dialog>
  );
}
