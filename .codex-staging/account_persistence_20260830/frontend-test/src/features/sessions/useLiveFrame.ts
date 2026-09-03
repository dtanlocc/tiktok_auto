import { useEffect, useRef, useState } from "react";
import { frameSocketUrl } from "./api";

export type StreamConnection = "idle" | "connecting" | "live" | "stale" | "offline";
interface FrameState {
  source: string | null;
  connection: StreamConnection;
  receivedAt: number | null;
}

function asBlob(data: unknown): Blob | null {
  if (data instanceof Blob) return data;
  if (data instanceof ArrayBuffer) return new Blob([data], { type: "image/jpeg" });
  if (typeof data !== "string") return null;
  try {
    const parsed = JSON.parse(data) as { jpeg_b64?: string };
    if (!parsed.jpeg_b64) return null;
    const binary = atob(parsed.jpeg_b64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return new Blob([bytes], { type: "image/jpeg" });
  } catch {
    return null;
  }
}

export function useLiveFrame(sessionId: string | null, enabled: boolean): FrameState {
  const [state, setState] = useState<FrameState>({ source: null, connection: "idle", receivedAt: null });
  const activeUrl = useRef<string | null>(null);

  useEffect(() => {
    if (!sessionId || !enabled) {
      setState({ source: null, connection: "idle", receivedAt: null });
      return;
    }
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let staleTimer: number | undefined;
    let animationFrame: number | undefined;
    let pendingBlob: Blob | null = null;
    let decoding = false;
    let decodingUrl: string | null = null;
    let disposed = false;
    const release = (url: string | null) => { if (url) URL.revokeObjectURL(url); };
    const scheduleStaleCheck = () => {
      window.clearTimeout(staleTimer);
      staleTimer = window.setTimeout(() => {
        setState((current) => current.connection === "live" ? { ...current, connection: "stale" } : current);
      }, 1_500);
    };
    const scheduleDecode = () => {
      if (disposed || decoding || animationFrame !== undefined || !pendingBlob) return;
      animationFrame = window.requestAnimationFrame(() => {
        animationFrame = undefined;
        if (disposed || decoding || !pendingBlob) return;
        const blob = pendingBlob;
        pendingBlob = null;
        decoding = true;
        const nextUrl = URL.createObjectURL(blob);
        decodingUrl = nextUrl;
        const decoder = new Image();
        const finish = () => {
          decoding = false;
          decodingUrl = null;
          scheduleDecode();
        };
        decoder.onload = () => {
          if (disposed) {
            release(nextUrl);
            finish();
            return;
          }
          const previous = activeUrl.current;
          activeUrl.current = nextUrl;
          setState({ source: nextUrl, connection: "live", receivedAt: Date.now() });
          release(previous);
          scheduleStaleCheck();
          finish();
        };
        decoder.onerror = () => {
          release(nextUrl);
          finish();
        };
        decoder.src = nextUrl;
      });
    };
    const connect = () => {
      if (disposed || document.hidden) return;
      setState((current) => ({ ...current, connection: "connecting" }));
      socket = new WebSocket(frameSocketUrl(sessionId));
      socket.binaryType = "arraybuffer";
      socket.onopen = () => { if (!disposed) setState((current) => ({ ...current, connection: "live" })); };
      socket.onmessage = (event) => {
        const blob = asBlob(event.data);
        if (!blob) return;
        // Keep only the newest not-yet-decoded frame. This prevents latency
        // from growing when capture briefly outpaces image decoding.
        pendingBlob = blob;
        scheduleDecode();
      };
      socket.onclose = () => {
        if (disposed) return;
        setState((current) => ({ ...current, connection: "offline" }));
        reconnectTimer = window.setTimeout(connect, 2_000);
      };
      socket.onerror = () => socket?.close();
    };
    const handleVisibility = () => {
      if (document.hidden) {
        window.clearTimeout(reconnectTimer);
        socket?.close();
      } else if (!socket || socket.readyState === WebSocket.CLOSED) connect();
    };
    document.addEventListener("visibilitychange", handleVisibility);
    connect();
    return () => {
      disposed = true;
      document.removeEventListener("visibilitychange", handleVisibility);
      window.clearTimeout(reconnectTimer);
      window.clearTimeout(staleTimer);
      if (animationFrame !== undefined) window.cancelAnimationFrame(animationFrame);
      socket?.close();
      pendingBlob = null;
      release(decodingUrl);
      decodingUrl = null;
      release(activeUrl.current);
      activeUrl.current = null;
    };
  }, [enabled, sessionId]);
  return state;
}
