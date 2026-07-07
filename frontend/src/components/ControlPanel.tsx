import React from 'react';

interface ControlPanelProps {
  concurrency: number;
  setConcurrency: (val: number) => void;
  avatarFolder: string;
  setAvatarFolder: (val: string) => void;
}

export const ControlPanel: React.FC<ControlPanelProps> = ({
  concurrency,
  setConcurrency,
  avatarFolder,
  setAvatarFolder
}) => {
  return (
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
        <label className="text-xs text-slate-400 block mb-1 font-semibold">Đường dẫn thư mục chứa ảnh đại diện (Avatar Folder):</label>
        <input
          type="text"
          placeholder="Ví dụ: /home/dtanlocc/Downloads/avatars"
          value={avatarFolder}
          onChange={(e) => setAvatarFolder(e.target.value)}
          className="w-full bg-[#182032] border border-slate-700 rounded-xl p-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-teal-400 text-slate-100"
        />
      </div>
    </div>
  );
};