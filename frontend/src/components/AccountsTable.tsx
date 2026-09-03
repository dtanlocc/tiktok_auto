// File: frontend/src/components/AccountsTable.tsx
import React, { useMemo, useState } from 'react';
import { AlertCircle, BarChart3, CheckCircle2, CheckSquare, Square, Folder, Pause, Play, Search, ArrowUp, ArrowDown, ArrowUpDown, Copy, X, Square as StopSquare, Check, Zap, LockKeyhole, Mail, FlaskConical } from 'lucide-react';
import { Account, Proxy } from '../types';
import { getCountryFlagUrl } from '../utils/countries'; // <-- NẠP TẬP TRUNG TỪ UTILS CHUẨN XÁC
import { AccountAnalyticsModal } from './AccountAnalyticsModal';

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
  // Sửa trường trực tiếp trên UI (username/country/batch_tag) -> cập nhật ngay.
  onUpdateAccount: (accountId: string, fields: Partial<Account>) => void;
  // CHẾ ĐỘ PHIÊN TAY: mở trình duyệt HIỆN, login rồi giữ mở để thao tác tay.
  onRunOneTest: (accountId: string) => void;
  onStopRunOneTest: (accountId: string) => void;
  manualSessionIds: string[];
  onSyncAnalytics: (accountIds: string[]) => Promise<void>;
}

type SortKey = 'email' | 'username' | 'country' | 'batch_tag' | 'health_status' | 'profile_status' | 'created_at';
type SortDirection = 'asc' | 'desc' | null;

const SORTABLE_COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'email', label: 'Hotmail' },
  { key: 'username', label: 'Username' },
  { key: 'country', label: 'Quốc Gia / Lô hàng' },
  { key: 'health_status', label: 'Sức khỏe Nick' },
  { key: 'profile_status', label: 'Cập nhật Profile' },
];

