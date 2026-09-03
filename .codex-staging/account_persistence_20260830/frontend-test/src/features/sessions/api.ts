import type {
  AutomationBatch,
  AutomationBatchPolicy,
  BrowserSession,
  CreateAutomationBatchRequest,
  CreateSessionRequest,
  CreateSignupTestRequest,
  ImportAccountRow,
  ImportAccountsResult,
  SessionEvent,
  SignupTest,
  StoredAccount,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API_BASE + path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {
      detail?: string;
      message?: string;
    } | null;
    throw new Error(body?.detail ?? body?.message ?? "Request failed (" + response.status + ")");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function listSessions(): Promise<BrowserSession[]> {
  const result = await request<BrowserSession[] | { items: BrowserSession[] }>(
    "/api/v1/sessions?limit=200",
  );
  return Array.isArray(result) ? result : result.items;
}

export function createSession(payload: CreateSessionRequest): Promise<BrowserSession> {
  return request("/api/v1/sessions", { method: "POST", body: JSON.stringify(payload) });
}

export async function listAutomationBatches(): Promise<AutomationBatch[]> {
  const result = await request<{ items: AutomationBatch[] }>("/api/v1/automation-batches");
  return result.items;
}

export function getAutomationBatchPolicy(): Promise<AutomationBatchPolicy> {
  return request("/api/v1/automation-batches/policy");
}

export function createAutomationBatch(
  payload: CreateAutomationBatchRequest,
): Promise<AutomationBatch> {
  return request("/api/v1/automation-batches", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function cancelAutomationBatch(batchId: string): Promise<AutomationBatch> {
  return request("/api/v1/automation-batches/" + encodeURIComponent(batchId), {
    method: "DELETE",
  });
}

export function startAutomationBatch(batchId: string): Promise<AutomationBatch> {
  return request("/api/v1/automation-batches/" + encodeURIComponent(batchId) + "/start", {
    method: "POST",
  });
}

export function retryAutomationBatch(batchId: string): Promise<AutomationBatch> {
  return request("/api/v1/automation-batches/" + encodeURIComponent(batchId) + "/retry", {
    method: "POST",
  });
}

export function createSignupTest(payload: CreateSignupTestRequest): Promise<SignupTest> {
  return request("/api/v1/signup-tests", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listAccounts(): Promise<StoredAccount[]> {
  const result = await request<{ items: StoredAccount[] }>("/api/v1/accounts?limit=1000");
  return result.items;
}

export function importAccounts(rows: ImportAccountRow[]): Promise<ImportAccountsResult> {
  return request("/api/v1/accounts/import", {
    method: "POST",
    body: JSON.stringify({ rows }),
  });
}

export async function getCurrentSignupTest(): Promise<SignupTest | null> {
  const response = await fetch(API_BASE + "/api/v1/signup-tests/current");
  if (response.status === 404) return null;
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {
      message?: string;
    } | null;
    throw new Error(body?.message ?? "Could not load the signup test.");
  }
  return response.json() as Promise<SignupTest>;
}

export function cancelSignupTest(testId: string): Promise<SignupTest> {
  return request("/api/v1/signup-tests/" + encodeURIComponent(testId), {
    method: "DELETE",
  });
}

export function closeSession(sessionId: string): Promise<void> {
  return request("/api/v1/sessions/" + encodeURIComponent(sessionId), { method: "DELETE" });
}

export function navigateSession(sessionId: string, url: string): Promise<BrowserSession> {
  return request("/api/v1/sessions/" + encodeURIComponent(sessionId) + "/navigate", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function uploadFile(sessionId: string, file: File): Promise<{ filename: string; bytes_received: number }> {
  const body = new FormData();
  body.append("file", file);
  return request("/api/v1/sessions/" + encodeURIComponent(sessionId) + "/upload", {
    method: "POST",
    body,
  });
}

function websocketBase(): string {
  if (API_BASE) {
    const url = new URL(API_BASE, window.location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return url.origin;
  }
  return (window.location.protocol === "https:" ? "wss:" : "ws:") + "//" + window.location.host;
}

export function eventSocketUrl(): string {
  return websocketBase() + "/ws/events";
}

export function frameSocketUrl(sessionId: string): string {
  return websocketBase() + "/ws/sessions/" + encodeURIComponent(sessionId) + "/stream";
}

export function parseEvent(data: string): SessionEvent | null {
  try {
    const value = JSON.parse(data) as Partial<SessionEvent> & {
      payload?: { message?: string; severity?: SessionEvent["severity"] };
    };
    return {
      id: value.id ?? crypto.randomUUID(),
      session_id: value.session_id,
      type: value.type ?? "system",
      message: value.message ?? value.payload?.message ?? "Session state changed",
      occurred_at: value.occurred_at ?? new Date().toISOString(),
      severity: value.severity ?? value.payload?.severity ?? "info",
    };
  } catch {
    return null;
  }
}
