import { create } from 'zustand';
import { Account, ProxyModel, LogMessage } from '../types';

interface AppState {
  accounts: Account[];
  proxies: ProxyModel[];
  logs: LogMessage[];
  wsConnected: boolean;
  
  setAccounts: (accounts: Account[]) => void;
  setProxies: (proxies: ProxyModel[]) => void;
  setWsConnected: (connected: boolean) => void;
  
  addAccount: (account: Account) => void;
  updateAccountStatus: (id: string, status: string, current_step?: string) => void;
  updateAccountStep: (id: string, current_step: string) => void;
  updateAccountProxy: (id: string, proxyId: string | null) => void;
  
  addLog: (log: LogMessage) => void;
  clearLogs: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  accounts: [],
  proxies: [],
  logs: [],
  wsConnected: false,

  setAccounts: (accounts) => set({ accounts }),
  setProxies: (proxies) => set({ proxies }),
  setWsConnected: (connected) => set({ wsConnected: connected }),

  addAccount: (account) => set((state) => {
    if (state.accounts.some((a) => a.id === account.id)) {
      return state;
    }
    return { accounts: [...state.accounts, account] };
  }),

  updateAccountStatus: (id, status, current_step) => set((state) => ({
    accounts: state.accounts.map((acc) => 
      acc.id === id 
        ? { ...acc, status, ...(current_step ? { current_step } : {}) } 
        : acc
    )
  })),

  updateAccountStep: (id, current_step) => set((state) => ({
    accounts: state.accounts.map((acc) => 
      acc.id === id ? { ...acc, current_step } : acc
    )
  })),

  updateAccountProxy: (id, proxyId) => set((state) => ({
    accounts: state.accounts.map((acc) => 
      acc.id === id ? { ...acc, proxy_id: proxyId } : acc
    )
  })),

  addLog: (log) => set((state) => ({
    logs: [...state.logs, log]
  })),

  clearLogs: () => set({ logs: [] })
}));