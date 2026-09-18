import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { FileText, X } from 'lucide-react';
import { proxyApi } from '../../services/proxyApi';

interface ProxyImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImported: (message: string) => void;
}

const FORMATS = [
  'socks5://user:pass@host:port',
  'socks5://host:port:user:pass',
  'http://host:port',
  'host|port|socks5|user|pass',
];

export const ProxyImportModal: React.FC<ProxyImportModalProps> = ({ isOpen, onClose, onImported }) => {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!isOpen) return;
    setText('');
    setError('');
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const lineCount = text.split(/\r?\n/).filter((line) => line.trim() && !line.trim().startsWith('#')).length;

  const addFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const contents = await Promise.all(Array.from(files).map((file) => file.text()));
    setText((current) => [current.trim(), ...contents.map((c) => c.trim())].filter(Boolean).join('\n'));
  };

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await proxyApi.importText(text);
      onImported(result.message);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-black/75 p-4 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}
    >
      <div role="dialog" aria-modal="true" aria-labelledby="proxy-import-title" className="card w-full max-w-xl shadow-2xl shadow-black/60">
        <div className="flex items-center justify-between p-4 border-b border-line-soft">
          <h2 id="proxy-import-title" className="font-semibold text-sm text-fg">Nhập proxy hàng loạt</h2>
          <button type="button" onClick={onClose} disabled={busy} className="text-fg-subtle hover:text-fg" aria-label="Đóng">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-4 space-y-3">
          <p className="text-[11px] text-fg-muted leading-relaxed">
            Mỗi dòng một proxy. Proxy đã có trong kho sẽ được bỏ qua. Định dạng hỗ trợ:
          </p>
          <ul className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] font-mono text-fg-subtle">
            {FORMATS.map((f) => <li key={f}>{f}</li>)}
          </ul>
          <textarea
            className="field w-full min-h-[200px] font-mono text-xs resize-y"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Dán danh sách proxy vào đây..."
            spellCheck={false}
            aria-label="Danh sách proxy"
          />
          <div className="flex items-center justify-between">
            <label className="btn btn-ghost btn-sm">
              <FileText className="w-3.5 h-3.5" /> Thêm từ tệp .txt
              <input type="file" accept=".txt" multiple className="hidden"
                onChange={(e) => { addFiles(e.target.files); e.target.value = ''; }} />
            </label>
            <span className="text-[11px] text-fg-subtle">{lineCount} dòng</span>
          </div>
          {error && <p role="alert" className="text-xs text-danger">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 p-4 border-t border-line-soft">
          <button type="button" onClick={onClose} disabled={busy} className="btn btn-ghost">Hủy</button>
          <button type="button" onClick={submit} disabled={busy || lineCount === 0} className="btn btn-primary">
            {busy ? 'Đang nhập...' : `Nhập ${lineCount} dòng`}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
};
