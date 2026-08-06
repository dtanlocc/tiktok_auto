import React, { useState } from 'react';
import { Users, Heart, MonitorPlay, Globe, Bot, PanelLeftClose, PanelLeftOpen } from 'lucide-react';

type Tab = 'accounts' | 'proxies' | 'interactions' | 'screens';

interface NavSidebarProps {
  activeTab: Tab;
  setActiveTab: (t: Tab) => void;
  isGloballyPaused: boolean;
  collapsed: boolean;
  onToggle: () => void;
}

const NAV: { id: Tab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'accounts', label: 'Tài khoản', icon: Users },
  { id: 'interactions', label: 'Tương tác video', icon: Heart },
  { id: 'screens', label: 'Màn hình trực tiếp', icon: MonitorPlay },
  { id: 'proxies', label: 'Proxies', icon: Globe },
];

export const NavSidebar: React.FC<NavSidebarProps> = ({ activeTab, setActiveTab, isGloballyPaused, collapsed, onToggle }) => {
  // Khi thu gọn: hover vào để TỰ ĐỘNG bung ra (overlay, không đẩy nội dung chính).
  const [hover, setHover] = useState(false);
  const expanded = !collapsed || hover; // đang hiển thị dạng rộng (chữ + nhãn)

  return (
    // Wrapper GIỮ CHỖ theo trạng thái pin (64px thu gọn / 208px mở); aside bên trong
    // overlay khi hover nên không làm xê dịch layout.
    <div className={`shrink-0 h-screen sticky top-0 relative transition-[width] duration-200 ease-out ${collapsed ? 'w-[64px]' : 'w-[208px]'}`}>
      <aside
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        className={`absolute inset-y-0 left-0 flex flex-col bg-surface border-r border-line-soft transition-[width] duration-200 ease-out
          ${expanded ? 'w-[208px]' : 'w-[64px]'} ${collapsed && hover ? 'z-40 shadow-2xl shadow-black/50' : 'z-10'}`}
      >
        {/* Brand + toggle */}
        <div className={`flex items-center h-14 border-b border-line-soft ${expanded ? 'gap-2.5 px-3.5' : 'justify-center'}`}>
          <div className="grid place-items-center w-8 h-8 rounded-lg bg-brand/15 border border-brand/25 text-brand shrink-0">
            <Bot className="w-[18px] h-[18px]" />
          </div>
          {expanded && (
            <div className="min-w-0 flex-1">
              <h1 className="text-[13px] font-bold tracking-tight text-fg leading-tight truncate">TikTok Automation</h1>
              <p className="text-[10px] text-fg-subtle leading-tight">Multi-thread</p>
            </div>
          )}
          {expanded && (
            <button
              onClick={onToggle}
              title={collapsed ? 'Ghim mở sidebar' : 'Thu gọn sidebar'}
              aria-label="Bật/tắt sidebar"
              className="grid place-items-center w-7 h-7 rounded-md text-fg-subtle hover:text-fg hover:bg-white/5 transition-colors cursor-pointer shrink-0"
            >
              {collapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
            </button>
          )}
        </div>

        {/* Nav */}
        <nav className={`flex-1 py-2.5 flex flex-col gap-0.5 ${expanded ? 'px-2.5' : 'px-2'}`} role="tablist" aria-label="Điều hướng">
          {NAV.map(({ id, label, icon: Icon }) => {
            const active = activeTab === id;
            return (
              <button
                key={id}
                role="tab"
                aria-selected={active}
                onClick={() => setActiveTab(id)}
                title={!expanded ? label : undefined}
                className={`group relative flex items-center rounded-lg text-[13px] font-semibold transition-colors duration-150 cursor-pointer
                  ${expanded ? 'gap-3 px-2.5 py-2' : 'justify-center h-10'}
                  ${active ? 'bg-brand/12 text-brand' : 'text-fg-muted hover:text-fg hover:bg-white/5'}`}
              >
                <span className={`absolute left-0 top-1/2 -translate-y-1/2 w-1 h-5 rounded-r-full bg-brand transition-opacity ${active ? 'opacity-100' : 'opacity-0'}`} />
                <Icon className="w-[18px] h-[18px] shrink-0" />
                {expanded && <span className="truncate">{label}</span>}
              </button>
            );
          })}
        </nav>

        {/* System status */}
        <div className={`py-3 border-t border-line-soft ${expanded ? 'px-3.5' : 'flex justify-center'}`}>
          {expanded ? (
            <div className="flex items-center gap-2 text-[11px] font-semibold">
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
      </aside>
    </div>
  );
};