const compactNumber = new Intl.NumberFormat('vi-VN', { notation: 'compact', maximumFractionDigits: 1 });
const formatMetric = (value: number | null | undefined) => value === null || value === undefined ? '—' : compactNumber.format(value);
const formatDateTime = (value: string) => value ? new Date(value).toLocaleString('vi-VN') : 'Chưa có';

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
  onUpdateAccount,
  onRunOneTest,
  onStopRunOneTest,
  manualSessionIds,
  onSyncAnalytics,
}) => {
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [batchSize, setBatchSize] = useState<number>(10);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>(null);
  const [lastClickedIndex, setLastClickedIndex] = useState<number | null>(null);
  const [analyticsAccount, setAnalyticsAccount] = useState<Account | null>(null);
  const selectedSet = useMemo(() => new Set(selectedAccountIds), [selectedAccountIds]);
  const allVisibleSelected = accounts.length > 0 && accounts.every((account) => selectedSet.has(account.id));

  // =========================================================================
  // SỬA TRƯỜNG TRỰC TIẾP (inline edit): double-click 1 ô -> hiện input ->
  // Enter/blur lưu ngay (gọi onUpdateAccount), Esc hủy.
  // =========================================================================
  type EditableField = 'email' | 'country' | 'batch_tag' | 'note';
  const [editing, setEditing] = useState<{ id: string; field: EditableField } | null>(null);
  const [editValue, setEditValue] = useState<string>('');

  const startEdit = (e: React.MouseEvent, id: string, field: EditableField, current: string) => {
    e.stopPropagation();
    setEditing({ id, field });
    setEditValue(current || '');
  };
  const commitEdit = () => {
    if (!editing) return;
    const val = editValue.trim();
    const acc = accounts.find((a) => a.id === editing.id);
    // note ĐƯỢC PHÉP rỗng (xóa ghi chú); các trường khác yêu cầu có giá trị.
    const changed = acc && val !== String(acc[editing.field] ?? '');
    const allowed = editing.field === 'note' || val.length > 0;
    if (changed && allowed) {
      onUpdateAccount(editing.id, { [editing.field]: val } as Partial<Account>);
    }
    setEditing(null);
  };
  const renderEditable = (acc: Account, field: EditableField, node: React.ReactNode) => {
    if (editing && editing.id === acc.id && editing.field === field) {
      return (
        <input
          autoFocus
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={commitEdit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitEdit();
            else if (e.key === 'Escape') setEditing(null);
          }}
          className="bg-surface border border-teal-500 rounded px-1.5 py-0.5 text-xs text-brand focus:outline-none w-full max-w-[180px]"
        />
      );
    }
    return (
      <span
        onDoubleClick={(e) => startEdit(e, acc.id, field, String(acc[field] ?? ''))}
        title="Nhấp đúp để sửa"
        className="cursor-text hover:bg-slate-700/40 rounded px-0.5"
      >
        {node}
      </span>
    );
  };

  // =========================================================================
  // Tìm theo Hotmail trước; username và ID chỉ là khóa đối chiếu phụ.
  // =========================================================================
  const searchedAccounts = useMemo(() => {
    if (!searchQuery.trim()) return accounts;
    const q = searchQuery.trim().toLowerCase();
    return accounts.filter(
      (a) => (a.email || '').toLowerCase().includes(q) || a.username.toLowerCase().includes(q) || a.id.toLowerCase().includes(q)
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
    if (sortKey !== key) return <ArrowUpDown className="w-3 h-3 text-fg-subtle" />;
    return sortDirection === 'asc' ? <ArrowUp className="w-3 h-3 text-brand" /> : <ArrowDown className="w-3 h-3 text-brand" />;
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

  const handleCopyValue = (e: React.MouseEvent, value: string) => {
    e.stopPropagation();
    navigator.clipboard.writeText(value).catch(() => {});
  };

  return (
    <>
    <div className="card overflow-hidden flex-1 flex flex-col">
      <div className="p-3.5 border-b border-line-soft flex justify-between items-center bg-surface-2/40 flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <button
            onClick={toggleSelectAll}
            className="text-xs font-bold text-brand hover:text-brand flex items-center gap-1.5"
          >
            {allVisibleSelected ? (
              <CheckSquare className="w-4 h-4" />
            ) : (
              <Square className="w-4 h-4" />
            )}
            <span>Tích chọn tất cả ({selectedAccountIds.length})</span>
          </button>

          {selectedAccountIds.length > 0 && (
            <button
              onClick={() => setSelectedAccountIds([])}
              className="text-[10px] font-bold text-fg-subtle hover:text-rose-400 flex items-center gap-1"
            >
              <X className="w-3 h-3" /> Bỏ chọn
            </button>
          )}

          {/* CHỌN NHANH THEO LÔ - mặc định 10 acc mỗi lần bấm, tự động lấy
              N tài khoản TIẾP THEO (đang hiển thị, chưa được chọn) cộng dồn
              vào lựa chọn hiện tại. Bấm liên tiếp để chọn 10, rồi 10 tiếp
              theo, rồi 10 tiếp theo nữa... */}
          <div className="flex items-center gap-1.5 bg-surface-2 border border-line rounded-lg pl-2 pr-1 py-1">
            <span className="text-[10px] text-fg-muted font-semibold">Chọn nhanh mỗi lần:</span>
            <input
              type="number" min={1} max={200}
              value={batchSize}
              onChange={(e) => setBatchSize(parseInt(e.target.value) || 10)}
              className="w-12 bg-transparent text-xs text-center font-bold text-brand focus:outline-none"
            />
            <button
              onClick={() => {
                const remaining = displayedAccounts.filter((a) => !selectedSet.has(a.id));
                const nextBatch = remaining.slice(0, batchSize).map((a) => a.id);
                setSelectedAccountIds([...selectedAccountIds, ...nextBatch]);
              }}
              className="bg-brand/10 hover:bg-brand/20 border border-brand/30 text-brand text-[10px] font-bold px-2 py-1 rounded-md transition-all whitespace-nowrap"
            >
              + Chọn {batchSize} tiếp theo
            </button>
          </div>
        </div>

        {/* Ô TÌM NHANH */}
        <div className="flex items-center gap-2 bg-surface-2 border border-line rounded-lg px-2.5 w-full sm:w-64">
          <Search className="w-3.5 h-3.5 text-fg-subtle shrink-0" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Tìm theo Hotmail, username hoặc ID..."
            className="w-full bg-transparent py-1.5 text-xs focus:outline-none text-fg"
          />
          {searchQuery && (
            <button onClick={() => setSearchQuery('')} className="text-fg-subtle hover:text-fg shrink-0">
              <X className="w-3 h-3" />
            </button>
          )}
        </div>

        <div className="text-[10px] text-fg-muted hidden lg:block">
          Click <span className="text-brand font-bold">bất kỳ đâu</span> trên hàng để chọn ·
          {' '}<span className="text-brand font-bold">Shift+Click</span> chọn cả dải ·
          {' '}<span className="text-brand font-bold">Chuột phải</span> để mở Menu nâng cao
        </div>
      </div>

      <div className="min-h-[380px] max-h-[620px] flex-1 overflow-auto overscroll-contain">
        <table className="w-full min-w-[1540px] text-left border-collapse">
          <thead className="sticky top-0 z-10">
            <tr className="bg-surface-2 text-[11px] font-semibold text-fg-subtle uppercase tracking-wide">
              <th className="px-3 py-2 w-12 text-center">Tích</th>
              {SORTABLE_COLUMNS.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className="px-3 py-2 cursor-pointer select-none hover:text-fg transition-colors"
                  title="Bấm để sắp xếp"
                >
                  <span className="inline-flex items-center gap-1.5">
                    {col.label} {renderSortIcon(col.key)}
                  </span>
                </th>
              ))}
              <th className="px-3 py-2">Khả năng đăng</th>
              <th className="px-3 py-2">Hiệu suất TikTok</th>
              <th className="px-3 py-2">Liên kết IP Proxy</th>
              <th className="px-3 py-2">Ghi chú</th>
              <th className="px-3 py-2 text-center">Điều khiển</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 text-xs">
            {displayedAccounts.length === 0 ? (
              <tr>
                <td colSpan={11} className="p-8 text-center text-fg-subtle font-semibold">
                  {searchQuery
                    ? `Không tìm thấy tài khoản nào khớp với "${searchQuery}".`
                    : 'Không tìm thấy tài khoản nào khớp với bộ lọc hoặc Lô đang chọn.'}
                </td>
              </tr>
            ) : (
              displayedAccounts.map((acc: Account, rowIndex: number) => {
                const isSelected = selectedSet.has(acc.id);
                return (
                  <tr
                    key={acc.id}
                    onClick={(e) => handleRowClick(e, acc.id, rowIndex)}
                    onContextMenu={(e) => handleRowContextMenu(e, acc.id)}
                    className={`hover:bg-slate-900/40 cursor-pointer transition-colors select-none ${
                      isSelected ? 'bg-teal-500/[0.07] border-l-2 border-l-teal-400' : 'border-l-2 border-l-transparent'
                    }`}
                  >
                    <td className="px-3 py-2 text-center">
                      <button
                        onClick={(e) => { e.stopPropagation(); toggleSelectAccount(acc.id); setLastClickedIndex(rowIndex); }}
                        className="text-fg-muted hover:text-brand"
                      >
                        {isSelected ? (
                          <CheckSquare className="w-4 h-4 text-brand" />
                        ) : (
                          <Square className="w-4 h-4" />
                        )}
                      </button>
                    </td>

                    <td className="px-3 py-2 font-medium text-fg min-w-[220px]">
                      <div className="flex items-center gap-1.5 group">
                        <Mail className="w-3.5 h-3.5 text-brand shrink-0" aria-hidden="true" />
                        {renderEditable(
                          acc,
                          'email',
                          acc.email || <span className="text-amber-300">Chưa có Hotmail</span>,
                        )}
                        <button
                          onClick={(e) => handleCopyValue(e, acc.email || '')}
                          disabled={!acc.email}
                          className="opacity-0 group-hover:opacity-100 text-fg-subtle hover:text-brand transition-opacity"
                          title="Sao chép Hotmail"
                        >
                          <Copy className="w-3 h-3" />
                        </button>
                      </div>
                      <div className="text-[10px] text-fg-subtle font-mono mt-0.5">{acc.id}</div>
                    </td>

                    <td className="px-3 py-2 min-w-[150px]">
                      <div className="flex items-center gap-1.5 group" title="Username do TikTok quản lý, chỉ hiển thị để đối chiếu">
                        <LockKeyhole className="w-3 h-3 text-fg-subtle shrink-0" aria-hidden="true" />
                        <span className="truncate font-medium text-fg-muted">@{acc.username || '—'}</span>
                        <button
                          onClick={(e) => handleCopyValue(e, acc.username)}
                          className="opacity-0 group-hover:opacity-100 text-fg-subtle hover:text-brand transition-opacity"
                          title="Sao chép username"
                          aria-label={`Sao chép username ${acc.username}`}
                        >
                          <Copy className="w-3 h-3" />
                        </button>
                      </div>
                      <span className="mt-1 inline-flex items-center gap-1 text-[10px] text-fg-subtle">
                        Chỉ đọc
                      </span>
                    </td>

                    {/* QUỐC GIA & PHÂN LÔ ĐỒ HỌA SẮC NÉT CHẠY HOÀN HẢO TRÊN WINDOWS */}
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2 font-bold text-fg">
                        <img
                          src={getCountryFlagUrl(acc.country)}
                          alt={acc.country}
                          className="w-4.5 h-3.5 object-cover rounded-sm border border-line-soft shadow-sm shrink-0"
                          onError={(e) => { e.currentTarget.style.display = 'none'; }}
                        />
                        <span className="text-[11px] uppercase tracking-wider">
                          {renderEditable(acc, 'country', acc.country)}
                        </span>
                      </div>
                      <div className="text-[10px] text-fg-muted font-medium mt-1 flex items-center gap-1">
                        <Folder className="w-3 h-3 text-fg-subtle" /> {renderEditable(acc, 'batch_tag', acc.batch_tag)}
                      </div>
                      <div className="text-[10px] text-fg-subtle font-mono mt-0.5">
                        {acc.created_at || "—"}
                      </div>
                    </td>

                    {/* CỘT SỨC KHỎE NICK - THỐNG NHẤT 1 TẬP GIÁ TRỊ VỚI LUỒNG LOGIN */}
                    <td className="px-3 py-2 text-center">
                      <span className={`badge border normal-case tracking-normal ${
                        acc.health_status === 'BANNED' ? 'bg-rose-500/12 text-rose-400 border-rose-500/30' :
                        acc.health_status === 'ALIVE' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25' :
                        'bg-white/5 text-fg-subtle border-line'
                      }`}>
                        <span className="w-1.5 h-1.5 rounded-full bg-current opacity-80" />
                        {acc.health_status === 'BANNED' ? 'Banned' : acc.health_status === 'ALIVE' ? 'Sống' : '—'}
                      </span>
                    </td>

                    <td className="px-3 py-2 text-center" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => onUpdateAccount(acc.id, {
                          profile_status: acc.profile_status === 'COMPLETED' ? 'PENDING' : 'COMPLETED',
                        })}
                        title="Nhấp để đổi trạng thái (ĐÃ ĐỔI ⇄ CHƯA ĐỔI)"
                        className={`badge border cursor-pointer transition-colors duration-150 ${
                          acc.profile_status === 'COMPLETED'
                            ? 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30 hover:bg-indigo-500/25'
                            : 'bg-white/5 text-fg-subtle border-line hover:text-fg-muted'
                        }`}
                      >
                        {acc.profile_status === 'COMPLETED'
                          ? <><Check className="w-3 h-3" /> Đã đổi</>
                          : <><Zap className="w-3 h-3" /> Chưa đổi</>}
                      </button>
                    </td>

                    <td className="min-w-[170px] px-3 py-2">
                      {acc.is_sold ? (
                        <><span className="badge border border-slate-500/30 bg-slate-500/10 text-slate-300 normal-case tracking-normal">ĐÃ BÁN</span><p className="mt-1.5 text-[10px] text-fg-subtle">Chỉ lưu trữ lịch sử, không vận hành.</p></>
                      ) : (acc.upload_success_count || 0) > 0 ? (
                        <span className="badge border border-emerald-500/25 bg-emerald-500/10 text-emerald-300 normal-case tracking-normal"><CheckCircle2 className="h-3 w-3" /> Đã đăng được</span>
                      ) : acc.last_upload_status === 'FAILED' ? (
                        <span className="badge border border-rose-500/25 bg-rose-500/10 text-rose-300 normal-case tracking-normal"><AlertCircle className="h-3 w-3" /> Cần chạy lại</span>
                      ) : (
                        <span className="badge border border-line bg-white/5 text-fg-subtle normal-case tracking-normal">Chưa xác minh</span>
                      )}
                      <p className="mt-1.5 text-[10px] tabular-nums text-fg-muted"><span className="text-emerald-300">{acc.upload_success_count || 0} thành công</span> · <span className="text-rose-300">{acc.upload_failure_count || 0} lỗi</span></p>
                      <p className="mt-0.5 max-w-[190px] truncate text-[10px] text-fg-subtle" title={acc.last_upload_error || formatDateTime(acc.last_upload_at)}>{acc.last_upload_status === 'FAILED' && acc.last_upload_error ? acc.last_upload_error : formatDateTime(acc.last_upload_at)}</p>
                    </td>

                    <td className="min-w-[215px] px-3 py-2" onClick={(e) => e.stopPropagation()}>
                      {acc.is_sold ? <div><span className="badge border border-slate-500/30 bg-slate-500/10 text-slate-300 normal-case tracking-normal">ĐÃ BÁN · chỉ lưu trữ</span><p className="mt-1.5 text-[10px] text-fg-subtle">Không check, không mở browser, không đồng bộ.</p></div> : <>
                        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] text-fg-muted">
                          <span>Video <strong className="text-fg">{formatMetric(acc.collected_video_count || acc.video_count)}</strong></span>
                          <span>View <strong className="text-fg">{formatMetric(acc.total_views)}</strong></span>
                          <span>Follower <strong className="text-fg">{formatMetric(acc.follower_count)}</strong></span>
                          <span>Like video <strong className="text-fg">{formatMetric(acc.total_video_likes)}</strong></span>
                          <span>Comment <strong className="text-fg">{formatMetric(acc.total_comments)}</strong></span>
                          <span>Share <strong className="text-fg">{formatMetric(acc.total_shares)}</strong></span>
                        </div>
                        <p className={`mt-1.5 text-[10px] ${acc.analytics_sync_status === 'FAILED' ? 'text-rose-300' : acc.analytics_sync_status === 'PARTIAL' ? 'text-amber-300' : 'text-fg-subtle'}`}>{acc.analytics_sync_status === 'FAILED' ? 'Đồng bộ lỗi' : acc.analytics_sync_status === 'PARTIAL' ? 'Dữ liệu một phần' : acc.metrics_updated_at ? `Đồng bộ ${formatDateTime(acc.metrics_updated_at)}` : 'Chưa đồng bộ TikTok'}</p>
                        <button type="button" onClick={() => setAnalyticsAccount(acc)} className="mt-2 inline-flex min-h-8 items-center gap-1 rounded-md border border-brand/25 bg-brand/10 px-2 text-[10px] font-bold text-brand hover:bg-brand/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"><BarChart3 className="h-3 w-3" /> Chi tiết</button>
                      </>}
                    </td>

                    <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                      <select
                        value={acc.proxy_id || 'none'}
                        disabled={acc.is_sold}
                        onChange={(e) => handleBindProxy(acc.id, e.target.value)}
                        title={acc.is_sold ? 'Account ĐÃ BÁN chỉ lưu trữ, không đổi proxy' : 'Đổi proxy cho account'}
                        className="bg-surface-2 border border-line rounded-lg p-1.5 text-xs text-brand font-medium focus:outline-none focus:ring-1 focus:ring-teal-400 disabled:cursor-not-allowed disabled:opacity-45"
                      >
                        <option value="none">Mạng LAN (Không Proxy)</option>
                        {proxies.map((p) => (
                          <option key={p.id} value={p.id}>
                            [{p.protocol.toUpperCase()}] {p.host}:{p.port}
                          </option>
                        ))}
                      </select>
                    </td>

                    {/* GHI CHÚ tự do - nhấp đúp để sửa (được phép để trống) */}
                    <td className="px-3 py-2 text-fg max-w-[160px]" onClick={(e) => e.stopPropagation()}>
                      {renderEditable(
                        acc,
                        'note',
                        acc.note
                          ? <span className="text-amber-200/90">{acc.note}</span>
                          : <span className="text-fg-subtle italic">＋ ghi chú</span>
                      )}
                    </td>

                    {/* NÚT TẠM DỪNG / TIẾP TỤC RIÊNG + RUN ONE TEST (thao tác tay) */}
                    <td className="px-3 py-2 text-center" onClick={(e) => e.stopPropagation()}>
                      <div className="flex flex-col items-center gap-1.5">
                        {!acc.is_sold && (acc.status === 'RUNNING' || acc.is_paused) ? (
                          acc.is_paused ? (
                            <button
                              onClick={() => onResumeAccount(acc.id)}
                              className="inline-flex items-center gap-1 bg-brand/10 hover:bg-brand/20 border border-brand/30 text-brand text-[10px] font-bold px-2.5 py-1 rounded-md transition-all animate-pulse"
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
                        ) : null}

                        {/* RUN ONE TEST: mở trình duyệt HIỆN lên, login rồi giữ để thao tác tay. */}
                        {manualSessionIds.includes(acc.id) ? (
                          <button
                            onClick={() => onStopRunOneTest(acc.id)}
                            className="inline-flex items-center gap-1 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 text-rose-400 text-[10px] font-bold px-2.5 py-1 rounded-md transition-all animate-pulse"
                            title="Đóng phiên đang mở"
                          >
                            <StopSquare className="w-3 h-3" /> Đóng phiên
                          </button>
                        ) : (
                          <button
                            onClick={() => onRunOneTest(acc.id)}
                            disabled={acc.is_sold}
                            className="btn btn-sm bg-violet-500/10 text-violet-300 border border-violet-500/25 hover:bg-violet-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                            title={acc.is_sold ? 'Account ĐÃ BÁN được khóa mọi thao tác browser' : 'Mở phiên tay trong trình duyệt HIỆN để bạn thao tác tới khi đóng'}
                          >
                            <FlaskConical className="w-3 h-3" /> {acc.is_sold ? 'Đã khóa' : 'Run one test'}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
    <AccountAnalyticsModal account={analyticsAccount} onClose={() => setAnalyticsAccount(null)} onSync={onSyncAnalytics} />
    </>
  );
};
