import React from 'react';
import { Terminal as TerminalIcon } from 'lucide-react';

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
    <div className="bg-[#050811] border border-slate-800 rounded-2xl overflow-hidden flex flex-col h-[180px]">
      <div className="bg-[#0e1424] px-4 py-2 border-b border-slate-800 flex justify-between items-center">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-300">
          <TerminalIcon className="text-teal-400 w-4 h-4" />
          <span>Nhật ký luồng hệ thống (Live Terminal Console)</span>
        </div>
        <button onClick={() => setLogs([])} className="text-[10px] text-slate-500 hover:text-slate-300 font-bold">Xóa nhật ký</button>
      </div>
      <div className="p-4 font-mono text-xs overflow-y-auto flex-1 space-y-1 text-slate-400 bg-black/40">
        {logs.length === 0 ? <div className="text-slate-600 italic">Chờ khởi động tác vụ để ghi nhận log...</div> : logs.map((log, index) => (
          <div key={index} className="flex gap-2">
            <span className="text-slate-600">[{log.time}]</span>
            <span className="text-teal-400">[{log.username}]</span>
            <span className="text-slate-200">{log.message}</span>
          </div>
        ))}
        <div ref={terminalEndRef} />
      </div>
    </div>
  );
};