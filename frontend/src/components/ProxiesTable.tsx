import React from 'react';
import { Proxy } from '../store/useAppStore';

interface ProxiesTableProps {
  proxies: Proxy[];
}

export const ProxiesTable: React.FC<ProxiesTableProps> = ({ proxies }) => {
  return (
    <div className="bg-[#0e1424] rounded-2xl border border-slate-800 overflow-hidden flex-1 flex flex-col">
      <div className="p-4 border-b border-slate-800 bg-[#141b2e]/50">
        <h2 className="font-bold text-slate-200">Kho lưu trữ IP Proxy</h2>
      </div>
      <div className="overflow-y-auto max-h-[420px] flex-1">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-[#141b2e] text-xs font-semibold text-slate-400 uppercase">
              <th className="p-4">Giao thức</th>
              <th className="p-4">Địa chỉ IP / Host</th>
              <th className="p-4">Cổng (Port)</th>
              <th className="p-4">Tài khoản xác thực</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 text-xs text-slate-300">
            {proxies.length === 0 ? (
              <tr>
                <td colSpan={4} className="p-8 text-center text-slate-500 font-semibold">
                  Chưa có proxy nào. Vui lòng tải lên file proxies.txt để nhập hàng loạt.
                </td>
              </tr>
            ) : (
              proxies.map((p) => (
                <tr key={p.id} className="hover:bg-slate-900/40 font-mono">
                  <td className="p-4"><span className="bg-teal-500/10 text-teal-400 border border-teal-500/30 px-2 py-0.5 rounded font-bold text-[10px]">{p.protocol.toUpperCase()}</span></td>
                  <td className="p-4 text-slate-100 font-bold">{p.host}</td>
                  <td className="p-4 text-slate-300">{p.port}</td>
                  <td className="p-4 text-slate-500">{p.username ? p.username : 'Không có'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};