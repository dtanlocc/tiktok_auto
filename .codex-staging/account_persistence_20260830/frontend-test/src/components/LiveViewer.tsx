import { useEffect, useState, type FormEvent } from "react";
import { EyeOff, Globe2, Radio, RefreshCw, Send, Upload, XCircle } from "lucide-react";
import type { BrowserSession } from "../features/sessions/types";
import { useLiveFrame } from "../features/sessions/useLiveFrame";
import { StatusBadge } from "./StatusBadge";

interface Props {
  session: BrowserSession | null;
  busyAction: string | null;
  onNavigate: (url: string) => Promise<void>;
  onUpload: (file: File) => Promise<void>;
  onClose: () => Promise<void>;
  onRefresh: () => Promise<void>;
}

export function LiveViewer({ session, busyAction, onNavigate, onUpload, onClose, onRefresh }: Props) {
  const [url, setUrl] = useState("");
  const [streamEnabled, setStreamEnabled] = useState(true);
  const frame = useLiveFrame(session?.id ?? null, streamEnabled && Boolean(session));
  useEffect(() => setUrl(session?.current_url ?? ""), [session?.current_url]);

  if (!session) {
    return (
      <section className="panel viewer-panel viewer-panel--empty" aria-labelledby="viewer-title">
        <div className="viewer-empty">
          <EyeOff aria-hidden="true" /><span className="eyebrow">Live workspace</span>
          <h2 id="viewer-title">Select a browser session</h2>
          <p>The viewer only subscribes when this panel is visible. Browser work continues independently.</p>
        </div>
      </section>
    );
  }

  const submitNavigation = async (event: FormEvent) => {
    event.preventDefault();
    await onNavigate(url);
  };
  const streamLabel = frame.connection === "live" ? "Live" : frame.connection;

  return (
    <section className="panel viewer-panel" aria-labelledby="viewer-title">
      <div className="viewer-toolbar">
        <div className="viewer-title">
          <span className="eyebrow">Live workspace</span>
          <div className="viewer-title__line"><h2 id="viewer-title">{session.display_name}</h2><StatusBadge status={session.status} /></div>
        </div>
        <div className="toolbar-actions">
          <button className="button button--ghost" type="button" onClick={() => setStreamEnabled((value) => !value)} aria-pressed={streamEnabled}>
            <Radio aria-hidden="true" />{streamEnabled ? "Pause viewer" : "Resume viewer"}
          </button>
          <button className="icon-button" type="button" onClick={onRefresh} disabled={busyAction !== null} aria-label="Refresh session">
            <RefreshCw aria-hidden="true" />
          </button>
          <button className="button button--danger-ghost" type="button" onClick={onClose} disabled={busyAction !== null}>
            <XCircle aria-hidden="true" />Close
          </button>
        </div>
      </div>
      <form className="address-bar" onSubmit={submitNavigation}>
        <Globe2 aria-hidden="true" />
        <label className="sr-only" htmlFor="navigation-url">Navigate to URL</label>
        <input id="navigation-url" value={url} onChange={(event) => setUrl(event.target.value)} />
        <button className="icon-button icon-button--compact" type="submit" disabled={busyAction !== null} aria-label="Navigate">
          <Send aria-hidden="true" />
        </button>
      </form>
      <div className="browser-viewport">
        {frame.source ? (
          <img src={frame.source} alt={"Live browser view for " + session.display_name} />
        ) : (
          <div className="stream-placeholder">
            <span className="stream-placeholder__orb" aria-hidden="true" />
            <strong>{streamEnabled ? "Waiting for the first frame" : "Viewer paused"}</strong>
            <p>The session stays active. No frame queue is accumulated.</p>
          </div>
        )}
        <div className={"stream-state stream-state--" + frame.connection} role="status" aria-live="polite">
          <span aria-hidden="true" />{streamLabel}
        </div>
      </div>
      <div className="viewer-footer">
        <div><span className="viewer-footer__label">Mode</span><strong>{session.mode === "hidden" ? "Hidden background" : "Visible window"}</strong></div>
        <div><span className="viewer-footer__label">Last frame</span><strong>{frame.receivedAt ? new Date(frame.receivedAt).toLocaleTimeString() : "Not received"}</strong></div>
        <label className="button button--secondary upload-button">
          <Upload aria-hidden="true" />{busyAction === "upload" ? "Uploading…" : "Upload file"}
          <input type="file" disabled={busyAction !== null} onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void onUpload(file);
            event.currentTarget.value = "";
          }} />
        </label>
      </div>
    </section>
  );
}
