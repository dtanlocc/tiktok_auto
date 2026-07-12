// File: frontend/src/components/AccountsTable.tsx
import React, { useMemo, useState } from 'react';
import { CheckSquare, Square, Folder, Pause, Play, Search, ArrowUp, ArrowDown, ArrowUpDown, Copy, X } from 'lucide-react';
import { Account, Proxy } from '../types';
import { getCountryFlagUrl } from '../utils/countries'; // <-- NẠP TẬP TRUNG TỪ UTILS CHUẨN XÁC

interface AccountsTableProps {
  accounts: Account[];
  proxies: Proxy[];
  selectedAccountIds: string[];
  toggleSelectAll: () => void;
  toggleSelectAccount: (id: string) => void;
  handleBindProxy: (accountId: string, proxyId: string) => void;
  handleRowContextMenu: (e: React.MouseEvent, accountId: string) => void;
  onPauseAccount: (accountId: string) => void;
  onResumeAccount: (accountId: string) => void;
  // NÂNG CẤP: cho phép App.tsx set thẳng danh sách đã chọn (cần cho Shift+Click
  // chọn cả dải và click-đâu-cũng-chọn kiểu bảng SQL view)
  setSelectedAccountIds: (ids: string[]) => void;
}

type SortKey = 'username' | 'country' | 'batch_tag' | 'status' | 'health_status' | 'profile_status' | 'created_at';
type SortDirection = 'asc' | 'desc' | null;

const SORTABLE_COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'username', label: 'Tài khoản' },
  { key: 'country', label: 'Quốc Gia / Lô hàng' },
  { key: 'status', label: 'Phiên chạy' },
  { key: 'health_status', label: 'Sức khỏe Nick' },
  { key: 'profile_status', label: 'Cập nhật Profile' },
];

