import React, { useCallback, useState } from 'react';
import { Users, Heart, MonitorPlay, Globe, Film, Settings2 } from 'lucide-react';
import { AppTab } from '../types';
import { CaptchaSettingsModal } from './CaptchaSettingsModal';

interface NavSidebarProps {
  activeTab: AppTab;
  setActiveTab: (t: AppTab) => void;
  isGloballyPaused: boolean;
  versions?: {
    app_version?: string;
    engine?: { label?: string; version?: string; engine_tag?: string; firefox?: string };
  } | null;
}

const NAV: { id: AppTab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'accounts', label: 'Tài khoản', icon: Users },
  { id: 'videos', label: 'Quản lý video', icon: Film },
  { id: 'interactions', label: 'Tương tác video', icon: Heart },
  { id: 'screens', label: 'Màn hình trực tiếp', icon: MonitorPlay },
  { id: 'proxies', label: 'Proxies', icon: Globe },
];

export const NavSidebar: React.FC<NavSidebarProps> = ({ activeTab, setActiveTab, isGloballyPaused, versions }) => {
  // Dòng chi tiết cho tooltip: gộp mọi thứ người hỗ trợ cần hỏi vào một chỗ.
  const e = versions?.engine;
  const chiTiet = [
    `Giao diện v${__APP_VERSION__}`,
    versions?.app_version ? `Backend ${versions.app_version}` : null,
    e?.version ? `${e.label ?? 'engine'} ${e.version}` : null,
    e?.firefox ? `Firefox ${e.firefox} (${e.engine_tag ?? '?'})` : null,
  ].filter(Boolean).join('\n');

  // LUÔN là rail biểu tượng; rê chuột vào -> TỰ ĐỘNG bung ra (overlay, không đẩy
  // nội dung). Rời chuột -> thu lại về rail. Không có chế độ ghim mở.
  const [hover, setHover] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const openSettings = useCallback(() => setSettingsOpen(true), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

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
              {/* "May dang chay ban nao" la cau hoi dau tien moi lan ho tro ai
                  do, nen so phien ban nam ngay canh ten app. `select-all` de
                  mot cu bam la boi den duoc ca so, khong phai go lai.
                  Rê chuột vào để xem đủ: backend, thư viện, đời Firefox. */}
              <p
                className="text-[10px] text-fg-subtle leading-tight truncate"
                title={chiTiet}
              >
                Multi-thread · <span className="select-all">v{__APP_VERSION__}</span>
                {versions?.app_version && versions.app_version !== __APP_VERSION__ && (
                  <span className="select-all"> · be {versions.app_version}</span>
                )}
              </p>
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

        {/* Settings + system status */}
        <div className={`py-3 border-t border-line-soft ${hover ? 'px-3.5' : 'flex justify-center'}`}>
          <button
            type="button"
            onClick={openSettings}
            aria-haspopup="dialog"
            title={!hover ? 'Cài đặt extension' : undefined}
            className={`mb-3 flex h-10 w-full cursor-pointer items-center rounded-lg text-[13px] font-semibold text-fg-muted transition-colors duration-150 hover:bg-white/5 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 ${hover ? 'gap-3 px-2.5' : 'justify-center'}`}
          >
            <Settings2 className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
            {hover && <span className="truncate whitespace-nowrap">Cài đặt extension</span>}
          </button>
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
      <CaptchaSettingsModal isOpen={settingsOpen} onClose={closeSettings} />
    </aside>
  );
};
