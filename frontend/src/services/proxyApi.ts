import { Proxy, ProxyInput } from '../types';

const BASE_URL = 'http://127.0.0.1:9000/api/v1';

/** Error carrying the backend's own Vietnamese `detail` message. */
export class ProxyApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, init);
  } catch {
    throw new ProxyApiError('Không kết nối được backend.');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : `Lỗi ${res.status}`;
    throw new ProxyApiError(detail);
  }
  return data as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export interface ProxyImportResult {
  imported: number;
  duplicates: number;
  invalid: number;
  message: string;
}

export interface ProxyAllocateResult {
  allocated: number;
  changed: number;
  skipped_sold: number;
  distribution: Record<string, number>;
  message: string;
}

export const proxyApi = {
  list: () => request<Proxy[]>('/proxies/'),
  create: (input: ProxyInput) => request<Proxy>('/proxies/', json('POST', input)),
  update: (id: string, changes: Partial<ProxyInput>) =>
    request<Proxy>(`/proxies/${encodeURIComponent(id)}`, json('PATCH', changes)),
  /** detachAccounts: gỡ proxy khỏi account đang gán (chúng thành Mạng thật) rồi xóa. */
  remove: (id: string, detachAccounts = false) =>
    request<{ message: string; detached?: number }>(
      `/proxies/${encodeURIComponent(id)}${detachAccounts ? '?detach_accounts=true' : ''}`,
      { method: 'DELETE' },
    ),
  importText: (text: string) => request<ProxyImportResult>('/proxies/import-text', json('POST', { text })),
  /** proxyIds undefined = check every proxy. Resolves when all checks finish. */
  check: (proxyIds?: string[]) =>
    request<Proxy[]>('/proxies/check', json('POST', { proxy_ids: proxyIds ?? null })),
  allocate: (accountIds: string[], proxyIds: string[]) =>
    request<ProxyAllocateResult>(
      '/accounts/auto-allocate-proxies',
      json('POST', { account_ids: accountIds, proxy_ids: proxyIds }),
    ),
};

export const proxyName = (p: Pick<Proxy, 'label' | 'host' | 'port'>) =>
  p.label ? p.label : `${p.host}:${p.port}`;
