import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowRight, X } from 'lucide-react';
import { Account, Proxy } from '../../types';
import { proxyApi, proxyName } from '../../services/proxyApi';
import { ProxyStatusBadge } from './ProxyStatus';

interface ProxyAllocateModalProps {
  isOpen: boolean;
  accounts: Account[];
  onClose: () => void;
  onDone: (message: string) => void;
}

/** Same order the backend round-robins in (proxy_allocation._proxy_sort_key). */
const sortKey = (p: Proxy) => [p.protocol.toLowerCase(), p.host.toLowerCase(), String(p.port).padStart(5, '0'), p.id];
const compare = (a: Proxy, b: Proxy) => {
  const ka = sortKey(a); const kb = sortKey(b);
  for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] < kb[i] ? -1 : 1;
  return 0;
};

export const ProxyAllocateModal: React.FC<ProxyAllocateModalProps> = ({ isOpen, accounts, onClose, onDone }) => {
  const [proxies, setProxies] = useState<Proxy[]>([]);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!isOpen) return;
    setError('');
    setLoading(true);
    proxyApi.list()
      .then((list) => {
        setProxies(list);
        // Default: every enabled proxy whose last check did not fail.
        setChosen(new Set(list.filter((p) => p.enabled !== false && p.check_status !== 'FAIL').map((p) => p.id)));
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setLoading(false));
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !saving) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, saving, onClose]);

  const eligible = useMemo(() => accounts.filter((a) => !a.is_sold), [accounts]);
  const soldCount = accounts.length - eligible.length;

  const plan = useMemo(() => {
    const picked = proxies.filter((p) => chosen.has(p.id)).sort(compare);
    const base = picked.length ? Math.floor(eligible.length / picked.length) : 0;
    const extra = picked.length ? eligible.length % picked.length : 0;
    const share = new Map(picked.map((p, i) => [p.id, base + (i < extra ? 1 : 0)]));
    const leaving = new Map<string, number>();
    eligible.forEach((a) => { if (a.proxy_id) leaving.set(a.proxy_id, (leaving.get(a.proxy_id) || 0) + 1); });
    const after = new Map(proxies.map((p) => [p.id, (p.account_count || 0) - (leaving.get(p.id) || 0) + (share.get(p.id) || 0)]));
    return { picked, base, extra, after };
  }, [proxies, chosen, eligible]);

  if (!isOpen) return null;

  const toggle = (id: string) => setChosen((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const selectWhere = (predicate: (p: Proxy) => boolean) =>
    setChosen(new Set(proxies.filter((p) => p.enabled !== false && predicate(p)).map((p) => p.id)));

  const submit = async () => {
    setSaving(true);
    setError('');
    try {
      const result = await proxyApi.allocate(accounts.map((a) => a.id), plan.picked.map((p) => p.id));
      onDone(result.message);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const perProxy = plan.picked.length
    ? (plan.extra ? `${plan.base}–${plan.base + 1}` : `${plan.base}`)
    : '0';

  return createPortal(
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-black/75 p-4 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !saving) onClose(); }}
    >
      <div role="dialog" aria-modal="true" aria-labelledby="proxy-allocate-title"
        className="card w-full max-w-2xl max-h-[90vh] flex flex-col shadow-2xl shadow-black/60">
        <div className="flex items-center justify-between p-4 border-b border-line-soft">
          <div>
            <h2 id="proxy-allocate-title" className="font-semibold text-sm text-fg">Phân bổ proxy</h2>
            <p className="text-[11px] text-fg-subtle mt-0.5">
              Chia đều lại {eligible.length} account đã chọn cho các proxy được tích
              {soldCount > 0 && ` (bỏ qua ${soldCount} account ĐÃ BÁN)`}.
            </p>
          </div>
          <button type="button" onClick={onClose} disabled={saving} className="text-fg-subtle hover:text-fg" aria-label="Đóng">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-4 pt-3 flex flex-wrap gap-2 text-[11px]">
          <button className="btn btn-ghost btn-sm" onClick={() => selectWhere(() => true)}>Chọn mọi proxy đang bật</button>
          <button className="btn btn-ghost btn-sm" onClick={() => selectWhere((p) => p.check_status === 'OK')}>Chỉ proxy kiểm tra Tốt</button>
          <button className="btn btn-ghost btn-sm" onClick={() => setChosen(new Set())}>Bỏ chọn tất cả</button>
        </div>

        <div className="p-4 overflow-auto flex-1">
          {loading ? (
            <p className="text-xs text-fg-subtle p-6 text-center">Đang tải danh sách proxy...</p>
          ) : proxies.length === 0 ? (
            <p className="text-xs text-fg-subtle p-6 text-center">Kho chưa có proxy. Hãy thêm proxy ở mục Proxy trước.</p>
          ) : (
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="text-[11px] text-fg-subtle uppercase tracking-wide">
                  <th className="pb-2 w-8" />
                  <th className="pb-2">Proxy</th>
                  <th className="pb-2">Tình trạng</th>
                  <th className="pb-2 text-right">Account: hiện tại → sau</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-soft">
                {proxies.map((p) => {
                  const off = p.enabled === false;
                  const current = p.account_count || 0;
                  const after = plan.after.get(p.id) ?? current;
                  return (
                    <tr key={p.id} className={off ? 'opacity-50' : 'cursor-pointer hover:bg-white/[0.02]'}
                      onClick={() => { if (!off) toggle(p.id); }}>
                      <td className="py-2.5">
                        <input type="checkbox" className="accent-teal-400" checked={chosen.has(p.id)} disabled={off}
                          onChange={() => toggle(p.id)} onClick={(e) => e.stopPropagation()}
                          aria-label={`Chọn ${proxyName(p)}`} />
                      </td>
                      <td className="py-2.5">
                        <div className="font-semibold text-fg">{proxyName(p)}{off && <span className="ml-2 text-[10px] text-fg-subtle font-normal">đang tắt</span>}</div>
                        {p.label && <div className="text-[11px] font-mono text-fg-subtle">{p.host}:{p.port}</div>}
                      </td>
                      <td className="py-2.5">
                        <ProxyStatusBadge proxy={p} />
                        {p.country && <span className="ml-2 text-[11px] text-fg-subtle">{p.country}</span>}
                      </td>
                      <td className="py-2.5 text-right font-mono">
                        <span className="text-fg-muted">{current}</span>
                        <ArrowRight className="inline w-3 h-3 mx-1.5 text-fg-subtle" />
                        <span className={after !== current ? 'text-brand font-semibold' : 'text-fg-muted'}>{after}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {error && <p role="alert" className="text-xs text-danger mt-3">{error}</p>}
        </div>

        <div className="flex items-center justify-between gap-3 p-4 border-t border-line-soft">
          <span className="text-[11px] text-fg-muted">
            {plan.picked.length
              ? `${eligible.length} account → ${plan.picked.length} proxy, mỗi proxy ${perProxy} account`
              : 'Chưa chọn proxy nào'}
          </span>
          <div className="flex gap-2">
            <button type="button" onClick={onClose} disabled={saving} className="btn btn-ghost">Hủy</button>
            <button type="button" onClick={submit} disabled={saving || !plan.picked.length || !eligible.length} className="btn btn-primary">
              {saving ? 'Đang phân bổ...' : 'Phân bổ'}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
};
