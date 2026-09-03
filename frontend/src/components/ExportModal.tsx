// File: frontend/src/components/ExportModal.tsx
import React, { useState } from 'react';
import { Download, X, Package, AlertTriangle, CheckCircle2 } from 'lucide-react';

interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedAccountIds: string[];
  displayedAccountIds: string[]; // toan bo acc trong lo dang hien thi
  onExported: () => void;
}

const API = 'http://127.0.0.1:9000/api/v1/accounts/export';

interface ExportResult {
  exported_count: number;
  available_before: number;
  remaining_count: number;
  requested: number | null;
  took_all_available: boolean;
  filename: string;
}

export const ExportModal: React.FC<ExportModalProps> = ({
  isOpen, onClose, selectedAccountIds, displayedAccountIds, onExported,
}) => {
  const [quantity, setQuantity] = useState<number>(10);
  const [busy, setBusy] = useState<boolean>(false);
  const [result, setResult] = useState<ExportResult | null>(null);

  if (!isOpen) return null;

  const doExport = async (accountIds: string[], qty: number | null) => {
    if (accountIds.length === 0) {
      alert('Không có tài khoản nào để xuất.');
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch(API, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_ids: accountIds, quantity: qty }),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.detail || 'Lỗi khi xuất tài khoản.');
        return;
      }
      // Tai file .txt ve may
      const blob = new Blob([data.content ?? ''], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = data.filename || 'export.txt';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      setResult(data);
      onExported(); // refresh bang (cac acc da bi xoa)
    } catch {
      alert('Không kết nối được tới backend.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-surface border border-line rounded-2xl w-full max-w-lg p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line-soft pb-3 mb-4">
          <h3 className="font-bold text-fg flex items-center gap-2">
            <Package className="w-5 h-5 text-brand" /> Xuất tài khoản ra file .txt
          </h3>
          <button onClick={onClose} className="text-fg-muted hover:text-fg">
            <X className="w-5 h-5" />
          </button>
        </div>

        <p className="text-[11px] text-amber-400/90 mb-4 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">
          ⚠️ Acc sau khi xuất sẽ bị <b>xóa khỏi app</b>. File xuất theo định dạng đầy đủ — cần lại thì <b>import file đó vào</b> là acc quay lại.
        </p>

        {/* Lua chon 1: acc da chon */}
        <div className="bg-surface-2 rounded-xl p-4 mb-3 border border-line-soft">
          <p className="text-sm font-bold text-fg mb-1">1. Xuất tài khoản đã chọn</p>
          <p className="text-xs text-fg-muted mb-3">
            Đang chọn: <span className="text-brand font-bold">{selectedAccountIds.length}</span> tài khoản
          </p>
          <button
            disabled={busy || selectedAccountIds.length === 0}
            onClick={() => doExport(selectedAccountIds, null)}
            className="w-full bg-teal-500 hover:bg-teal-600 disabled:bg-slate-700 disabled:text-fg-subtle text-slate-950 font-bold text-sm py-2 rounded-lg flex items-center justify-center gap-2 transition-all"
          >
            <Download className="w-4 h-4" /> Xuất {selectedAccountIds.length} acc đã chọn
          </button>
        </div>

        {/* Lua chon 2: lay N tu lo dang hien */}
        <div className="bg-surface-2 rounded-xl p-4 border border-line-soft">
          <p className="text-sm font-bold text-fg mb-1">2. Lấy số lượng từ lô đang hiển thị</p>
          <p className="text-xs text-fg-muted mb-3">
            Lô đang hiện có: <span className="text-indigo-400 font-bold">{displayedAccountIds.length}</span> tài khoản
          </p>
          <div className="flex gap-2">
            <input
              type="number"
              min={1}
              value={quantity}
              onChange={(e) => setQuantity(parseInt(e.target.value) || 1)}
              className="w-24 bg-surface border border-line rounded-lg p-2 text-center font-bold text-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            />
            <button
              disabled={busy || displayedAccountIds.length === 0}
              onClick={() => doExport(displayedAccountIds, quantity)}
              className="flex-1 bg-indigo-500 hover:bg-indigo-600 disabled:bg-slate-700 disabled:text-fg-subtle text-white font-bold text-sm py-2 rounded-lg flex items-center justify-center gap-2 transition-all"
            >
              <Download className="w-4 h-4" /> Lấy {quantity} acc
            </button>
          </div>
        </div>

        {/* Popup ket qua */}
        {result && (
          <div
            className={`mt-4 rounded-xl p-4 border ${
              result.took_all_available
                ? 'bg-amber-500/15 border-amber-500/60'
                : 'bg-brand/10 border-teal-500/40'
            }`}
          >
            {result.took_all_available && (
              <p className="text-amber-400 font-black text-base flex items-center gap-2 mb-2 animate-pulse">
                <AlertTriangle className="w-6 h-6" /> CHỈ CÓ {result.available_before} ACC — ĐÃ LẤY HẾT!
                <span className="text-xs font-normal">(bạn yêu cầu {result.requested})</span>
              </p>
            )}
            <p className="text-sm text-fg flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4 text-brand" />
              Đã xuất & tải về: <span className="font-bold text-brand text-lg">{result.exported_count}</span> tài khoản.
            </p>
            <p className="text-sm text-fg mt-1">
              Lô còn lại: <span className="font-bold text-fg">{result.remaining_count}</span> tài khoản.
            </p>
            <p className="text-[11px] text-fg-subtle mt-2">
              File: <span className="font-mono">{result.filename}</span> — các acc này đã bị xóa khỏi app.
            </p>
          </div>
        )}

        {busy && <p className="text-xs text-fg-muted mt-3 text-center animate-pulse">Đang xuất...</p>}
      </div>
    </div>
  );
};
