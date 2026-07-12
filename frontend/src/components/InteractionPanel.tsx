// File: frontend/src/components/InteractionPanel.tsx
import React, { useEffect, useState } from 'react';
import { FileText, Play, CalendarClock, Pause, RotateCcw, Trash2, Heart, MessageCircle, Hash, Sparkles } from 'lucide-react';
import { Account } from '../types';

interface InteractionPanelProps {
  accounts: Account[];
  selectedAccountIds: string[];
}

interface ScheduleItem {
  schedule_id: string;
  account_ids: string[];
  mode: string;
  hashtag: string | null;
  duration_minutes: number;
  interval_minutes: number;
  like_probability: number;
  comment_probability: number;
  is_active: boolean;
  created_at: string;
}

const API_BASE = 'http://127.0.0.1:8001/api/v1/interactions';

export const InteractionPanel: React.FC<InteractionPanelProps> = ({ accounts, selectedAccountIds }) => {
  const [mode, setMode] = useState<'foryou' | 'hashtag'>('foryou');
  const [hashtag, setHashtag] = useState<string>('');
  const [durationMinutes, setDurationMinutes] = useState<number>(10);
  const [intervalMinutes, setIntervalMinutes] = useState<number>(60);
  const [likeProbability, setLikeProbability] = useState<number>(40);
  const [commentProbability, setCommentProbability] = useState<number>(5);
  const [commentFilePath, setCommentFilePath] = useState<string>('');
  const [commentCount, setCommentCount] = useState<number>(0);
  const [concurrencyLimit, setConcurrencyLimit] = useState<number>(4);
  const [schedules, setSchedules] = useState<ScheduleItem[]>([]);
  const [busy, setBusy] = useState<boolean>(false);

  const selectedAccounts = accounts.filter((a) => selectedAccountIds.includes(a.id));

  const loadSchedules = async () => {
    try {
      const res = await fetch(`${API_BASE}/schedules`);
      if (res.ok) setSchedules(await res.json());
    } catch (err) {
      console.error('Lỗi tải danh sách lịch:', err);
    }
  };

  useEffect(() => {
    loadSchedules();
    const interval = setInterval(loadSchedules, 10000); // Tự làm mới mỗi 10s
    return () => clearInterval(interval);
  }, []);

  const handlePickCommentFile = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/select-comment-file`, { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.status === 'SUCCESS') {
        setCommentFilePath(data.path);
        setCommentCount(data.comment_count);
      } else if (data.status !== 'CANCELLED') {
        alert(data.detail || 'Không thể chọn file. Vui lòng dán đường dẫn thủ công.');
      }
    } catch (err) {
      alert('Không thể kết nối bộ chọn file của OS.');
    } finally {
      setBusy(false);
    }
  };

  const buildPayload = () => ({
    account_ids: selectedAccountIds,
    mode,
    hashtag: mode === 'hashtag' ? hashtag : null,
    duration_minutes: durationMinutes,
    like_probability: likeProbability / 100,
    comment_probability: commentProbability / 100,
    comment_file_path: commentFilePath || null,
    concurrency_limit: concurrencyLimit,
  });

  const validate = (): string | null => {
    if (selectedAccountIds.length === 0) {
      return 'Vui lòng chọn ít nhất 1 tài khoản ở tab "Quản lý Tài Khoản" trước.';
    }
    if (mode === 'hashtag' && !hashtag.trim()) {
      return 'Vui lòng nhập từ khóa hashtag.';
    }
    return null;
  };

  const handleRunOnce = async () => {
    const err = validate();
    if (err) return alert(err);

    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/run-once`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
      });
      const data = await res.json();
      if (res.ok) {
        alert(data.message);
      } else {
        alert(data.detail || 'Có lỗi xảy ra.');
      }
    } catch (e) {
      alert('Không thể kết nối tới backend.');
    } finally {
      setBusy(false);
    }
  };

  const handleCreateSchedule = async () => {
    const err = validate();
    if (err) return alert(err);
    if (intervalMinutes <= durationMinutes) {
      if (!window.confirm(
        `Chu kỳ lặp (${intervalMinutes} phút) nhỏ hơn hoặc bằng thời gian chạy (${durationMinutes} phút). ` +
        `Các vòng có thể bị chồng lấn (hệ thống sẽ tự bỏ qua account đang bận). Vẫn tiếp tục?`
      )) return;
    }

    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/schedule`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...buildPayload(), interval_minutes: intervalMinutes }),
      });
      const data = await res.json();
      if (res.ok) {
        alert(data.message);
        loadSchedules();
      } else {
        alert(data.detail || 'Có lỗi xảy ra.');
      }
    } catch (e) {
      alert('Không thể kết nối tới backend.');
    } finally {
      setBusy(false);
    }
  };

  const callScheduleAction = async (scheduleId: string, action: 'pause' | 'resume' | 'delete') => {
    try {
      const method = action === 'delete' ? 'DELETE' : 'POST';
      const url = action === 'delete' ? `${API_BASE}/schedule/${scheduleId}` : `${API_BASE}/schedule/${scheduleId}/${action}`;
      const res = await fetch(url, { method });
      if (res.ok) {
        loadSchedules();
      } else {
        const data = await res.json();
        alert(data.detail || 'Có lỗi xảy ra.');
      }
    } catch (e) {
      alert('Không thể kết nối tới backend.');
    }
  };

  return (
    <div className="flex flex-col gap-6">
      {/* CẤU HÌNH CHIẾN DỊCH TƯƠNG TÁC */}
      <div className="bg-[#0e1424] p-5 rounded-2xl border border-slate-800 flex flex-col gap-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <h3 className="font-bold text-sm text-slate-200 flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-pink-400" /> Cấu Hình Tương Tác Video
          </h3>
          <span className="text-[11px] text-slate-400 font-semibold">
            Đã chọn: <span className="text-teal-400 font-bold">{selectedAccounts.length}</span> tài khoản
            {selectedAccounts.length === 0 && (
              <span className="text-amber-400 ml-2">(chọn ở tab "Quản lý Tài Khoản" trước)</span>
            )}
          </span>
        </div>

        {/* NGUỒN VIDEO */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-slate-400 font-semibold block mb-1.5">Nguồn video:</label>
            <div className="flex gap-2">
              <button
                onClick={() => setMode('foryou')}
                className={`flex-1 py-2 rounded-lg text-xs font-bold border transition-all ${
                  mode === 'foryou' ? 'bg-teal-500/20 border-teal-500/40 text-teal-400' : 'bg-slate-900 border-slate-800 text-slate-400'
                }`}
              >
                Trang For You
              </button>
              <button
                onClick={() => setMode('hashtag')}
                className={`flex-1 py-2 rounded-lg text-xs font-bold border transition-all ${
                  mode === 'hashtag' ? 'bg-teal-500/20 border-teal-500/40 text-teal-400' : 'bg-slate-900 border-slate-800 text-slate-400'
                }`}
              >
                Theo Hashtag
              </button>
            </div>
          </div>

          {mode === 'hashtag' && (
            <div>
              <label className="text-xs text-slate-400 font-semibold block mb-1.5">Từ khóa hashtag (không cần #):</label>
              <div className="flex items-center gap-2 bg-[#182032] border border-slate-700 rounded-xl px-3">
                <Hash className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                <input
                  type="text"
                  value={hashtag}
                  onChange={(e) => setHashtag(e.target.value)}
                  placeholder="vd: dance, funnyvideo..."
                  className="w-full bg-transparent p-2.5 text-sm focus:outline-none text-slate-100"
                />
              </div>
            </div>
          )}
        </div>

        {/* THỜI GIAN CHẠY / CHU KỲ */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-slate-400 font-semibold block mb-1.5">
              Chạy mỗi phiên trong (phút): <span className="text-teal-400 font-bold">{durationMinutes}</span>
            </label>
            <input
              type="range" min={1} max={120} value={durationMinutes}
              onChange={(e) => setDurationMinutes(parseInt(e.target.value))}
              className="w-full accent-teal-400"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400 font-semibold block mb-1.5">
              Lặp lại mỗi (phút, chỉ áp dụng khi Tạo lịch): <span className="text-indigo-400 font-bold">{intervalMinutes}</span>
            </label>
            <input
              type="range" min={5} max={720} step={5} value={intervalMinutes}
              onChange={(e) => setIntervalMinutes(parseInt(e.target.value))}
              className="w-full accent-indigo-400"
            />
          </div>
        </div>

        {/* XÁC SUẤT TYM / CMT */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-slate-400 font-semibold flex items-center gap-1.5 mb-1.5">
              <Heart className="w-3.5 h-3.5 text-rose-400" /> Xác suất Tym mỗi video: <span className="text-rose-400 font-bold">{likeProbability}%</span>
            </label>
            <input
              type="range" min={0} max={100} value={likeProbability}
              onChange={(e) => setLikeProbability(parseInt(e.target.value))}
              className="w-full accent-rose-400"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400 font-semibold flex items-center gap-1.5 mb-1.5">
              <MessageCircle className="w-3.5 h-3.5 text-sky-400" /> Xác suất Bình luận mỗi video: <span className="text-sky-400 font-bold">{commentProbability}%</span>
            </label>
            <input
              type="range" min={0} max={100} value={commentProbability}
              onChange={(e) => setCommentProbability(parseInt(e.target.value))}
              className="w-full accent-sky-400"
            />
          </div>
        </div>

        {/* FILE CÂU BÌNH LUẬN */}
        <div>
          <label className="text-xs text-slate-400 font-semibold block mb-1.5">
            File danh sách câu bình luận (.txt, mỗi dòng 1 câu):
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={commentFilePath}
              onChange={(e) => setCommentFilePath(e.target.value)}
              placeholder="Dán đường dẫn file .txt hoặc bấm Chọn file"
              className="flex-1 bg-[#182032] border border-slate-700 rounded-xl p-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-teal-400 text-slate-100"
            />
            <button
              onClick={handlePickCommentFile}
              disabled={busy}
              className="bg-teal-500 hover:bg-teal-600 disabled:bg-slate-800 text-slate-950 font-bold text-xs px-4 rounded-xl flex items-center gap-1.5 shrink-0"
            >
              <FileText className="w-4 h-4" /> Chọn file
            </button>
          </div>
          {commentCount > 0 && (
            <p className="text-[10px] text-teal-400 mt-1 font-semibold">Đã nạp {commentCount} câu bình luận.</p>
          )}
        </div>

        {/* SỐ LUỒNG SONG SONG */}
        <div>
          <label className="text-xs text-slate-400 font-semibold block mb-1.5">Số luồng chạy song song (chỉ áp dụng cho "Chạy 1 lần"):</label>
          <input
            type="number" min={1} max={20} value={concurrencyLimit}
            onChange={(e) => setConcurrencyLimit(parseInt(e.target.value) || 4)}
            className="w-32 bg-[#182032] border border-slate-700 rounded-xl p-2 text-sm text-center font-bold text-teal-400 focus:outline-none"
          />
        </div>

        {/* NÚT HÀNH ĐỘNG */}
        <div className="flex gap-3 pt-2 border-t border-slate-800">
          <button
            onClick={handleRunOnce}
            disabled={busy}
            className="flex-1 flex items-center justify-center gap-2 bg-teal-500 hover:bg-teal-600 disabled:bg-slate-800 text-slate-950 font-bold text-sm py-2.5 rounded-xl transition-all"
          >
            <Play className="w-4 h-4" /> Chạy 1 lần ngay
          </button>
          <button
            onClick={handleCreateSchedule}
            disabled={busy}
            className="flex-1 flex items-center justify-center gap-2 bg-indigo-500 hover:bg-indigo-600 disabled:bg-slate-800 text-white font-bold text-sm py-2.5 rounded-xl transition-all"
          >
            <CalendarClock className="w-4 h-4" /> Tạo lịch lặp chu kỳ
          </button>
        </div>
      </div>

      {/* DANH SÁCH LỊCH ĐANG HOẠT ĐỘNG */}
      <div className="bg-[#0e1424] p-5 rounded-2xl border border-slate-800">
        <h3 className="font-bold text-sm text-slate-200 mb-3 flex items-center gap-2">
          <CalendarClock className="w-4 h-4 text-indigo-400" /> Các Lịch Đang Hoạt Động ({schedules.length})
        </h3>

        {schedules.length === 0 ? (
          <p className="text-xs text-slate-500 text-center py-6">Chưa có lịch tương tác nào được tạo.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {schedules.map((s) => (
              <div key={s.schedule_id} className="bg-[#141b2e] border border-slate-800 rounded-xl p-3 flex items-center justify-between gap-3 flex-wrap">
                <div className="text-xs text-slate-300 flex flex-col gap-0.5">
                  <span className="font-bold text-slate-100">
                    {s.mode === 'hashtag' ? `#${s.hashtag}` : 'For You'} — {s.account_ids.length} tài khoản
                  </span>
                  <span className="text-slate-500">
                    Chạy {s.duration_minutes} phút / mỗi {s.interval_minutes} phút · Tym {(s.like_probability * 100).toFixed(0)}% · Cmt {(s.comment_probability * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-[10px] font-bold px-2 py-1 rounded-md ${s.is_active ? 'bg-teal-500/10 text-teal-400 border border-teal-500/30' : 'bg-amber-500/10 text-amber-400 border border-amber-500/30'}`}>
                    {s.is_active ? '● ĐANG CHẠY' : '⏸ TẠM DỪNG'}
                  </span>
                  {s.is_active ? (
                    <button
                      onClick={() => callScheduleAction(s.schedule_id, 'pause')}
                      className="p-1.5 rounded-md bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 text-amber-400"
                      title="Tạm dừng lịch"
                    >
                      <Pause className="w-3.5 h-3.5" />
                    </button>
                  ) : (
                    <button
                      onClick={() => callScheduleAction(s.schedule_id, 'resume')}
                      className="p-1.5 rounded-md bg-teal-500/10 hover:bg-teal-500/20 border border-teal-500/30 text-teal-400"
                      title="Tiếp tục lịch"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                    </button>
                  )}
                  <button
                    onClick={() => {
                      if (window.confirm('Xoá lịch này? Không thể hoàn tác.')) callScheduleAction(s.schedule_id, 'delete');
                    }}
                    className="p-1.5 rounded-md bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 text-rose-400"
                    title="Xoá lịch"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
