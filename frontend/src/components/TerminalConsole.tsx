import React from 'react';
import { Terminal as TerminalIcon, Trash } from 'lucide-react';

interface LogMessage {
  time: string;
  username: string;
  message: string;
}

interface TerminalConsoleProps {
  logs: LogMessage[];
  setLogs: React.Dispatch<React.SetStateAction<LogMessage[]>>;
  terminalEndRef: React.RefObject<HTMLDivElement>;
}

export const TerminalConsole: React.FC<TerminalConsoleProps> = ({ logs, setLogs, terminalEndRef }) => {
  return (
    <div className="card overflow-hidden flex flex-col h-[180px] shrink-0">
      <div className="bg-surface-2/40 px-3.5 py-2 border-b border-line-soft flex justify-between items-center">
        <div className="flex items-center gap-2 text-xs font-semibold text-fg-muted">
          <TerminalIcon className="text-brand w-4 h-4" />
          <span>Nhật ký hệ thống</span>
          {logs.length > 0 && <span className="badge bg-white/5 text-fg-subtle">{logs.length}</span>}
        </div>
        <button onClick={() => setLogs([])} className="btn btn-sm btn-ghost">
          <Trash className="w-3.5 h-3.5" /> Xóa
        </button>
      </div>
      <div className="p-3.5 font-mono text-[11px] leading-relaxed overflow-y-auto flex-1 space-y-0.5 bg-black/30">
        {logs.length === 0 ? (
          <div className="text-fg-subtle italic">Chờ khởi động tác vụ để ghi nhận log…</div>
        ) : (
          logs.map((log, index) => (
            <div key={index} className="flex gap-2">
              <span className="text-fg-subtle shrink-0">[{log.time}]</span>
              <span className="text-brand shrink-0">{log.username}</span>
              <span className="text-fg-muted">{log.message}</span>
            </div>
          ))
        )}
        <div ref={terminalEndRef} />
      </div>
    </div>
  );
};
