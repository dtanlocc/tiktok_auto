import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Eye, EyeOff, X } from 'lucide-react';
import { Proxy, ProxyInput } from '../../types';
import { proxyApi } from '../../services/proxyApi';

interface ProxyFormModalProps {
  /** null = add a new proxy */
  proxy: Proxy | null;
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
}

const PROTOCOLS = ['socks5', 'http', 'https'];

export const ProxyFormModal: React.FC<ProxyFormModalProps> = ({ proxy, isOpen, onClose, onSaved }) => {
  const [protocol, setProtocol] = useState('socks5');
  const [host, setHost] = useState('');
  const [port, setPort] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [label, setLabel] = useState('');
  const [note, setNote] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const hostRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    setProtocol(proxy?.protocol || 'socks5');
    setHost(proxy?.host || '');
    setPort(proxy ? String(proxy.port) : '');
    setUsername(proxy?.username || '');
    setPassword('');
    setShowPassword(false);
    setLabel(proxy?.label || '');
    setNote(proxy?.note || '');
    setEnabled(proxy?.enabled ?? true);
    setError('');
    setTimeout(() => hostRef.current?.focus(), 0);
  }, [isOpen, proxy]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !saving) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, saving, onClose]);

  if (!isOpen) return null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const portNumber = Number(port);
    if (!host.trim()) return setError('Nhập host của proxy.');
    if (!Number.isInteger(portNumber) || portNumber < 1 || portNumber > 65535) {
      return setError('Port phải từ 1 đến 65535.');
    }
    const input: ProxyInput = {
      protocol,
      host: host.trim(),
      port: portNumber,
      username: username.trim() || null,
      label: label.trim(),
      note: note.trim(),
      enabled,
    };
    // Editing: an empty password field keeps the stored password.
    if (!proxy || password) input.password = password || null;
    setSaving(true);
    setError('');
    try {
      if (proxy) await proxyApi.update(proxy.id, input);
      else await proxyApi.create(input);
      onSaved();
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-black/75 p-4 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !saving) onClose(); }}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="proxy-form-title"
        onSubmit={submit}
        className="card w-full max-w-lg shadow-2xl shadow-black/60"
      >
        <div className="flex items-center justify-between p-4 border-b border-line-soft">
          <h2 id="proxy-form-title" className="font-semibold text-sm text-fg">
            {proxy ? 'Sửa proxy' : 'Thêm proxy'}
          </h2>
          <button type="button" onClick={onClose} disabled={saving} className="text-fg-subtle hover:text-fg" aria-label="Đóng">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-3">
          <div className="grid grid-cols-[110px_1fr_110px] gap-2">
            <label className="flex flex-col gap-1 text-[11px] text-fg-subtle">
              Giao thức
              <select className="field" value={protocol} onChange={(e) => setProtocol(e.target.value)}>
                {PROTOCOLS.map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-fg-subtle">
              Host
              <input ref={hostRef} className="field font-mono" value={host} onChange={(e) => setHost(e.target.value)}
                placeholder="151.244.119.172" autoComplete="off" spellCheck={false} />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-fg-subtle">
              Port
              <input className="field font-mono" value={port} inputMode="numeric"
                onChange={(e) => setPort(e.target.value.replace(/[^\d]/g, ''))} placeholder="50101" />
            </label>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[11px] text-fg-subtle">
              Tài khoản proxy
              <input className="field font-mono" value={username} onChange={(e) => setUsername(e.target.value)}
                placeholder="(không có)" autoComplete="off" spellCheck={false} />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-fg-subtle">
              Mật khẩu
              <span className="relative">
                <input className="field font-mono w-full pr-9" type={showPassword ? 'text' : 'password'}
                  value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
                  placeholder={proxy?.has_password ? 'Để trống = giữ nguyên' : '(không có)'} />
                <button type="button" onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-fg-subtle hover:text-fg"
                  aria-label={showPassword ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}>
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </span>
            </label>
          </div>

          <label className="flex flex-col gap-1 text-[11px] text-fg-subtle">
            Tên / nhóm
            <input className="field" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="VD: Indo #1" maxLength={60} />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-fg-subtle">
            Ghi chú
            <textarea className="field min-h-[64px] resize-y" value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="Nhà cung cấp, ngày hết hạn, giới hạn kết nối..." />
          </label>

          <label className="flex items-center gap-2 text-xs text-fg cursor-pointer select-none">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="accent-teal-400" />
            Bật (được dùng khi phân bổ và khi chạy)
          </label>

          {error && <p role="alert" className="text-xs text-danger">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 p-4 border-t border-line-soft">
          <button type="button" onClick={onClose} disabled={saving} className="btn btn-ghost">Hủy</button>
          <button type="submit" disabled={saving} className="btn btn-primary">
            {saving ? 'Đang lưu...' : proxy ? 'Lưu thay đổi' : 'Thêm proxy'}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
};
