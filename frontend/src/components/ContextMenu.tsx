import React, { useState } from 'react';
import { LogIn, KeyRound, Image, Globe, ScanSearch, Trash2, Wifi, ChevronRight, Check, Shuffle, Mail, AtSign, ShieldCheck, MailQuestion } from 'lucide-react';

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
  loginIdentifier: 'email' | 'username';       // gõ gì vào ô "Email or username"
  onLoginWithIdentifier: (mode: 'email' | 'username') => void;
  verifyMethod: '2fa' | 'otp';                 // vượt màn xác minh bằng 2FA hay OTP email
  onSetVerifyMethod: (mode: '2fa' | 'otp') => void;
}

const rowCls =
  'w-full text-left px-2.5 py-2 rounded-lg flex items-center gap-2.5 text-[13px] font-medium ' +
  'text-fg-muted hover:text-fg hover:bg-white/6 transition-colors duration-150 cursor-pointer';

export const ContextMenu: React.FC<ContextMenuProps> = ({
  x, y, selectedCount,
  onBulkLogin, onBulkUpdateProfile, onAutoAllocateProxies, onBulkDelete, onQuickHealthCheck,
  proxyMode, onSetProxyMode,
  loginIdentifier, onLoginWithIdentifier,
  verifyMethod, onSetVerifyMethod,
}) => {
  const [showProxySub, setShowProxySub] = useState(false);
  const [showLoginSub, setShowLoginSub] = useState(false);

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
      {/* ==== FORM + OTP: hover ra submenu chọn gõ hộp thư hay username ====
           Ô "Email or username" của TikTok nhận cả hai, nhưng chúng KHÔNG
           tương đương: cả hai tên đều từng bị từ chối "Account doesn't exist"
           trên máy thật, mỗi tên một lần. Người vận hành biết lô account của
           mình nhập từ đâu, nên chọn ở đây đúng hơn là ghim trong code.
           Dù chọn gì, khi TikTok nói không có tài khoản thì app vẫn bấm lại
           bằng tên còn lại - nên lựa chọn chỉ quyết định tên nào tốn lượt đầu. */}
      <div
        className="relative"
        onMouseEnter={() => setShowLoginSub(true)}
        onMouseLeave={() => setShowLoginSub(false)}
      >
        <button className={rowCls} onClick={() => onBulkLogin('CREDENTIAL')}>
          <KeyRound className="w-4 h-4 shrink-0 text-fg-subtle" />
          <span className="truncate flex-1">Đăng nhập Form + OTP</span>
          <span className="text-[10px] text-fg-subtle mr-1">
            {loginIdentifier === 'email' ? 'Hộp thư' : 'Username'}
          </span>
          <ChevronRight className="w-3.5 h-3.5 shrink-0 text-fg-subtle" />
        </button>

        {showLoginSub && (
          <div className="absolute left-full top-0 -ml-1 pl-1.5 z-50">
            <div className="min-w-[246px] rounded-xl p-1.5 bg-elevated/95 border border-line shadow-2xl shadow-black/60 backdrop-blur-md">
              <button className={rowCls} onClick={() => onLoginWithIdentifier('email')}>
                <Mail className="w-4 h-4 shrink-0 text-fg-subtle" />
                <span className="truncate flex-1">Gõ hộp thư trước</span>
                {loginIdentifier === 'email' && <Check className="w-4 h-4 shrink-0 text-brand" />}
              </button>
              <button className={rowCls} onClick={() => onLoginWithIdentifier('username')}>
                <AtSign className="w-4 h-4 shrink-0 text-fg-subtle" />
                <span className="truncate flex-1">Gõ username trước</span>
                {loginIdentifier === 'username' && <Check className="w-4 h-4 shrink-0 text-brand" />}
              </button>
              <div className="px-2.5 pt-1 pb-0.5 text-[10px] text-fg-subtle leading-snug">
                Chọn rồi chạy luôn. Nếu TikTok báo không có tài khoản, app tự thử lại bằng tên còn lại.
              </div>

              {/* ==== Sub nhỏ: vượt màn xác minh bằng 2FA hay OTP email ====
                   Đây là TUỲ CHỌN được lưu, KHÔNG tự chạy login (khác hai dòng
                   trên). Mặc định "2FA": nick có khoá thì dùng authenticator,
                   không có khoá thì tự lui về OTP email. */}
              <div className="my-1 border-t border-line-soft" />
              <div className="px-2.5 pt-0.5 pb-1 text-[10px] font-semibold text-fg-subtle uppercase tracking-wide">
                Mã xác minh
              </div>
              <button className={rowCls} onClick={() => onSetVerifyMethod('2fa')}>
                <ShieldCheck className="w-4 h-4 shrink-0 text-fg-subtle" />
                <span className="truncate flex-1">Ưu tiên 2FA (nếu có khoá)</span>
                {verifyMethod === '2fa' && <Check className="w-4 h-4 shrink-0 text-brand" />}
              </button>
              <button className={rowCls} onClick={() => onSetVerifyMethod('otp')}>
                <MailQuestion className="w-4 h-4 shrink-0 text-fg-subtle" />
                <span className="truncate flex-1">Chỉ OTP email</span>
                {verifyMethod === 'otp' && <Check className="w-4 h-4 shrink-0 text-brand" />}
              </button>
              <div className="px-2.5 pt-1 pb-0.5 text-[10px] text-fg-subtle leading-snug">
                2FA: nick có khoá đăng nhập bằng authenticator, không có thì lui về OTP. OTP: luôn đọc mã từ hộp thư.
              </div>
            </div>
          </div>
        )}
      </div>
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
              {/* 1) Chạy qua proxy đã gán cho từng account */}
              <button
                className={rowCls}
                onClick={() => onSetProxyMode(true)}
              >
                <Globe className="w-4 h-4 shrink-0 text-fg-subtle" />
                <span className="truncate flex-1">Dùng proxy đã gán</span>
                {proxyMode && <Check className="w-4 h-4 shrink-0 text-brand" />}
              </button>
              {/* 2) Chọn proxy nào được chia cho các account đã chọn */}
              <button
                className={rowCls}
                onClick={onAutoAllocateProxies}
              >
                <Shuffle className="w-4 h-4 shrink-0 text-fg-subtle" />
                <span className="truncate flex-1">Phân bổ proxy…</span>
              </button>
              {/* 3) Dùng mạng thật (không proxy) - cho VPN toàn máy */}
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
