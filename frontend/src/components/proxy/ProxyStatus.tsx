import React from 'react';
import { Proxy } from '../../types';

const STATUS: Record<string, { text: string; cls: string; dot: string }> = {
  OK: { text: 'Tốt', cls: 'bg-ok/10 text-ok border-ok/25', dot: 'bg-ok' },
  WARN: { text: 'Cảnh báo', cls: 'bg-warn/10 text-warn border-warn/25', dot: 'bg-warn' },
  FAIL: { text: 'Lỗi', cls: 'bg-danger/10 text-danger border-danger/25', dot: 'bg-danger' },
  UNCHECKED: { text: 'Chưa kiểm tra', cls: 'bg-white/5 text-fg-subtle border-line', dot: 'bg-fg-subtle' },
};

const statusMeta = (status?: string) => STATUS[status || 'UNCHECKED'] || STATUS.UNCHECKED;

export const ProxyStatusBadge: React.FC<{ proxy: Proxy; checking?: boolean }> = ({ proxy, checking }) => {
  if (checking) {
    return (
      <span className="badge border bg-brand/10 text-brand border-brand/25">
        <span className="w-1.5 h-1.5 rounded-full bg-brand animate-pulse-soft" /> Đang kiểm tra
      </span>
    );
  }
  const meta = statusMeta(proxy.check_status);
  return (
    <span className={`badge border ${meta.cls}`} title={proxy.check_error || undefined}>
      <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} /> {meta.text}
    </span>
  );
};

/** "TikTok ✓ · CDN ✗" - unknown parts are left out. */
export const ReachChips: React.FC<{ proxy: Proxy }> = ({ proxy }) => {
  const chip = (name: string, ok?: boolean | null) =>
    ok === null || ok === undefined ? null : (
      <span className={ok ? 'text-ok' : 'text-danger'}>
        {name} {ok ? '✓' : '✗'}
      </span>
    );
  const parts = [chip('TikTok', proxy.tiktok_ok), chip('CDN', proxy.cdn_ok)].filter(Boolean);
  if (!parts.length) return null;
  return (
    <span className="inline-flex items-center gap-2 text-[11px] font-medium">
      {parts.map((part, i) => <React.Fragment key={i}>{part}</React.Fragment>)}
    </span>
  );
};
