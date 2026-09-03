export type BrowserMode = "hidden" | "visible";
export type SessionStatus =
  | "queued" | "starting" | "running" | "closing" | "closed" | "failed";
export type SessionPhase =
  | "queued" | "rotating_proxy" | "launching" | "active"
  | "cleanup" | "completed" | "cancelled" | "failed";
export type BatchStatus =
  | "queued" | "running" | "cancelling" | "completed"
  | "completed_with_errors" | "cancelled" | "failed";
export type QueueStatus = "queued" | "running" | "succeeded" | "failed";
export type SignupTestStatus =
  | "queued" | "running" | "waiting_otp" | "completed"
  | "captcha_required" | "email_rejected" | "cancelled" | "failed";
export type SignupTestPhase =
  | "opening" | "sign_up" | "method" | "birthday" | "email"
  | "otp" | "username" | "complete" | "cleanup";

export interface ProxyConfig {
  server: string;
  username?: string;
  password?: string;
}

export interface CreateSessionRequest {
  display_name: string;
  initial_url: string;
  mode: BrowserMode;
  locale: string;
  timezone: string;
  priority: number;
  tenant_id: string;
  proxy?: ProxyConfig;
}

export interface BrowserSession {
  id: string;
  tenant_id: string;
  display_name: string;
  status: SessionStatus;
  mode: BrowserMode;
  current_url: string;
  locale: string;
  timezone: string;
  proxy_server?: string | null;
  created_at: string;
  updated_at: string;
  error?: string | null;
  batch_id?: string | null;
  ephemeral: boolean;
  phase: SessionPhase;
  auto_close_after_seconds?: number | null;
  rotation_attempts: number;
  rotation_succeeded?: boolean | null;
  extensions_enabled: boolean;
  humanize: boolean;
}

export interface CreateAutomationBatchRequest {
  tenant_id: string;
  display_name: string;
  start_url: string;
  mode: BrowserMode;
  total_jobs: number;
  concurrency: number;
  active_seconds: number;
  locale: string;
  timezone: string;
  priority: number;
  proxy?: ProxyConfig;
  proxies?: ProxyConfig[];
  rotation_url?: string;
  auto_start?: boolean;
}

export interface AutomationBatch {
  id: string;
  tenant_id: string;
  display_name: string;
  start_url: string;
  mode: BrowserMode;
  total_jobs: number;
  concurrency: number;
  active_seconds: number;
  proxy_server?: string | null;
  proxy_servers: string[];
  proxy_auth_required: boolean;
  queue_status: QueueStatus;
  rotation_enabled: boolean;
  status: BatchStatus;
  session_ids: string[];
  completed_jobs: number;
  failed_jobs: number;
  cancelled_jobs: number;
  finished_jobs: number;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface AutomationBatchPolicy {
  max_jobs: number;
  max_concurrency: number;
}

export interface CreateSignupTestRequest {
  start_url: string;
  email: string;
  account_password: string;
  refresh_token: string;
  client_id: string;
  username: string;
  birth_date: string;
  proxy?: ProxyConfig;
  fallback_mailboxes: SignupMailboxInput[];
}

export interface SignupMailboxInput {
  email: string;
  refresh_token: string;
  client_id: string;
}

export interface SignupDraft {
  email: string;
  refresh_token: string;
  client_id: string;
  username: string;
  account_password: string;
}

export interface ManagedAccountRecord {
  id: string;
  email_masked: string;
  username: string;
  status: SignupTestStatus;
  updated_at: string;
}

export interface StoredAccount {
  id: string;
  email: string;
  source_name: string;
  has_email_password: boolean;
  has_refresh_token: boolean;
  has_client_id: boolean;
  created_at: string;
  updated_at: string;
}

export interface ImportAccountRow {
  email: string;
  email_password: string;
  refresh_token: string;
  client_id: string;
  source_name: string;
}

export interface ImportAccountsResult {
  imported: number;
  duplicates: number;
  total: number;
}

export interface SignupTest {
  id: string;
  session_id: string;
  start_url: string;
  email_masked: string;
  requested_username: string;
  status: SignupTestStatus;
  phase: SignupTestPhase;
  message: string;
  error_code?: string | null;
  email_attempts: number;
  total_email_candidates: number;
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
  revision: number;
}

export interface SessionEvent {
  id: string;
  session_id?: string;
  type: string;
  message: string;
  occurred_at: string;
  severity: "info" | "success" | "warning" | "error";
}
