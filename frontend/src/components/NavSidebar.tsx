import React, { useState } from 'react';
import { Users, Heart, MonitorPlay, Globe, Film } from 'lucide-react';
import { AppTab } from '../types';

interface NavSidebarProps {
  activeTab: AppTab;
  setActiveTab: (t: AppTab) => void;
  isGloballyPaused: boolean;
}

const NAV: { id: AppTab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'accounts', label: 'Tài khoản', icon: Users },
  { id: 'videos', label: 'Quản lý video', icon: Film },
  { id: 'interactions', label: 'Tương tác video', icon: Heart },
  { id: 'screens', label: 'Màn hình trực tiếp', icon: MonitorPlay },
  { id: 'proxies', label: 'Proxies', icon: Globe },
];

export const NavSidebar: React.FC<NavSidebarProps> = ({ activeTab, setActiveTab, isGloballyPaused }) => {
  // LUÔN là rail biểu tượng; rê chuột vào -> TỰ ĐỘNG bung ra (overlay, không đẩy
  // nội dung). Rời chuột -> thu lại về rail. Không có chế độ ghim mở.
  const [hover, setHover] = useState(false);

  return (
    <aside
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className="relative sticky top-0 z-40 h-screen w-[60px] shrink-0"
    >
      <div className={`absolute inset-y-0 left-0 flex flex-col overflow-hidden border-r border-line-soft bg-surface shadow-2xl shadow-black/0 transition-[width,box-shadow] duration-200 ease-out ${hover ? 'w-[210px] shadow-black/25' : 'w-[60px]'}`}>
        {/* Brand */}
        <div className={`flex items-center h-14 border-b border-line-soft ${hover ? 'gap-2.5 px-3.5' : 'justify-center'}`}>
          <div className="grid place-items-center w-8 h-8 rounded-lg bg-brand/15 border border-brand/25 text-brand shrink-0">
            <img src="/app-mark.svg" alt="" className="w-7 h-7" aria-hidden="true" />
          </div>
          {hover && (
            <div className="min-w-0 flex-1 overflow-hidden">
              <h1 className="text-[13px] font-bold tracking-tight text-fg leading-tight truncate">TikTok Automation</h1>
              <p className="text-[10px] text-fg-subtle leading-tight">Multi-thread</p>
            </div>
          )}
        </div>

        {/* Nav */}
        <nav className={`flex-1 py-2.5 flex flex-col gap-0.5 ${hover ? 'px-2.5' : 'px-2'}`} role="tablist" aria-label="Điều hướng">
          {NAV.map(({ id, label, icon: Icon }) => {
            const active = activeTab === id;
            return (
              <button
                key={id}
                role="tab"
                aria-selected={active}
                onClick={() => setActiveTab(id)}
                title={!hover ? label : undefined}
                className={`group relative flex items-center rounded-lg text-[13px] font-semibold transition-colors duration-150 cursor-pointer
                  ${hover ? 'gap-3 px-2.5 py-2' : 'justify-center h-10'}
                  ${active ? 'bg-brand/12 text-brand' : 'text-fg-muted hover:text-fg hover:bg-white/5'}`}
              >
                <span className={`absolute left-0 top-1/2 -translate-y-1/2 w-1 h-5 rounded-r-full bg-brand transition-opacity ${active ? 'opacity-100' : 'opacity-0'}`} />
                <Icon className="w-[18px] h-[18px] shrink-0" />
                {hover && <span className="truncate whitespace-nowrap">{label}</span>}
              </button>
            );
          })}
        </nav>

        {/* System status */}
        <div className={`py-3 border-t border-line-soft ${hover ? 'px-3.5' : 'flex justify-center'}`}>
          {hover ? (
            <div className="flex items-center gap-2 text-[11px] font-semibold whitespace-nowrap">
              <span className={`w-2 h-2 rounded-full ${isGloballyPaused ? 'bg-amber-400 animate-pulse-soft' : 'bg-emerald-400'}`} />
              <span className={isGloballyPaused ? 'text-amber-400' : 'text-emerald-400'}>
                {isGloballyPaused ? 'Hệ thống tạm dừng' : 'Hệ thống sẵn sàng'}
              </span>
            </div>
          ) : (
            <span
              title={isGloballyPaused ? 'Hệ thống tạm dừng' : 'Hệ thống sẵn sàng'}
              className={`w-2.5 h-2.5 rounded-full ${isGloballyPaused ? 'bg-amber-400 animate-pulse-soft' : 'bg-emerald-400'}`}
            />
          )}
        </div>
      </div>
    </aside>
  );
};
