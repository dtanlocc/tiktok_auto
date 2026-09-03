import React from 'react';
import { Users, Globe, Heart, MonitorPlay, Film } from 'lucide-react';
import { AppTab } from '../types';

interface HeaderProps {
  activeTab: AppTab;
  setActiveTab: (tab: AppTab) => void;
}

const TABS = [
  { id: 'accounts', label: 'Tài khoản', icon: Users },
  { id: 'videos', label: 'Video', icon: Film },
  { id: 'interactions', label: 'Tương tác', icon: Heart },
  { id: 'screens', label: 'Màn hình', icon: MonitorPlay },
  { id: 'proxies', label: 'Proxies', icon: Globe },
] as const;

export const Header: React.FC<HeaderProps> = ({ activeTab, setActiveTab }) => {
  return (
    <header className="flex flex-wrap items-center justify-between gap-4">
      {/* Brand */}
      <div className="flex items-center gap-3">
        <div className="grid place-items-center w-10 h-10 rounded-xl bg-brand/15 border border-brand/25 text-brand shrink-0">
          <img src="/app-mark.svg" alt="" className="w-8 h-8" aria-hidden="true" />
        </div>
        <div>
          <h1 className="text-lg font-bold tracking-tight text-fg leading-tight">
            TikTok Automation
          </h1>
          <p className="text-fg-subtle text-xs leading-tight">
            Quản trị đa luồng · phân bổ IP thông minh
          </p>
        </div>
      </div>

      {/* Segmented nav */}
      <nav className="flex items-center gap-1 p-1 rounded-xl card-2" role="tablist" aria-label="Điều hướng">
        {TABS.map(({ id, label, icon: Icon }) => {
          const active = activeTab === id;
          return (
            <button
              key={id}
              role="tab"
              aria-selected={active}
              onClick={() => setActiveTab(id)}
              className={`seg ${active ? 'seg-active' : ''}`}
              title={label}
            >
              <Icon className="w-4 h-4" />
              <span className="hidden sm:inline">{label}</span>
            </button>
          );
        })}
      </nav>
    </header>
  );
};
