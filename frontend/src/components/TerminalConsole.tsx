import React, { useEffect, useRef } from 'react';
import { Terminal as TerminalIcon, Trash } from 'lucide-react';
import { useAppStore } from '../store/useAppStore';

export const TerminalConsole: React.FC = () => {
  const logs = useAppStore((state) => state.logs);
  const clearLogs = useAppStore((state) => state.clearLogs);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Chi cuon ben trong hop terminal. scrollIntoView() tren sentinel cu con
    // cuon ca document, nen moi dong log lai keo tab Man hinh truc tiep xuong.
    const container = scrollContainerRef.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [logs]);

  return (
    <section className="card flex h-[200px] shrink-0 flex-col overflow-hidden" aria-labelledby="system-log-title">
      <div className="flex items-center justify-between border-b border-line-soft bg-surface-2/40 px-3.5 py-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-fg-muted">
          <TerminalIcon className="h-4 w-4 text-brand" aria-hidden="true" />
          <span id="system-log-title">Nhật ký tác vụ</span>
          {logs.length > 0 && <span className="badge bg-white/5 text-fg-subtle">{logs.length}</span>}
        </div>
        <button type="button" onClick={clearLogs} className="btn btn-sm btn-ghost">
          <Trash className="h-3.5 w-3.5" aria-hidden="true" /> Xóa
        </button>
      </div>
      <div ref={scrollContainerRef} className="flex-1 space-y-0.5 overflow-y-auto bg-black/30 p-3.5 font-mono text-[11px] leading-relaxed">
        {logs.length === 0 ? (
          <div className="text-fg-subtle">Chưa có nhật ký tác vụ.</div>
        ) : logs.map((log, index) => (
          <div key={`${log.time}-${index}`} className="flex gap-2">
            <span className="shrink-0 text-fg-subtle">[{log.time}]</span>
            <span className="shrink-0 text-brand">{log.username}</span>
            <span className="text-fg-muted">{log.message}</span>
          </div>
        ))}
      </div>
    </section>
  );
};
