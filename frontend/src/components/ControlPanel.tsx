// File: frontend/src/components/ControlPanel.tsx
import React, { useState, useEffect } from 'react';
import { FolderOpen, Play, Pause, Square, RotateCcw, RadioTower } from 'lucide-react';

interface ControlPanelProps {
  concurrency: number;
  setConcurrency: (val: number) => void;
  avatarFolder: string;
  setAvatarFolder: (val: string) => void;
  isGloballyPaused: boolean;
  onGlobalStart: () => void;
  onGlobalPause: () => void;
  onGlobalResume: () => void;
  onGlobalStop: () => void;
}

interface ContinuousCheckStatus {
  is_active: boolean;
  gap_seconds: number;
  concurrency_limit: number;
  cycle_count: number;
  last_cycle_at: string | null;
  is_running_now: boolean;
}

const TASKS_API = 'http://127.0.0.1:8001/api/v1/tasks';

export const ControlPanel: React.FC<ControlPanelProps> = ({
  concurrency,
  setConcurrency,
  avatarFolder,
  setAvatarFolder,
  isGloballyPaused,
  onGlobalStart,
  onGlobalPause,
  onGlobalResume,
  onGlobalStop
}) => {
  const [loading, setLoading] = useState<boolean>(false);

  // =========================================================================
  // CHẾ ĐỘ CHECK NHANH LIÊN TỤC - hoàn toàn độc lập với dispatcher chính,
  // tự quản lý state/polling riêng bên trong widget này.
  // =========================================================================
  const [continuousStatus, setContinuousStatus] = useState<ContinuousCheckStatus | null>(null);
  const [continuousGapSeconds, setContinuousGapSeconds] = useState<number>(3);
  const [continuousConcurrency, setContinuousConcurrency] = useState<number>(15);

  const loadContinuousStatus = async () => {
    try {
      const res = await fetch(`${TASKS_API}/quick-health-check/continuous-status`);
      if (res.ok) setContinuousStatus(await res.json());
    } catch (err) {
      console.error('Lỗi tải trạng thái Check nhanh liên tục:', err);
    }
  };

  useEffect(() => {
    loadContinuousStatus();
    const interval = setInterval(loadContinuousStatus, 8000);
    return () => clearInterval(interval);
  }, []);

  const handleStartContinuous = async () => {
    try {
      const res = await fetch(`${TASKS_API}/quick-health-check/start-continuous`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gap_seconds: continuousGapSeconds, concurrency_limit: continuousConcurrency }),
      });
      const data = await res.json();
      if (!res.ok) alert(data.detail || 'Có lỗi xảy ra.');
      loadContinuousStatus();
    } catch (err) {
      alert('Không thể kết nối tới backend.');
    }
  };

  const handleStopContinuous = async () => {
    try {
      const res = await fetch(`${TASKS_API}/quick-health-check/stop-continuous`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) alert(data.detail || 'Có lỗi xảy ra.');
      loadContinuousStatus();
    } catch (err) {
      alert('Không thể kết nối tới backend.');
    }
  };

  // GỌI CẦU NỐI API ĐỂ BẬT WINDOWS FOLDER PICKER
  const handleBrowseFolder = async () => {
    setLoading(true);
    try {
      const res = await fetch('http://127.0.0.1:8001/api/v1/accounts/select-local-folder', {
        method: 'POST'
      });
      if (res.ok) {
        const data = await res.json();
        if (data.status === 'SUCCESS' && data.path) {
          setAvatarFolder(data.path); // Tự động điền đường dẫn thật vào ô nhập liệu
        }
      } else {
        const err = await res.json();
        alert(err.detail || "Không thể chọn tự động. Vui lòng nhập tay.");
      }
    } catch (err) {
      alert("Không thể kết nối bộ chọn thư mục của OS. Vui lòng dán trực tiếp đường dẫn vào ô nhập liệu.");
    } finally {
      setLoading(false);
    }
  };

  const handleStop = () => {
    if (!window.confirm("DỪNG KHẨN CẤP sẽ hủy ngay lập tức TẤT CẢ các luồng đang chạy (đóng trình duyệt) và xóa hàng đợi đang chờ. Bạn có chắc chắn?")) {
      return;
    }
    onGlobalStop();
  };

  return (
    <div className="flex flex-col gap-3">
      {/* THANH ĐIỀU KHIỂN TOÀN CỤC: Bắt đầu / Tạm dừng / Tiếp tục / Dừng khẩn cấp */}
      <div className="bg-[#0e1424] p-3 rounded-2xl border border-slate-800 flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider pr-1">Điều khiển toàn cục:</span>

        <button
          onClick={onGlobalStart}
          className="flex items-center gap-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 text-[11px] font-bold px-3 py-1.5 rounded-lg transition-all"
          title="Khởi động (hoặc khởi động lại) hệ thống điều phối tác vụ"
        >
          <Play className="w-3.5 h-3.5" /> Bắt đầu
        </button>

        {isGloballyPaused ? (
          <button
            onClick={onGlobalResume}
            className="flex items-center gap-1.5 bg-teal-500/10 hover:bg-teal-500/20 border border-teal-500/30 text-teal-400 text-[11px] font-bold px-3 py-1.5 rounded-lg transition-all animate-pulse"
            title="Tiếp tục tất cả các luồng đang bị tạm dừng"
          >
            <RotateCcw className="w-3.5 h-3.5" /> Tiếp tục toàn cục
          </button>
        ) : (
          <button
            onClick={onGlobalPause}
            className="flex items-center gap-1.5 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 text-amber-400 text-[11px] font-bold px-3 py-1.5 rounded-lg transition-all"
            title="Tạm dừng tất cả các luồng đang chạy tại checkpoint gần nhất"
          >
            <Pause className="w-3.5 h-3.5" /> Tạm dừng toàn cục
          </button>
        )}

        <button
          onClick={handleStop}
          className="flex items-center gap-1.5 bg-rose-600/10 hover:bg-rose-600/20 border border-rose-600/30 text-rose-400 text-[11px] font-bold px-3 py-1.5 rounded-lg transition-all"
          title="Hủy ngay lập tức toàn bộ luồng đang chạy và xóa hàng đợi"
        >
          <Square className="w-3.5 h-3.5" /> Dừng khẩn cấp
        </button>

        {isGloballyPaused && (
          <span className="text-[10px] text-amber-400 font-bold bg-amber-500/10 border border-amber-500/30 px-2 py-1 rounded-md ml-auto animate-pulse">
            ⏸ HỆ THỐNG ĐANG TẠM DỪNG
          </span>
        )}
      </div>

      {/* CẤU HÌNH SONG SONG + AVATAR FOLDER */}
      <div className="bg-[#0e1424] p-4 rounded-2xl border border-slate-800 grid grid-cols-1 md:grid-cols-3 gap-4 items-center">
        <div>
          <label className="text-xs text-slate-400 block mb-1 font-semibold">Cấu hình số luồng chạy song song (Threads):</label>
          <input
            type="number"
            min={1}
            max={20}
            value={concurrency}
            onChange={(e) => setConcurrency(parseInt(e.target.value) || 4)}
            className="w-full bg-[#182032] border border-slate-700 rounded-xl p-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-teal-400 font-bold text-teal-400 text-center"
          />
        </div>

        <div className="md:col-span-2">
          <label className="text-xs text-slate-400 block mb-1 font-semibold">
            Đường dẫn thư mục chứa ảnh đại diện (Avatar Folder):
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="Ví dụ: D:\images\avatars hoặc dán đường dẫn thủ công"
              value={avatarFolder}
              onChange={(e) => setAvatarFolder(e.target.value)}
              className="flex-1 bg-[#182032] border border-slate-700 rounded-xl p-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-teal-400 text-slate-100 font-medium"
            />
            <button
              onClick={handleBrowseFolder}
              disabled={loading}
              className="bg-teal-500 hover:bg-teal-600 disabled:bg-slate-800 text-slate-950 font-bold text-xs px-4 rounded-xl flex items-center gap-1.5 transition-all shadow-md shadow-teal-500/10 cursor-pointer h-10 shrink-0"
              title="Mở thư mục hệ thống để chọn trực quan"
            >
              <FolderOpen className="w-4 h-4" />
              <span>{loading ? 'Đang chọn...' : 'Chọn thư mục'}</span>
            </button>
          </div>
        </div>
      </div>

      {/* CHECK NHANH LIÊN TỤC - Quét lặp lại toàn bộ account đang ALIVE theo
          chu kỳ, hoàn toàn tách biệt (Chromium riêng, semaphore riêng, không
          đụng gì tới hàng đợi/luồng đăng nhập chính). */}
      <div className="bg-[#0e1424] p-4 rounded-2xl border border-slate-800 flex flex-col gap-3">
        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
          <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider flex items-center gap-1.5">
            <RadioTower className="w-3.5 h-3.5 text-sky-400" /> Check Nhanh Liên Tục (toàn bộ acc đang SỐNG)
          </span>
          {continuousStatus?.is_active && (
            <span className="text-[10px] text-sky-400 font-bold bg-sky-500/10 border border-sky-500/30 px-2 py-1 rounded-md animate-pulse">
              ● ĐANG BẬT — đã chạy {continuousStatus.cycle_count} chu kỳ
              {continuousStatus.is_running_now ? ' (đang quét...)' : ''}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1.5">
            <label className="text-[10px] text-slate-400 font-semibold">Nghỉ giữa 2 vòng (giây):</label>
            <input
              type="number" min={0} max={60}
              value={continuousGapSeconds}
              onChange={(e) => setContinuousGapSeconds(parseInt(e.target.value) || 0)}
              disabled={!!continuousStatus?.is_active}
              className="w-16 bg-[#182032] border border-slate-700 rounded-lg p-1.5 text-xs text-center font-bold text-sky-400 disabled:opacity-50 focus:outline-none"
            />
          </div>
          <div className="flex items-center gap-1.5">
            <label className="text-[10px] text-slate-400 font-semibold">Luồng song song:</label>
            <input
              type="number" min={1} max={50}
              value={continuousConcurrency}
              onChange={(e) => setContinuousConcurrency(parseInt(e.target.value) || 15)}
              disabled={!!continuousStatus?.is_active}
              className="w-16 bg-[#182032] border border-slate-700 rounded-lg p-1.5 text-xs text-center font-bold text-sky-400 disabled:opacity-50 focus:outline-none"
            />
          </div>

          {continuousStatus?.is_active ? (
            <button
              onClick={handleStopContinuous}
              className="flex items-center gap-1.5 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 text-rose-400 text-[11px] font-bold px-3 py-1.5 rounded-lg transition-all"
            >
              <Square className="w-3.5 h-3.5" /> Tắt liên tục
            </button>
          ) : (
            <button
              onClick={handleStartContinuous}
              className="flex items-center gap-1.5 bg-sky-500/10 hover:bg-sky-500/20 border border-sky-500/30 text-sky-400 text-[11px] font-bold px-3 py-1.5 rounded-lg transition-all"
            >
              <Play className="w-3.5 h-3.5" /> Bật liên tục
            </button>
          )}

          {continuousStatus?.last_cycle_at && (
            <span className="text-[10px] text-slate-500 ml-auto">
              Chu kỳ gần nhất: {new Date(continuousStatus.last_cycle_at).toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
