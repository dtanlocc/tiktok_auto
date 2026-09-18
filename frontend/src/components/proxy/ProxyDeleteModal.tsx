import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, X } from 'lucide-react';
import { Account, Proxy } from '../../types';
import { proxyApi, proxyName } from '../../services/proxyApi';
import { ProxyStatusBadge } from './ProxyStatus';

interface ProxyDeleteModalProps {
  proxy: Proxy | null;
  /** Accounts currently on this proxy (sold ones included: they cannot be moved). */
  accountsOnProxy: Account[];
  /** Proxies that can take those accounts over. */
  otherProxies: Proxy[];
  onClose: () => void;
  onDone: (message: string) => void;
}

export const ProxyDeleteModal: React.FC<ProxyDeleteModalProps> = ({
  proxy, accountsOnProxy, otherProxies, onClose, onDone,
}) => {
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<'move' | 'detach'>('move');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const movable = accountsOnProxy.filter((a) => !a.is_sold);
  const sold = accountsOnProxy.length - movable.length;
  const takers = otherProxies.filter((p) => p.enabled !== false);

  useEffect(() => {
    if (!proxy) return;
    setError('');
    setBusy(false);
    setChosen(new Set(takers.filter((p) => p.check_status !== 'FAIL').map((p) => p.id)));
    setMode(takers.length ? 'move' : 'detach');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proxy?.id]);

  useEffect(() => {
    if (!proxy) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [proxy, busy, onClose]);

  if (!proxy) return null;

  const toggle = (id: string) => setChosen((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const run = async () => {
    setBusy(true);
    setError('');
    try {
      if (accountsOnProxy.length && mode === 'move') {
        const result = await proxyApi.allocate(movable.map((a) => a.id), [...chosen]);
        await proxyApi.remove(proxy.id);
        onDone(`Đã chuyển ${result.allocated} account sang ${chosen.size} proxy khác và xóa ${proxyName(proxy)}.`);
      } else {
        // detach: the backend clears proxy_id on every account first, sold ones included.
        const result = await proxyApi.remove(proxy.id, accountsOnProxy.length > 0);
        onDone(result.message);
      }
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const blocked = accountsOnProxy.length > 0 && mode === 'move' && (takers.length === 0 || chosen.size === 0);
  const actionLabel = accountsOnProxy.length === 0
    ? 'Xóa proxy'
    : mode === 'move'
      ? `Chuyển ${movable.length} account rồi xóa`
      : `Gỡ proxy khỏi ${accountsOnProxy.length} account rồi xóa`;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-black/75 p-4 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}
    >
      <div role="dialog" aria-modal="true" aria-labelledby="proxy-delete-title"
        className="card w-full max-w-lg shadow-2xl shadow-black/60">
        <div className="flex items-center justify-between p-4 border-b border-line-soft">
          <h2 id="proxy-delete-title" className="font-semibold text-sm text-fg flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-danger" /> Xóa proxy {proxyName(proxy)}
          </h2>
          <button type="button" onClick={onClose} disabled={busy} className="text-fg-subtle hover:text-fg" aria-label="Đóng">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-3 text-xs text-fg-muted">
          <p className="font-mono text-fg">{proxy.protocol}://{proxy.host}:{proxy.port}</p>

          {movable.length === 0 && accountsOnProxy.length === 0 && (
            <p>Proxy này không còn account nào. Xóa sẽ không thể hoàn tác.</p>
          )}

          {accountsOnProxy.length > 0 && (
            <>
              <p>
                Proxy đang gán cho <b className="text-fg">{accountsOnProxy.length} account</b>. Chọn cách xử lý
                các account đó rồi xóa:
              </p>

              <label className={`flex gap-2 p-2 rounded-lg border cursor-pointer ${mode === 'move' ? 'border-brand/40 bg-brand/5' : 'border-line-soft'} ${takers.length === 0 ? 'opacity-50 cursor-not-allowed' : ''}`}>
                <input type="radio" className="mt-0.5 accent-teal-400" name="proxy-delete-mode" value="move"
                  checked={mode === 'move'} disabled={takers.length === 0}
                  onChange={() => setMode('move')} />
                <span>
                  <b className="text-fg">Chuyển sang proxy khác</b>
                  {takers.length === 0
                    ? <span className="block text-warn">Không còn proxy nào đang bật để nhận.</span>
                    : <span className="block text-fg-subtle">Chia đều {movable.length} account cho các proxy được tích bên dưới.</span>}
                  {sold > 0 && takers.length > 0 && (
                    <span className="block text-warn">
                      {sold} account ĐÃ BÁN không chuyển được nên cách này sẽ bị từ chối xóa.
                    </span>
                  )}
                </span>
              </label>

              <label className={`flex gap-2 p-2 rounded-lg border cursor-pointer ${mode === 'detach' ? 'border-brand/40 bg-brand/5' : 'border-line-soft'}`}>
                <input type="radio" className="mt-0.5 accent-teal-400" name="proxy-delete-mode" value="detach"
                  checked={mode === 'detach'} onChange={() => setMode('detach')} />
                <span>
                  <b className="text-fg">Gỡ proxy — để account chạy Mạng thật</b>
                  <span className="block text-fg-subtle">
                    Cả {accountsOnProxy.length} account thành "Mạng LAN (không proxy)".
                  </span>
                  <span className="block text-warn">
                    Ở chế độ Proxy, account không có proxy sẽ KHÔNG chạy được; phải bật "Mạng thật" ở thanh điều khiển.
                  </span>
                </span>
              </label>

              {mode === 'move' && takers.length > 0 && (
                <div className="space-y-1 pl-2">
                  {takers.map((p) => (
                    <label key={p.id} className="flex items-center gap-2 py-1 cursor-pointer">
                      <input type="checkbox" className="accent-teal-400" checked={chosen.has(p.id)}
                        onChange={() => toggle(p.id)} aria-label={`Chọn ${proxyName(p)}`} />
                      <span className="text-fg">{proxyName(p)}</span>
                      <span className="font-mono text-fg-subtle">{p.host}:{p.port}</span>
                      <ProxyStatusBadge proxy={p} />
                      <span className="text-fg-subtle ml-auto">{p.account_count ?? 0} account</span>
                    </label>
                  ))}
                </div>
              )}
            </>
          )}

          {error && <p role="alert" className="text-danger">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 p-4 border-t border-line-soft">
          <button type="button" onClick={onClose} disabled={busy} className="btn btn-ghost">Hủy</button>
          <button type="button" onClick={run} disabled={busy || blocked} className="btn btn-danger">
            {busy ? 'Đang xử lý...' : actionLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
};
