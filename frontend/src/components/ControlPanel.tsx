// File: frontend/src/components/ControlPanel.tsx
import React, { useState, useEffect } from 'react';
import { FolderOpen, Play, Pause, Square, RotateCcw, RadioTower, Gauge, Image } from 'lucide-react';
import { Account } from '../types';

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
  // NÂNG CẤP: Check nhanh liên tục giờ chạy trên đúng acc đang được CHỌN
  // trên bảng, không còn quét toàn bộ DB nữa.
  accounts: Account[];
  selectedAccountIds: string[];
}

interface ContinuousCheckStatus {
  is_active: boolean;
  gap_seconds: number;
  concurrency_limit: number;
  cycle_count: number;
  last_cycle_at: string | null;
  is_running_now: boolean;
}

const TASKS_API = 'http://127.0.0.1:9000/api/v1/tasks';

export const ControlPanel: React.FC<ControlPanelProps> = ({
  concurrency,
  setConcurrency,
  avatarFolder,
  setAvatarFolder,
  isGloballyPaused,
  onGlobalStart,
  onGlobalPause,
  onGlobalResume,
  onGlobalStop,
  accounts,
  selectedAccountIds,
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
    if (selectedAccountIds.length === 0) {
      alert('Vui lòng chọn ít nhất 1 tài khoản ở bảng bên dưới trước khi bật Check nhanh liên tục.');
      return;
    }
    try {
      const res = await fetch(`${TASKS_API}/quick-health-check/start-continuous`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_ids: selectedAccountIds,
          gap_seconds: continuousGapSeconds,
          concurrency_limit: continuousConcurrency,
        }),
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
      const res = await fetch('http://127.0.0.1:9000/api/v1/accounts/select-local-folder', {
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

  const secLabel = "text-[10px] text-fg-subtle font-bold uppercase tracking-wider";

  return (
    <div className="flex flex-col gap-3">
      {/* THANH ĐIỀU KHIỂN TOÀN CỤC */}
      <div className="card px-3.5 py-3 flex items-center gap-2 flex-wrap">
        <span className={`${secLabel} pr-1`}>Điều khiển toàn cục</span>

        <button onClick={onGlobalStart} className="btn btn-sm bg-emerald-500/10 text-emerald-400 border border-emerald-500/25 hover:bg-emerald-500/20"
          title="Khởi động (hoặc khởi động lại) hệ thống điều phối tác vụ">
          <Play className="w-3.5 h-3.5" /> Bắt đầu
        </button>

        {isGloballyPaused ? (
          <button onClick={onGlobalResume} className="btn btn-sm bg-brand/10 text-brand border border-brand/30 hover:bg-brand/20 animate-pulse-soft"
            title="Tiếp tục tất cả các luồng đang bị tạm dừng">
            <RotateCcw className="w-3.5 h-3.5" /> Tiếp tục
          </button>
        ) : (
          <button onClick={onGlobalPause} className="btn btn-sm bg-amber-500/10 text-amber-400 border border-amber-500/25 hover:bg-amber-500/20"
            title="Tạm dừng tất cả các luồng đang chạy tại checkpoint gần nhất">
            <Pause className="w-3.5 h-3.5" /> Tạm dừng
          </button>
        )}

        <button onClick={handleStop} className="btn btn-sm btn-danger"
          title="Hủy ngay lập tức toàn bộ luồng đang chạy và xóa hàng đợi">
          <Square className="w-3.5 h-3.5" /> Dừng khẩn cấp
        </button>

        {isGloballyPaused && (
          <span className="badge bg-amber-500/10 text-amber-400 border border-amber-500/30 ml-auto animate-pulse-soft">
            <Pause className="w-3 h-3" /> Hệ thống đang tạm dừng
          </span>
        )}
      </div>

      {/* CẤU HÌNH SONG SONG + AVATAR FOLDER */}
      <div className="card p-4 grid grid-cols-1 md:grid-cols-3 gap-4 items-start">
        <div>
          <label className="text-xs text-fg-muted mb-1.5 font-semibold flex items-center gap-1.5">
            <Gauge className="w-3.5 h-3.5 text-brand" /> Luồng tối đa / 1 proxy
          </label>
          <input
            type="number" min={1} max={10} value={concurrency}
            onChange={(e) => setConcurrency(parseInt(e.target.value) || 1)}
            className="field w-full text-center font-bold text-brand"
          />
          <p className="text-[10px] text-fg-subtle mt-1.5 leading-snug">
            Mỗi proxy chỉ chạy tối đa bấy nhiêu account cùng lúc. Account thứ N+1 trên cùng proxy sẽ chờ tới lượt.
          </p>
        </div>

        <div className="md:col-span-2">
          <label className="text-xs text-fg-muted mb-1.5 font-semibold flex items-center gap-1.5">
            <Image className="w-3.5 h-3.5 text-brand" /> Thư mục ảnh đại diện (Avatar)
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="Ví dụ: D:\images\avatars — hoặc dán đường dẫn thủ công"
              value={avatarFolder}
              onChange={(e) => setAvatarFolder(e.target.value)}
              className="field flex-1"
            />
            <button onClick={handleBrowseFolder} disabled={loading} className="btn btn-primary shrink-0"
              title="Mở thư mục hệ thống để chọn trực quan">
              <FolderOpen className="w-4 h-4" />
              <span>{loading ? 'Đang chọn...' : 'Chọn'}</span>
            </button>
          </div>
        </div>
      </div>

      {/* CHECK NHANH LIÊN TỤC */}
      <div className="card p-4 flex flex-col gap-3">
        <div className="flex items-center justify-between border-b border-line-soft pb-2.5">
          <span className={`${secLabel} flex items-center gap-1.5`}>
            <RadioTower className="w-3.5 h-3.5 text-sky-400" /> Check nhanh liên tục
          </span>
          {continuousStatus?.is_active && (
            <span className="badge bg-sky-500/10 text-sky-400 border border-sky-500/30 animate-pulse-soft normal-case tracking-normal">
              ● Đang bật — {continuousStatus.cycle_count} chu kỳ
              {continuousStatus.is_running_now ? ' · đang quét' : ''}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-fg-muted font-semibold">Nghỉ giữa 2 vòng (s)</label>
            <input
              type="number" min={0} max={60} value={continuousGapSeconds}
              onChange={(e) => setContinuousGapSeconds(parseInt(e.target.value) || 0)}
              disabled={!!continuousStatus?.is_active}
              className="field w-16 py-1.5 text-center font-bold text-sky-400 disabled:opacity-50"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-fg-muted font-semibold">Luồng song song</label>
            <input
              type="number" min={1} max={50} value={continuousConcurrency}
              onChange={(e) => setContinuousConcurrency(parseInt(e.target.value) || 15)}
              disabled={!!continuousStatus?.is_active}
              className="field w-16 py-1.5 text-center font-bold text-sky-400 disabled:opacity-50"
            />
          </div>

          {continuousStatus?.is_active ? (
            <button onClick={handleStopContinuous} className="btn btn-sm btn-danger">
              <Square className="w-3.5 h-3.5" /> Tắt liên tục
            </button>
          ) : (
            <button onClick={handleStartContinuous} disabled={selectedAccountIds.length === 0}
              title={selectedAccountIds.length === 0 ? 'Chọn ít nhất 1 tài khoản ở bảng bên dưới trước' : undefined}
              className="btn btn-sm bg-sky-500/10 text-sky-400 border border-sky-500/30 hover:bg-sky-500/20">
              <Play className="w-3.5 h-3.5" /> Bật liên tục ({selectedAccountIds.length})
            </button>
          )}

          {continuousStatus?.last_cycle_at && (
            <span className="text-[10px] text-fg-subtle ml-auto">
              Chu kỳ gần nhất: {new Date(continuousStatus.last_cycle_at).toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
