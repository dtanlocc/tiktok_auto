import React from 'react';
import { CheckSquare, Square, ChevronDown } from 'lucide-react';
import { Account, Proxy } from '../store/useAppStore';

interface AccountsTableProps {
  accounts: Account[];
  proxies: Proxy[];
  selectedAccountIds: string[];
  toggleSelectAll: () => void;
  toggleSelectAccount: (id: string) => void;
  handleBindProxy: (accountId: string, proxyId: string) => void;
  handleRowContextMenu: (e: React.MouseEvent, accountId: string) => void;
}

export const AccountsTable: React.FC<AccountsTableProps> = ({
  accounts,
  proxies,
  selectedAccountIds,
  toggleSelectAll,
  toggleSelectAccount,
  handleBindProxy,
  handleRowContextMenu
}) => {
  return (
    <div className="bg-[#0e1424] rounded-2xl border border-slate-800 overflow-hidden flex-1 flex flex-col">
      <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-[#141b2e]/50">
        <div className="flex items-center gap-3">
          <button
            onClick={toggleSelectAll}
            className="text-xs font-bold text-teal-400 hover:text-teal-300 flex items-center gap-1.5"
          >
            {selectedAccountIds.length === accounts.length && accounts.length > 0 ? (
              <CheckSquare className="w-4 h-4" />
            ) : (
              <Square className="w-4 h-4" />
            )}
            <span>Tích chọn tất cả ({selectedAccountIds.length})</span>
          </button>
        </div>
        <div className="text-xs text-slate-400 italic">
          💡 Click <span className="text-teal-400 font-bold">Chuột phải</span> lên hàng tài khoản đã chọn để mở Menu nâng cao
        </div>
      </div>

      <div className="overflow-y-auto max-h-[380px] flex-1">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-[#141b2e] text-xs font-semibold text-slate-400 uppercase">
              <th className="p-4 w-12 text-center">Tích</th>
              <th className="p-4">Tài khoản</th>
              <th className="p-4">Liên kết IP Proxy</th>
              <th className="p-4">Trạng thái</th>
              <th className="p-4">Tiến trình chạy</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 text-xs">
            {accounts.length === 0 ? (
              <tr>
                <td colSpan={5} className="p-8 text-center text-slate-500 font-semibold">
                  Chưa có tài khoản nào. Vui lòng tải các file .txt lên để nhập hàng loạt.
                </td>
              </tr>
            ) : (
              accounts.map((acc: Account) => (
                <tr 
                  key={acc.id} 
                  onContextMenu={(e) => handleRowContextMenu(e, acc.id)}
                  className={`hover:bg-slate-900/40 cursor-context-menu transition-colors ${selectedAccountIds.includes(acc.id) ? 'bg-slate-900/20' : ''}`}
                >
                  <td className="p-4 text-center">
                    <button onClick={() => toggleSelectAccount(acc.id)} className="text-slate-400 hover:text-teal-400">
                      {selectedAccountIds.includes(acc.id) ? (
                        <CheckSquare className="w-4 h-4 text-teal-400" />
                      ) : (
                        <Square className="w-4 h-4" />
                      )}
                    </button>
                  </td>
                  <td className="p-4 font-medium text-slate-200">
                    <div>{acc.username}</div>
                    <div className="text-[10px] text-slate-500 font-mono mt-0.5">{acc.id}</div>
                  </td>
                  <td className="p-4">
                    <select
                      value={acc.proxy_id || 'none'}
                      onChange={(e) => handleBindProxy(acc.id, e.target.value)}
                      className="bg-[#182032] border border-slate-700 rounded-lg p-1.5 text-xs text-teal-400 font-medium focus:outline-none focus:ring-1 focus:ring-teal-400"
                    >
                      <option value="none">Mạng LAN (Không Proxy)</option>
                      {proxies.map((p: Proxy) => (
                        <option key={p.id} value={p.id}>
                          [{p.protocol.toUpperCase()}] {p.host}:{p.port}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="p-4">
                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold border ${
                      acc.status === 'RUNNING' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' :
                      acc.status === 'QUEUED' ? 'bg-teal-500/10 text-teal-400 border-teal-500/30' :
                      acc.status === 'LOGGED_IN' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' :
                      acc.status === 'ERROR' ? 'bg-rose-500/10 text-rose-400 border-rose-500/30' :
                      'bg-slate-500/10 text-slate-400 border-slate-500/30'
                    }`}>
                      {acc.status}
                    </span>
                  </td>
                  <td className="p-4 font-mono font-bold text-slate-300">
                    {acc.status === 'RUNNING' ? (
                      <span className="flex items-center gap-1 text-amber-400 animate-pulse">
                        ⏳ {acc.current_step}
                      </span>
                    ) : acc.status === 'QUEUED' ? (
                      <span className="text-teal-400">⏳ {acc.current_step}</span>
                    ) : (
                      <span>{acc.current_step}</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};