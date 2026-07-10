// File: frontend/src/components/ControlPanel.tsx
import React, { useState } from 'react';
import { FolderOpen } from 'lucide-react';

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
  const [loading, setLoading] = useState<boolean>(false);

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

  return (
    <div className="bg-[#0e1424] p-4 rounded-2xl border border-slate-800 grid grid-cols-1 md:grid-cols-3 gap-4 items-center">
      {/* CẤU HÌNH SONG SONG */}
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

      {/* ĐƯỜNG DẪN AVATAR COMPACT (KÈM NÚT BROWSE CHUYÊN NGHIỆP) */}
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
  );
};