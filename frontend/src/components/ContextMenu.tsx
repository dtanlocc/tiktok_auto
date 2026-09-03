import React, { useState } from 'react';
import { LogIn, KeyRound, Image, Globe, ScanSearch, Trash2, Wifi, ChevronRight, Check } from 'lucide-react';

interface ContextMenuProps {
  x: number;
  y: number;
  selectedCount: number;
  onBulkLogin: (method: 'COOKIE' | 'CREDENTIAL') => void;
  onBulkUpdateProfile: () => void;
  onAutoAllocateProxies: () => void;
  onBulkDelete: () => void;
  onQuickHealthCheck: () => void;
  proxyMode: boolean;                          // true = dùng proxy (auto-map); false = mạng thật
  onSetProxyMode: (useProxy: boolean) => void; // đổi chế độ runtime
}

const rowCls =
  'w-full text-left px-2.5 py-2 rounded-lg flex items-center gap-2.5 text-[13px] font-medium ' +
  'text-fg-muted hover:text-fg hover:bg-white/6 transition-colors duration-150 cursor-pointer';

export const ContextMenu: React.FC<ContextMenuProps> = ({
  x, y, selectedCount,
  onBulkLogin, onBulkUpdateProfile, onAutoAllocateProxies, onBulkDelete, onQuickHealthCheck,
  proxyMode, onSetProxyMode,
}) => {
  const [showProxySub, setShowProxySub] = useState(false);

  return (
    <div
      style={{ top: y, left: x }}
      className="fixed z-50 min-w-[236px] rounded-xl p-1.5 bg-elevated/95 border border-line shadow-2xl shadow-black/60 backdrop-blur-md text-fg"
    >
      <div className="px-2.5 py-1.5 mb-1 text-[10px] font-semibold uppercase tracking-wide text-fg-subtle border-b border-line-soft">
        {selectedCount} tài khoản đã chọn
      </div>

      <button className={rowCls} onClick={() => onBulkLogin('COOKIE')}>
        <LogIn className="w-4 h-4 shrink-0 text-fg-subtle" /> <span className="truncate">Đăng nhập bằng Cookie</span>
      </button>
      <button className={rowCls} onClick={() => onBulkLogin('CREDENTIAL')}>
        <KeyRound className="w-4 h-4 shrink-0 text-fg-subtle" /> <span className="truncate">Đăng nhập Form + OTP</span>
      </button>
      <button className={rowCls} onClick={onBulkUpdateProfile}>
        <Image className="w-4 h-4 shrink-0 text-fg-subtle" /> <span className="truncate">Đổi Avatar & Bio</span>
      </button>

      {/* ==== PROXY: hover ra submenu chọn chế độ ==== */}
      <div
        className="relative"
        onMouseEnter={() => setShowProxySub(true)}
        onMouseLeave={() => setShowProxySub(false)}
      >
        <button className={rowCls}>
          <Globe className="w-4 h-4 shrink-0 text-fg-subtle" />
          <span className="truncate flex-1">Proxy / Mạng</span>
          <span className="text-[10px] text-fg-subtle mr-1">{proxyMode ? 'Proxy' : 'Mạng thật'}</span>
          <ChevronRight className="w-3.5 h-3.5 shrink-0 text-fg-subtle" />
        </button>

        {showProxySub && (
          <div className="absolute left-full top-0 -ml-1 pl-1.5 z-50">
            <div className="min-w-[238px] rounded-xl p-1.5 bg-elevated/95 border border-line shadow-2xl shadow-black/60 backdrop-blur-md">
              {/* 1) Tự động map proxy như cũ */}
              <button
                className={rowCls}
                onClick={() => { onSetProxyMode(true); onAutoAllocateProxies(); }}
              >
                <Globe className="w-4 h-4 shrink-0 text-fg-subtle" />
                <span className="truncate flex-1">Tự động map proxy</span>
                {proxyMode && <Check className="w-4 h-4 shrink-0 text-brand" />}
              </button>
              {/* 2) Dùng mạng thật (không proxy) - cho VPN toàn máy */}
              <button
                className={rowCls}
                onClick={() => onSetProxyMode(false)}
              >
                <Wifi className="w-4 h-4 shrink-0 text-fg-subtle" />
                <span className="truncate flex-1">Mạng thật – không proxy</span>
                {!proxyMode && <Check className="w-4 h-4 shrink-0 text-brand" />}
              </button>
              <div className="px-2.5 pt-1 pb-0.5 text-[10px] text-fg-subtle leading-snug">
                "Mạng thật" = chạy thẳng qua mạng máy (bật VPN toàn máy). Áp cho các phiên mở sau đó.
              </div>
            </div>
          </div>
        )}
      </div>

      <button className={rowCls} onClick={onQuickHealthCheck} title="Kiểm tra nhanh, độc lập với hàng đợi login">
        <ScanSearch className="w-4 h-4 shrink-0 text-fg-subtle" /> <span className="truncate">Check nhanh sống/chết</span>
      </button>

      <div className="h-px bg-line-soft my-1" />

      <button
        onClick={onBulkDelete}
        className="w-full text-left px-2.5 py-2 rounded-lg flex items-center gap-2.5 text-[13px] font-medium
                   text-danger/90 hover:text-danger hover:bg-danger/10 transition-colors duration-150 cursor-pointer"
      >
        <Trash2 className="w-4 h-4 shrink-0" />
        <span>Xóa tài khoản</span>
      </button>
    </div>
  );
};
