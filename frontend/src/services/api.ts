const BASE_URL = 'http://127.0.0.1:8001/api/v1';

export const apiClient = {
  // 1. Lấy dữ liệu
  getAccounts: async () => {
    const res = await fetch(`${BASE_URL}/accounts/`);
    return res.json();
  },

  getProxies: async () => {
    const res = await fetch(`${BASE_URL}/proxies/`);
    return res.json();
  },

  // 2. Import File hàng loạt
  uploadFiles: async (files: FileList, type: 'accounts' | 'proxies') => {
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append('files', files[i]);
    }
    const endpoint = type === 'accounts' ? 'accounts/import-file' : 'proxies/import-file';
    const res = await fetch(`${BASE_URL}/${endpoint}`, {
      method: 'POST',
      body: formData,
    });
    return res.json();
  },

  // 3. Gán Proxy thủ công
  bindProxy: async (accountId: string, proxyId: string | null) => {
    const res = await fetch(`${BASE_URL}/accounts/${accountId}/proxy`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxy_id: proxyId }),
    });
    return res.json();
  },

  // 4. Kích hoạt đăng nhập hàng loạt
  bulkLogin: async (accountIds: string[], method: 'COOKIE' | 'CREDENTIAL', concurrency: number) => {
    const res = await fetch(`${BASE_URL}/tasks/bulk-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        account_ids: accountIds,
        login_method: method,
        concurrency_limit: concurrency,
      }),
    });
    return res.json();
  },

  // 5. Kích hoạt đổi Profile hàng loạt
  bulkUpdateProfile: async (accountIds: string[], avatarFolder: string, concurrency: number) => {
    const res = await fetch(`${BASE_URL}/tasks/bulk-update-profile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        account_ids: accountIds,
        avatar_folder: avatarFolder || null,
        concurrency_limit: concurrency,
      }),
    });
    return res.json();
  },

  // 6. Tự động gán Proxy tối ưu tải trọng
  autoAllocateProxies: async (accountIds: string[]) => {
    const res = await fetch(`${BASE_URL}/accounts/auto-allocate-proxies`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_ids: accountIds }),
    });
    return res.json();
  }
};