import React, { useEffect, useState } from 'react';
import { CheckCircle2, Clock, Film, ImagePlus, Info, LoaderCircle, Send, Upload, X } from 'lucide-react';

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedAccountIds: string[];
}

const API_NOW = 'http://127.0.0.1:9000/api/v1/tasks/bulk-upload-media';
const API_SCHEDULE = 'http://127.0.0.1:9000/api/v1/tasks/schedule-upload-media';

function defaultRunAt(): string {
  const d = new Date(Date.now() + 30 * 60 * 1000);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export const UploadModal: React.FC<UploadModalProps> = ({ isOpen, onClose, selectedAccountIds }) => {
  const [imagePath, setImagePath] = useState('');
  const [videoPath, setVideoPath] = useState('');
  const [caption, setCaption] = useState('');
  const [mode, setMode] = useState<'now' | 'schedule'>('now');
  const [runAt, setRunAt] = useState(defaultRunAt());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [busy, isOpen, onClose]);

  if (!isOpen) return null;

  const submit = async () => {
    setError(null);
    setMessage(null);
    if (selectedAccountIds.length === 0) {
      setError('Chưa chọn tài khoản để đăng.');
      return;
    }
    if (!imagePath.trim() && !videoPath.trim()) {
      setError('Nhập đường dẫn ảnh hoặc video dự phòng.');
      return;
    }
    if (mode === 'schedule' && !runAt) {
      setError('Chọn thời điểm đăng.');
      return;
    }

    setBusy(true);
    try {
      const payload = {
        account_ids: selectedAccountIds,
        image_path: imagePath.trim() || null,
        video_path: videoPath.trim() || null,
        caption,
        ...(mode === 'now' ? { schedule_at: null } : { run_at: runAt }),
      };
      const response = await fetch(mode === 'now' ? API_NOW : API_SCHEDULE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        setError(data.detail || 'Không thể tạo tác vụ đăng bài.');
        return;
      }
      setMessage(data.message || 'Đã tạo tác vụ đăng bài.');
    } catch {
      setError('Không kết nối được backend. Hãy kiểm tra dịch vụ rồi thử lại.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onMouseDown={() => !busy && onClose()}>
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="publish-title"
        className="w-full max-w-2xl overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between border-b border-line-soft px-5 py-4">
          <div>
            <h2 id="publish-title" className="flex items-center gap-2 text-lg font-bold text-fg">
              <Upload className="h-5 w-5 text-brand" aria-hidden="true" />
              Đăng nội dung TikTok
            </h2>
            <p className="mt-1 text-sm text-fg-muted">
              Áp dụng cho <span className="font-semibold text-brand">{selectedAccountIds.length}</span> tài khoản đã chọn.
            </p>
          </div>
          <button
            type="button"
            aria-label="Đóng"
            disabled={busy}
            onClick={onClose}
            className="grid h-11 w-11 place-items-center rounded-lg text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg disabled:opacity-50"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </header>

        <div className="max-h-[75vh] space-y-5 overflow-y-auto p-5">
          <div className="rounded-xl border border-brand/30 bg-brand/10 p-3 text-sm text-fg">
            <p className="font-semibold">Quy tắc chọn nội dung</p>
            <p className="mt-1 text-fg-muted">Có ảnh hợp lệ → đăng ảnh. Không có ảnh hợp lệ → dùng video dự phòng.</p>
          </div>

          <div>
            <label htmlFor="publish-images" className="flex items-center gap-2 text-sm font-bold text-fg">
              <ImagePlus className="h-4 w-4 text-brand" aria-hidden="true" />
              Ảnh ưu tiên
            </label>
            <input
              id="publish-images"
              value={imagePath}
              onChange={(event) => { setImagePath(event.target.value); setError(null); }}
              placeholder="D:\media\photos hoặc D:\media\photo.jpg"
              aria-describedby="publish-images-help"
              className="field mt-2 w-full font-mono text-xs"
            />
            <p id="publish-images-help" className="mt-1.5 text-xs leading-5 text-fg-muted">
              Nhập một ảnh hoặc thư mục; hệ thống lấy tối đa 35 ảnh JPG, JPEG, PNG, WEBP theo tên file.
            </p>
          </div>

          <div>
            <label htmlFor="publish-video" className="flex items-center gap-2 text-sm font-bold text-fg">
              <Film className="h-4 w-4 text-fg-subtle" aria-hidden="true" />
              Video dự phòng
            </label>
            <input
              id="publish-video"
              value={videoPath}
              onChange={(event) => { setVideoPath(event.target.value); setError(null); }}
              placeholder="D:\media\video.mp4"
              aria-describedby="publish-video-help"
              className="field mt-2 w-full font-mono text-xs"
            />
            <p id="publish-video-help" className="mt-1.5 text-xs text-fg-muted">Chỉ dùng khi ô ảnh trống hoặc không có ảnh hợp lệ.</p>
          </div>

          <div>
            <label htmlFor="publish-caption" className="text-sm font-bold text-fg">Caption</label>
            <textarea
              id="publish-caption"
              value={caption}
              onChange={(event) => setCaption(event.target.value)}
              rows={3}
              placeholder="Nội dung và hashtag..."
              className="field mt-2 w-full resize-y"
            />
          </div>

          <fieldset>
            <legend className="mb-2 text-sm font-bold text-fg">Thời điểm đăng</legend>
            <div className="inline-flex rounded-lg border border-line-soft bg-surface-2 p-1">
              <button type="button" aria-pressed={mode === 'now'} className={`seg ${mode === 'now' ? 'seg-active' : ''}`} onClick={() => setMode('now')}>
                <Send className="h-4 w-4" aria-hidden="true" /> Đăng ngay
              </button>
              <button type="button" aria-pressed={mode === 'schedule'} className={`seg ${mode === 'schedule' ? 'seg-active' : ''}`} onClick={() => setMode('schedule')}>
                <Clock className="h-4 w-4" aria-hidden="true" /> Hẹn giờ
              </button>
            </div>
          </fieldset>

          {mode === 'schedule' && (
            <div className="rounded-xl border border-line-soft bg-surface-2 p-3">
              <label htmlFor="publish-time" className="text-sm font-bold text-fg">Thời điểm đăng</label>
              <input id="publish-time" type="datetime-local" value={runAt} onChange={(event) => setRunAt(event.target.value)} className="field mt-2 w-full" />
              <p className="mt-2 flex gap-2 text-xs leading-5 text-fg-muted">
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" aria-hidden="true" />
                App sẽ mở trình duyệt và đăng khi tới giờ; không phụ thuộc tính năng lên lịch của TikTok.
              </p>
            </div>
          )}

          {error && <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">{error}</p>}
          {message && (
            <p role="status" className="flex gap-2 rounded-xl border border-brand/40 bg-brand/10 p-3 text-sm text-fg">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden="true" /> {message}
            </p>
          )}
        </div>

        <footer className="border-t border-line-soft p-5">
          <button
            type="button"
            disabled={busy || selectedAccountIds.length === 0}
            onClick={submit}
            className="btn flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-brand py-2.5 font-bold text-slate-950 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" /> : mode === 'now' ? <Send className="h-4 w-4" aria-hidden="true" /> : <Clock className="h-4 w-4" aria-hidden="true" />}
            {busy ? 'Đang tạo tác vụ...' : mode === 'now' ? `Đăng nội dung lên ${selectedAccountIds.length} tài khoản` : `Hẹn đăng cho ${selectedAccountIds.length} tài khoản`}
          </button>
        </footer>
      </section>
    </div>
  );
};
