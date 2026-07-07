import React from 'react';
import { FileInput, Plus } from 'lucide-react';

interface SidebarProps {
  activeTab: 'accounts' | 'proxies';
  loading: boolean;
  onFileUpload: (event: React.ChangeEvent<HTMLInputElement>, type: 'accounts' | 'proxies') => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, loading, onFileUpload }) => {
  return (
    <div className="bg-[#0e1424] p-5 rounded-2xl border border-slate-800 h-fit flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <FileInput className="text-teal-400 w-5 h-5" />
        <h2 className="font-bold text-slate-200">Nhập dữ liệu (.txt)</h2>
      </div>
      
      {activeTab === 'accounts' ? (
        <div className="space-y-4">
          <p className="text-xs text-slate-400 leading-relaxed">
            Nhấp nút dưới để chọn tải lên hàng loạt tệp tài khoản `.txt` cùng lúc (cho phép giữ Ctrl để chọn nhiều tệp).
          </p>
          <label className="flex flex-col items-center justify-center border-2 border-dashed border-slate-700 rounded-xl p-6 cursor-pointer hover:border-teal-400 transition-colors bg-[#182032]/50">
            <Plus className="text-slate-400 w-6 h-6 mb-2" />
            <span className="text-xs font-semibold text-slate-300">Chọn các file accounts.txt</span>
            <input
              type="file"
              accept=".txt"
              multiple={true}
              disabled={loading}
              onChange={(e) => onFileUpload(e, 'accounts')}
              className="hidden"
            />
          </label>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-xs text-slate-400 leading-relaxed">
            Nhấp nút dưới để chọn tải lên hàng loạt tệp Proxy `.txt` cùng lúc.
          </p>
          <label className="flex flex-col items-center justify-center border-2 border-dashed border-slate-700 rounded-xl p-6 cursor-pointer hover:border-teal-400 transition-colors bg-[#182032]/50">
            <Plus className="text-slate-400 w-6 h-6 mb-2" />
            <span className="text-xs font-semibold text-slate-300">Chọn các file proxies.txt</span>
            <input
              type="file"
              accept=".txt"
              multiple={true}
              disabled={loading}
              onChange={(e) => onFileUpload(e, 'proxies')}
              className="hidden"
            />
          </label>
        </div>
      )}
    </div>
  );
};