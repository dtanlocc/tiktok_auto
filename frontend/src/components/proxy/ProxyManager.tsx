import React, { useMemo, useState } from 'react';
import { Activity, Globe, Pencil, Plus, Search, Trash2, Upload } from 'lucide-react';
import { Account, Proxy } from '../../types';
import { proxyApi, proxyName } from '../../services/proxyApi';
import { ProxyStatusBadge, ReachChips } from './ProxyStatus';
import { timeAgo } from './timeAgo';
import { ProxyFormModal } from './ProxyFormModal';
import { ProxyImportModal } from './ProxyImportModal';
import { ProxyDeleteModal } from './ProxyDeleteModal';

interface ProxyManagerProps {
  proxies: Proxy[];
  accounts: Account[];
  onChanged: () => void;
}

type Notice = { tone: 'ok' | 'error'; text: string } | null;

export const ProxyManager: React.FC<ProxyManagerProps> = ({ proxies, accounts, onChanged }) => {
  const [query, setQuery] = useState('');
  const [checking, setChecking] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<Proxy | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [deleting, setDeleting] = useState<Proxy | null>(null);
  const [notice, setNotice] = useState<Notice>(null);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return proxies;
    return proxies.filter((p) =>
      [p.label, p.note, p.host, String(p.port), p.username, p.exit_ip, p.country]
        .some((v) => (v || '').toLowerCase().includes(q)));
  }, [proxies, query]);

  const enabledCount = proxies.filter((p) => p.enabled !== false).length;
  const troubled = proxies.filter((p) => p.check_status === 'FAIL' || p.check_status === 'WARN').length;
  const assigned = proxies.reduce((sum, p) => sum + (p.account_count || 0), 0);

  const runCheck = async (ids?: string[]) => {
    const targets = ids ?? proxies.map((p) => p.id);
    setChecking((current) => new Set([...current, ...targets]));
    setNotice(null);
    try {
      await proxyApi.check(ids);
      onChanged();
    } catch (err) {
      setNotice({ tone: 'error', text: (err as Error).message });
    } finally {
      setChecking((current) => {
        const next = new Set(current);
        targets.forEach((id) => next.delete(id));
        return next;
      });
    }
  };

  const toggle = async (proxy: Proxy) => {
    setNotice(null);
    try {
      await proxyApi.update(proxy.id, { enabled: proxy.enabled === false });
      onChanged();
    } catch (err) {
      setNotice({ tone: 'error', text: (err as Error).message });
    }
  };

  const openForm = (proxy: Proxy | null) => { setEditing(proxy); setFormOpen(true); };
  const allChecking = proxies.length > 0 && proxies.every((p) => checking.has(p.id));

  return (
    <div className="card overflow-hidden flex-1 flex flex-col min-h-0">
      <div className="p-3.5 border-b border-line-soft bg-surface-2/40 flex flex-wrap items-center gap-2">
        <Globe className="w-4 h-4 text-brand" />
        <h2 className="font-semibold text-fg text-sm">Kho Proxy</h2>
        <span className="text-[11px] text-fg-subtle">
          {proxies.length} proxy · {enabledCount} đang bật · {assigned} account đang gán
          {troubled > 0 && <span className="text-warn"> · {troubled} cần xem</span>}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <label className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-subtle" />
            <input className="field !py-1.5 !pl-8 !text-xs w-48" value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Tìm tên, host, IP..." aria-label="Tìm proxy" />
          </label>
          <button className="btn btn-ghost btn-sm" onClick={() => runCheck()} disabled={!proxies.length || allChecking}
            title="Kiểm tra qua từng proxy: IP ra, quốc gia, độ trễ, vào được TikTok và CDN của TikTok">
            <Activity className="w-3.5 h-3.5" /> {allChecking ? 'Đang kiểm tra...' : 'Kiểm tra tất cả'}
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => setImportOpen(true)}>
            <Upload className="w-3.5 h-3.5" /> Nhập hàng loạt
          </button>
          <button className="btn btn-primary btn-sm" onClick={() => openForm(null)}>
            <Plus className="w-3.5 h-3.5" /> Thêm proxy
          </button>
        </div>
      </div>

      {notice && (
        <div role="status" className={`px-3.5 py-2 text-xs border-b border-line-soft ${notice.tone === 'error' ? 'text-danger bg-danger/5' : 'text-ok bg-ok/5'}`}>
          {notice.text}
        </div>
      )}

      <div className="overflow-auto flex-1">
        <table className="w-full text-left border-collapse min-w-[900px]">
          <thead className="sticky top-0 z-10">
            <tr className="bg-surface-2 text-[11px] font-semibold text-fg-subtle uppercase tracking-wide">
              <th className="p-3 w-16">Bật</th>
              <th className="p-3">Tên / ghi chú</th>
              <th className="p-3">Proxy</th>
              <th className="p-3 text-right">Account</th>
              <th className="p-3">Tình trạng</th>
              <th className="p-3 w-28 text-right">Thao tác</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line-soft text-xs">
            {proxies.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-10 text-center text-fg-subtle">
                  Chưa có proxy nào. Bấm <b className="text-fg-muted">Thêm proxy</b> hoặc <b className="text-fg-muted">Nhập hàng loạt</b>.
                </td>
              </tr>
            ) : visible.length === 0 ? (
              <tr><td colSpan={6} className="p-8 text-center text-fg-subtle">Không có proxy khớp "{query}".</td></tr>
            ) : (
              visible.map((p) => {
                const off = p.enabled === false;
                return (
                  <tr key={p.id} className={`transition-colors hover:bg-white/[0.02] ${off ? 'opacity-55' : ''}`}>
                    <td className="p-3">
                      <button
                        role="switch"
                        aria-checked={!off}
                        aria-label={`${off ? 'Bật' : 'Tắt'} proxy ${proxyName(p)}`}
                        onClick={() => toggle(p)}
                        className={`relative w-9 h-5 rounded-full transition-colors ${off ? 'bg-line' : 'bg-brand'}`}
                        title={off ? 'Đang tắt: không phân bổ, account gán vào sẽ dừng khi chạy' : 'Đang bật'}
                      >
                        <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${off ? 'left-0.5' : 'left-[18px]'}`} />
                      </button>
                    </td>
                    <td className="p-3 max-w-[220px]">
                      <div className="font-semibold text-fg truncate">{p.label || <span className="text-fg-subtle font-normal">Chưa đặt tên</span>}</div>
                      {p.note && <div className="text-[11px] text-fg-subtle truncate" title={p.note}>{p.note}</div>}
                    </td>
                    <td className="p-3">
                      <div className="flex items-center gap-2">
                        <span className="badge bg-brand/10 text-brand border border-brand/25">{p.protocol}</span>
                        <span className="font-mono">
                          <span className="text-fg font-semibold">{p.host}</span>
                          <span className="text-fg-subtle">:</span>
                          <span className="text-brand">{p.port}</span>
                        </span>
                      </div>
                      <div className="text-[11px] text-fg-subtle mt-0.5 font-mono">
                        {p.username ? `${p.username}${p.has_password ? ' · ••••' : ''}` : 'không xác thực'}
                      </div>
                    </td>
                    <td className="p-3 text-right font-mono text-fg">{p.account_count ?? 0}</td>
                    <td className="p-3">
                      <div className="flex items-center gap-2 flex-wrap">
                        <ProxyStatusBadge proxy={p} checking={checking.has(p.id)} />
                        <ReachChips proxy={p} />
                      </div>
                      {p.checked_at && (
                        <div className="text-[11px] text-fg-subtle mt-1">
                          {[p.exit_ip && `IP ${p.exit_ip}`, p.country, p.latency_ms != null && `${p.latency_ms} ms`, timeAgo(p.checked_at)]
                            .filter(Boolean).join(' · ')}
                        </div>
                      )}
                      {p.check_error && (
                        <div className="text-[11px] text-warn mt-0.5 max-w-[340px] line-clamp-2" title={p.check_error}>{p.check_error}</div>
                      )}
                    </td>
                    <td className="p-3">
                      <div className="flex justify-end gap-1">
                        <button className="btn btn-ghost btn-sm !px-2" onClick={() => runCheck([p.id])} disabled={checking.has(p.id)}
                          aria-label={`Kiểm tra ${proxyName(p)}`} title="Kiểm tra proxy này">
                          <Activity className="w-3.5 h-3.5" />
                        </button>
                        <button className="btn btn-ghost btn-sm !px-2" onClick={() => openForm(p)}
                          aria-label={`Sửa ${proxyName(p)}`} title="Sửa">
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        <button className="btn btn-danger btn-sm !px-2" onClick={() => { setNotice(null); setDeleting(p); }}
                          aria-label={`Xóa ${proxyName(p)}`}
                          title={(p.account_count || 0) > 0
                            ? `Đang gán cho ${p.account_count} account - sẽ hỏi chuyển sang proxy khác rồi xóa`
                            : 'Xóa proxy'}>
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <ProxyFormModal isOpen={formOpen} proxy={editing} onClose={() => setFormOpen(false)} onSaved={onChanged} />
      <ProxyDeleteModal
        proxy={deleting}
        accountsOnProxy={deleting ? accounts.filter((a) => a.proxy_id === deleting.id) : []}
        otherProxies={deleting ? proxies.filter((p) => p.id !== deleting.id) : []}
        onClose={() => setDeleting(null)}
        onDone={(message) => { setNotice({ tone: 'ok', text: message }); onChanged(); }}
      />
      <ProxyImportModal
        isOpen={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={(message) => { setNotice({ tone: 'ok', text: message }); onChanged(); }}
      />
    </div>
  );
};
