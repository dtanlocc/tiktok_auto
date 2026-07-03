import { create } from 'zustand';

export interface Account {
  id: string;
  username: string;
  status: string;
  proxy_id: string | null;
  has_cookies: boolean;
  current_step: string;
}

export interface Proxy {
  id: string;
  host: string;
  port: number;
  username: string | null;
  protocol: string;
}

interface AppState {
  accounts: Account[];
  proxies: Proxy[]; // Danh sách proxy quản lý
  wsConnected: boolean;
  setAccounts: (accounts: Account[]) => void;
  updateAccountStatus: (id: string, status: string) => void;
  updateAccountProxy: (id: string, proxyId: string | null) => void; // Hàm update proxy realtime
  addAccount: (account: Account) => void;
  setProxies: (proxies: Proxy[]) => void;
  addProxy: (proxy: Proxy) => void;
  setWsConnected: (connected: boolean) => void;
}

export const useAppStore = create<AppState>((set) => ({
  accounts: [],
  proxies: [],
  wsConnected: false,
  setAccounts: (accounts) => set({ accounts }),
  updateAccountStatus: (id, status) => set((state) => ({
    accounts: state.accounts.map((acc) => 
      acc.id === id ? { ...acc, status } : acc
    )
  })),
  updateAccountProxy: (id, proxyId) => set((state) => ({
    accounts: state.accounts.map((acc) => 
      acc.id === id ? { ...acc, proxy_id: proxyId } : acc
    )
  })),
  addAccount: (account) => set((state) => ({
    accounts: [...state.accounts, account]
  })),
  setProxies: (proxies) => set({ proxies }),
  addProxy: (proxy) => set((state) => ({
    proxies: [...state.proxies, proxy]
  })),
  setWsConnected: (connected) => set({ wsConnected: connected })
}));