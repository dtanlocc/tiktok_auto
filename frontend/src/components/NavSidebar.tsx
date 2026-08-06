import React from 'react';
import { Users, Heart, MonitorPlay, Globe, Bot } from 'lucide-react';

type Tab = 'accounts' | 'proxies' | 'interactions' | 'screens';

interface NavSidebarProps {
  activeTab: Tab;
  setActiveTab: (t: Tab) => void;
  isGloballyPaused: boolean;
}

const NAV: { id: Tab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'accounts', label: 'Tài khoản', icon: Users },
  { id: 'interactions', label: 'Tương tác video', icon: Heart },
  { id: 'screens', label: 'Màn hình trực tiếp', icon: MonitorPlay },
  { id: 'proxies', label: 'Proxies', icon: Globe },
];

export const NavSidebar: React.FC<NavSidebarProps> = ({ activeTab, setActiveTab, isGloballyPaused }) => {
  return (
    <aside className="w-[224px] shrink-0 h-screen sticky top-0 bg-surface/50 border-r border-line-soft flex flex-col">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-4 h-[60px] border-b border-line-soft">
        <div className="grid place-items-center w-9 h-9 rounded-xl bg-brand/15 border border-brand/25 text-brand shrink-0">
          <Bot className="w-5 h-5" />
        </div>
        <div className="min-w-0">
          <h1 className="text-sm font-bold tracking-tight text-fg leading-tight truncate">TikTok Automation</h1>
          <p className="text-[10px] text-fg-subtle leading-tight">Multi-thread control</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 p-3 flex flex-col gap-1" role="tablist" aria-label="Điều hướng">
        {NAV.map(({ id, label, icon: Icon }) => {
          const active = activeTab === id;
          return (
            <button
              key={id}
              role="tab"
              aria-selected={active}
              onClick={() => setActiveTab(id)}
              className={`group relative flex items-center gap-3 px-3 py-2.5 rounded-lg text-[13px] font-semibold
                transition-colors duration-150 cursor-pointer ${
                  active ? 'bg-brand/12 text-brand' : 'text-fg-muted hover:text-fg hover:bg-white/5'
                }`}
            >
              <span className={`absolute left-0 top-1/2 -translate-y-1/2 w-1 h-5 rounded-r-full bg-brand transition-opacity ${active ? 'opacity-100' : 'opacity-0'}`} />
              <Icon className="w-[18px] h-[18px] shrink-0" />
              <span className="truncate">{label}</span>
            </button>
          );
        })}
      </nav>

      {/* System status */}
      <div className="px-4 py-3.5 border-t border-line-soft">
        <div className="flex items-center gap-2 text-[11px] font-semibold">
          <span className={`w-2 h-2 rounded-full ${isGloballyPaused ? 'bg-amber-400 animate-pulse-soft' : 'bg-emerald-400'}`} />
          <span className={isGloballyPaused ? 'text-amber-400' : 'text-emerald-400'}>
            {isGloballyPaused ? 'Hệ thống tạm dừng' : 'Hệ thống sẵn sàng'}
          </span>
        </div>
      </div>
    </aside>
  );
};