export const AccountsTable: React.FC<AccountsTableProps> = ({
  accounts,
  proxies,
  selectedAccountIds,
  toggleSelectAll,
  toggleSelectAccount,
  handleBindProxy,
  handleRowContextMenu,
  onPauseAccount,
  onResumeAccount,
  setSelectedAccountIds,
}) => {
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>(null);
  const [lastClickedIndex, setLastClickedIndex] = useState<number | null>(null);

  // =========================================================================
  // TÌM NHANH (giống thanh search của 1 bảng SQL view) - lọc theo username/ID
  // =========================================================================
  const searchedAccounts = useMemo(() => {
    if (!searchQuery.trim()) return accounts;
    const q = searchQuery.trim().toLowerCase();
    return accounts.filter(
      (a) => a.username.toLowerCase().includes(q) || a.id.toLowerCase().includes(q)
    );
  }, [accounts, searchQuery]);

  // =========================================================================
  // SẮP XẾP THEO CỘT - bấm vào tiêu đề cột để đổi asc -> desc -> bỏ sắp xếp
  // =========================================================================
  const displayedAccounts = useMemo(() => {
    if (!sortKey || !sortDirection) return searchedAccounts;
    const sorted = [...searchedAccounts].sort((a, b) => {
      const valA = String(a[sortKey] ?? '').toLowerCase();
      const valB = String(b[sortKey] ?? '').toLowerCase();
      if (valA < valB) return sortDirection === 'asc' ? -1 : 1;
      if (valA > valB) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });
    return sorted;
  }, [searchedAccounts, sortKey, sortDirection]);

  const handleSort = (key: SortKey) => {
    if (sortKey !== key) {
      setSortKey(key);
      setSortDirection('asc');
    } else if (sortDirection === 'asc') {
      setSortDirection('desc');
    } else {
      setSortKey(null);
      setSortDirection(null);
    }
  };

  const renderSortIcon = (key: SortKey) => {
    if (sortKey !== key) return <ArrowUpDown className="w-3 h-3 text-slate-600" />;
    return sortDirection === 'asc' ? <ArrowUp className="w-3 h-3 text-teal-400" /> : <ArrowDown className="w-3 h-3 text-teal-400" />;
  };

  // =========================================================================
  // CHỌN Ở BẤT KỲ ĐÂU TRÊN HÀNG (không cần tích vào ô) - giống hành vi 1
  // bảng dữ liệu chuyên nghiệp (Excel/DB admin tool):
  //   - Click thường: bật/tắt chọn đúng hàng đó (cộng dồn vào lựa chọn hiện tại)
  //   - Shift + Click: chọn nhanh cả dải từ lần click gần nhất tới hàng này
  // =========================================================================
  const handleRowClick = (e: React.MouseEvent, accountId: string, rowIndex: number) => {
    // Bỏ qua nếu người dùng đang thao tác trên control tương tác thật sự bên
    // trong hàng (dropdown chọn Proxy, nút Tạm dừng/Tiếp tục...) - những chỗ
    // này đã tự stopPropagation() riêng, nhưng phòng thủ thêm ở đây cho chắc.
    const target = e.target as HTMLElement;
    if (target.closest('select, button, a, input')) return;

    if (e.shiftKey && lastClickedIndex !== null) {
      const start = Math.min(lastClickedIndex, rowIndex);
      const end = Math.max(lastClickedIndex, rowIndex);
      const rangeIds = displayedAccounts.slice(start, end + 1).map((a) => a.id);
      // Gộp dải mới vào lựa chọn hiện tại (không xoá những gì đã chọn trước đó)
      const merged = Array.from(new Set([...selectedAccountIds, ...rangeIds]));
      setSelectedAccountIds(merged);
    } else {
      toggleSelectAccount(accountId);
      setLastClickedIndex(rowIndex);
    }
  };

  const handleCopyUsername = (e: React.MouseEvent, username: string) => {
    e.stopPropagation();
    navigator.clipboard.writeText(username).catch(() => {});
  };

  return (
    <div className="bg-[#0e1424] rounded-2xl border border-slate-800 overflow-hidden flex-1 flex flex-col">
      <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-[#141b2e]/50 flex-wrap gap-3">
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

          {selectedAccountIds.length > 0 && (
            <button
              onClick={() => setSelectedAccountIds([])}
              className="text-[10px] font-bold text-slate-500 hover:text-rose-400 flex items-center gap-1"
            >
              <X className="w-3 h-3" /> Bỏ chọn
            </button>
          )}
        </div>

        {/* Ô TÌM NHANH */}
        <div className="flex items-center gap-2 bg-[#182032] border border-slate-700 rounded-lg px-2.5 w-full sm:w-64">
          <Search className="w-3.5 h-3.5 text-slate-500 shrink-0" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Tìm theo username hoặc ID..."
            className="w-full bg-transparent py-1.5 text-xs focus:outline-none text-slate-200"
          />
          {searchQuery && (
            <button onClick={() => setSearchQuery('')} className="text-slate-500 hover:text-slate-300 shrink-0">
              <X className="w-3 h-3" />
            </button>
          )}
        </div>

        <div className="text-[10px] text-slate-400 italic hidden lg:block">
          💡 Click <span className="text-teal-400 font-bold">bất kỳ đâu</span> trên hàng để chọn ·
          {' '}<span className="text-teal-400 font-bold">Shift+Click</span> chọn cả dải ·
          {' '}<span className="text-teal-400 font-bold">Chuột phải</span> để mở Menu nâng cao
        </div>
      </div>

      <div className="overflow-y-auto max-h-[380px] flex-1">
        <table className="w-full text-left border-collapse">
          <thead className="sticky top-0 z-10">
            <tr className="bg-[#141b2e] text-xs font-semibold text-slate-400 uppercase">
              <th className="p-4 w-12 text-center">Tích</th>
              {SORTABLE_COLUMNS.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className="p-4 cursor-pointer select-none hover:text-slate-200 transition-colors"
                  title="Bấm để sắp xếp"
                >
                  <span className="inline-flex items-center gap-1.5">
                    {col.label} {renderSortIcon(col.key)}
                  </span>
                </th>
              ))}
              <th className="p-4">Liên kết IP Proxy</th>
              <th className="p-4">Tiến trình chạy</th>
              <th className="p-4 text-center">Điều khiển</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 text-xs">
            {displayedAccounts.length === 0 ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-slate-500 font-semibold">
                  {searchQuery
                    ? `Không tìm thấy tài khoản nào khớp với "${searchQuery}".`
                    : 'Không tìm thấy tài khoản nào khớp với bộ lọc hoặc Lô đang chọn.'}
                </td>
              </tr>
            ) : (
              displayedAccounts.map((acc: Account, rowIndex: number) => {
                const isSelected = selectedAccountIds.includes(acc.id);
                return (
                  <tr
                    key={acc.id}
                    onClick={(e) => handleRowClick(e, acc.id, rowIndex)}
                    onContextMenu={(e) => handleRowContextMenu(e, acc.id)}
                    className={`hover:bg-slate-900/40 cursor-pointer transition-colors select-none ${
                      isSelected ? 'bg-teal-500/[0.07] border-l-2 border-l-teal-400' : 'border-l-2 border-l-transparent'
                    }`}
                  >
                    <td className="p-4 text-center">
                      <button
                        onClick={(e) => { e.stopPropagation(); toggleSelectAccount(acc.id); setLastClickedIndex(rowIndex); }}
                        className="text-slate-400 hover:text-teal-400"
                      >
                        {isSelected ? (
                          <CheckSquare className="w-4 h-4 text-teal-400" />
                        ) : (
                          <Square className="w-4 h-4" />
                        )}
                      </button>
                    </td>

                    <td className="p-4 font-medium text-slate-200">
                      <div className="flex items-center gap-1.5 group">
                        <span>{acc.username}</span>
                        <button
                          onClick={(e) => handleCopyUsername(e, acc.username)}
                          className="opacity-0 group-hover:opacity-100 text-slate-500 hover:text-teal-400 transition-opacity"
                          title="Sao chép username"
                        >
                          <Copy className="w-3 h-3" />
                        </button>
                      </div>
                      <div className="text-[10px] text-slate-500 font-mono mt-0.5">{acc.id}</div>
                    </td>

                    {/* QUỐC GIA & PHÂN LÔ ĐỒ HỌA SẮC NÉT CHẠY HOÀN HẢO TRÊN WINDOWS */}
                    <td className="p-4">
                      <div className="flex items-center gap-2 font-bold text-slate-100">
                        <img
                          src={getCountryFlagUrl(acc.country)}
                          alt={acc.country}
                          className="w-4.5 h-3.5 object-cover rounded-sm border border-slate-800 shadow-sm shrink-0"
                          onError={(e) => { e.currentTarget.style.display = 'none'; }}
                        />
                        <span className="text-[11px] uppercase tracking-wider">{acc.country}</span>
                      </div>
                      <div className="text-[10px] text-slate-400 font-medium mt-1 flex items-center gap-1">
                        <Folder className="w-3 h-3 text-slate-500" /> {acc.batch_tag}
                      </div>
                      <div className="text-[9px] text-slate-500 font-mono mt-0.5">
                        📅 {acc.created_at || "N/A"}
                      </div>
                    </td>

                    <td className="p-4 text-center">
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-bold border ${
                        acc.status === 'RUNNING' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30 animate-pulse' :
                        acc.status === 'QUEUED' ? 'bg-teal-500/10 text-teal-400 border-teal-500/30' :
                        acc.status === 'SUCCESS' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' :
                        acc.status === 'ERROR' ? 'bg-rose-500/10 text-rose-400 border-rose-500/30' :
                        'bg-slate-500/10 text-slate-400 border-slate-500/30'
                      }`}>
                        {acc.status}
                      </span>
                    </td>

                    {/* CỘT SỨC KHỎE NICK - THỐNG NHẤT 1 TẬP GIÁ TRỊ VỚI LUỒNG LOGIN
                        (BANNED / ALIVE / chưa biết) - không còn "DEAD" riêng nữa */}
                    <td className="p-4 text-center">
                      <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-extrabold border ${
                        acc.health_status === 'BANNED'
                          ? 'bg-red-500/15 text-red-400 border-red-500/40 animate-pulse font-black'
                          : acc.health_status === 'ALIVE'
                          ? 'bg-green-500/10 text-green-400 border-green-500/30'
                          : 'bg-slate-800/40 text-slate-400 border-slate-700/50' // MÀU XÁM CHO TRẠNG THÁI CHƯA BIẾT (UNKNOWN)
                      }`}>
                        ● {
                          acc.health_status === 'BANNED' ? 'ĐÃ BỊ BAN' :
                          acc.health_status === 'ALIVE' ? 'ĐANG SỐNG' :
                          'CHƯA BIẾT'
                        }
                      </span>
                    </td>

                    <td className="p-4 text-center">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border ${
                        acc.profile_status === 'COMPLETED'
                          ? 'bg-indigo-500/15 text-indigo-400 border-indigo-500/30'
                          : 'bg-slate-700/20 text-slate-400 border-slate-700/40'
                      }`}>
                        {acc.profile_status === 'COMPLETED' ? '✓ ĐÃ ĐỔI PROFILE' : '⚡ CHƯA ĐỔI'}
                      </span>
                    </td>

                    <td className="p-4" onClick={(e) => e.stopPropagation()}>
                      <select
                        value={acc.proxy_id || 'none'}
                        onChange={(e) => handleBindProxy(acc.id, e.target.value)}
                        className="bg-[#182032] border border-slate-700 rounded-lg p-1.5 text-xs text-teal-400 font-medium focus:outline-none focus:ring-1 focus:ring-teal-400"
                      >
                        <option value="none">Mạng LAN (Không Proxy)</option>
                        {proxies.map((p) => (
                          <option key={p.id} value={p.id}>
                            [{p.protocol.toUpperCase()}] {p.host}:{p.port}
                          </option>
                        ))}
                      </select>
                    </td>

                    <td className="p-4 font-mono font-bold text-slate-300">
                      {acc.status === 'RUNNING' ? (
                        <span className="flex items-center gap-1 text-amber-400 animate-pulse">
                          ⏳ {acc.current_step}
                        </span>
                      ) : (
                        <span>{acc.current_step}</span>
                      )}
                    </td>

                    {/* NÚT TẠM DỪNG / TIẾP TỤC RIÊNG TỪNG ACCOUNT */}
                    <td className="p-4 text-center" onClick={(e) => e.stopPropagation()}>
                      {(acc.status === 'RUNNING' || acc.is_paused) ? (
                        acc.is_paused ? (
                          <button
                            onClick={() => onResumeAccount(acc.id)}
                            className="inline-flex items-center gap-1 bg-teal-500/10 hover:bg-teal-500/20 border border-teal-500/30 text-teal-400 text-[10px] font-bold px-2.5 py-1 rounded-md transition-all animate-pulse"
                            title="Tiếp tục lại tài khoản này"
                          >
                            <Play className="w-3 h-3" /> Tiếp tục
                          </button>
                        ) : (
                          <button
                            onClick={() => onPauseAccount(acc.id)}
                            className="inline-flex items-center gap-1 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 text-amber-400 text-[10px] font-bold px-2.5 py-1 rounded-md transition-all"
                            title="Tạm dừng tài khoản này tại checkpoint gần nhất để can thiệp thủ công"
                          >
                            <Pause className="w-3 h-3" /> Tạm dừng
                          </button>
                        )
                      ) : (
                        <span className="text-slate-600 text-[10px]">—</span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
