import React from 'react';
import { LogIn, KeyRound, Image, Globe, ScanSearch, Trash2 } from 'lucide-react';

interface ContextMenuProps {
  x: number;
  y: number;
  selectedCount: number;
  onBulkLogin: (method: 'COOKIE' | 'CREDENTIAL') => void;
  onBulkUpdateProfile: () => void;
  onAutoAllocateProxies: () => void;
  onBulkDelete: () => void;
  onQuickHealthCheck: () => void;
}

export const ContextMenu: React.FC<ContextMenuProps> = ({
  x, y, selectedCount,
  onBulkLogin, onBulkUpdateProfile, onAutoAllocateProxies, onBulkDelete, onQuickHealthCheck,
}) => {
  const items: { icon: React.ComponentType<{ className?: string }>; label: string; hint?: string; onClick: () => void }[] = [
    { icon: LogIn, label: 'Đăng nhập bằng Cookie', onClick: () => onBulkLogin('COOKIE') },
    { icon: KeyRound, label: 'Đăng nhập Form + OTP', onClick: () => onBulkLogin('CREDENTIAL') },
    { icon: Image, label: 'Đổi Avatar & Bio', onClick: onBulkUpdateProfile },
    { icon: Globe, label: 'Tự phân bổ Proxy', onClick: onAutoAllocateProxies },
    { icon: ScanSearch, label: 'Check nhanh sống/chết', hint: 'Kiểm tra nhanh, độc lập với hàng đợi login', onClick: onQuickHealthCheck },
  ];

  return (
    <div
      style={{ top: y, left: x }}
      className="fixed z-50 min-w-[236px] rounded-xl p-1.5 bg-elevated/95 border border-line shadow-2xl shadow-black/60 backdrop-blur-md text-fg"
    >
      <div className="px-2.5 py-1.5 mb-1 text-[10px] font-semibold uppercase tracking-wide text-fg-subtle border-b border-line-soft">
        {selectedCount} tài khoản đã chọn
      </div>

      {items.map(({ icon: Icon, label, hint, onClick }) => (
        <button
          key={label}
          onClick={onClick}
          title={hint}
          className="w-full text-left px-2.5 py-2 rounded-lg flex items-center gap-2.5 text-[13px] font-medium
                     text-fg-muted hover:text-fg hover:bg-white/6 transition-colors duration-150 cursor-pointer"
        >
          <Icon className="w-4 h-4 shrink-0 text-fg-subtle" />
          <span className="truncate">{label}</span>
        </button>
      ))}

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
