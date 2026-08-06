import React from 'react';
import { Globe } from 'lucide-react';
import { Proxy } from '../store/useAppStore';

interface ProxiesTableProps {
  proxies: Proxy[];
}

export const ProxiesTable: React.FC<ProxiesTableProps> = ({ proxies }) => {
  return (
    <div className="card overflow-hidden flex-1 flex flex-col">
      <div className="p-3.5 border-b border-line-soft bg-surface-2/40 flex items-center gap-2">
        <Globe className="w-4 h-4 text-brand" />
        <h2 className="font-semibold text-fg text-sm">Kho lưu trữ IP Proxy</h2>
        <span className="badge bg-white/5 text-fg-subtle ml-1">{proxies.length}</span>
      </div>
      <div className="overflow-y-auto flex-1">
        <table className="w-full text-left border-collapse">
          <thead className="sticky top-0 z-10">
            <tr className="bg-surface-2 text-[11px] font-semibold text-fg-subtle uppercase tracking-wide">
              <th className="p-3.5">Giao thức</th>
              <th className="p-3.5">Địa chỉ Proxy (Host : Port)</th>
              <th className="p-3.5">Tài khoản xác thực</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line-soft text-xs">
            {proxies.length === 0 ? (
              <tr>
                <td colSpan={3} className="p-10 text-center text-fg-subtle">
                  Chưa có proxy nào. Tải lên file <span className="font-mono text-fg-muted">proxies.txt</span> để nhập hàng loạt.
                </td>
              </tr>
            ) : (
              proxies.map((p) => (
                <tr key={p.id} className="hover:bg-white/[0.02] transition-colors">
                  <td className="p-3.5">
                    <span className="badge bg-brand/10 text-brand border border-brand/25">{p.protocol.toUpperCase()}</span>
                  </td>
                  <td className="p-3.5 font-mono">
                    <span className="text-fg font-semibold">{p.host}</span>
                    <span className="text-fg-subtle">:</span>
                    <span className="text-brand">{p.port}</span>
                  </td>
                  <td className="p-3.5 text-fg-subtle">{p.username || '—'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
