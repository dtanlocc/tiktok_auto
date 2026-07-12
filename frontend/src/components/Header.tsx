import React from 'react';
import { Users, Globe, Heart } from 'lucide-react';

interface HeaderProps {
  activeTab: 'accounts' | 'proxies' | 'interactions';
  setActiveTab: (tab: 'accounts' | 'proxies' | 'interactions') => void;
}

export const Header: React.FC<HeaderProps> = ({ activeTab, setActiveTab }) => {
  return (
    <div className="flex justify-between items-center border-b border-slate-800 pb-4">
      <div>
        <h1 className="text-2xl font-bold bg-gradient-to-r from-teal-400 to-blue-500 bg-clip-text text-transparent">
          TikTok Automation Dashboard
        </h1>
        <p className="text-slate-400 text-sm">Bộ quản trị đa luồng tàng hình kết nối chuột phải và phân bổ IP thông minh</p>
      </div>
      
      {/* Tab Selection */}
      <div className="flex bg-slate-900 border border-slate-800 p-1.5 rounded-xl gap-1">
        <button
          onClick={() => setActiveTab('accounts')}
          className={`px-4 py-2 text-xs font-bold rounded-lg transition-all flex items-center gap-1.5 ${activeTab === 'accounts' ? 'bg-teal-500 text-slate-950' : 'text-slate-400 hover:text-slate-100'}`}
        >
          <Users className="w-3.5 h-3.5" /> Quản lý Tài Khoản
        </button>
        <button
          onClick={() => setActiveTab('interactions')}
          className={`px-4 py-2 text-xs font-bold rounded-lg transition-all flex items-center gap-1.5 ${activeTab === 'interactions' ? 'bg-teal-500 text-slate-950' : 'text-slate-400 hover:text-slate-100'}`}
        >
          <Heart className="w-3.5 h-3.5" /> Tương Tác Video
        </button>
        <button
          onClick={() => setActiveTab('proxies')}
          className={`px-4 py-2 text-xs font-bold rounded-lg transition-all flex items-center gap-1.5 ${activeTab === 'proxies' ? 'bg-teal-500 text-slate-950' : 'text-slate-400 hover:text-slate-100'}`}
        >
          <Globe className="w-3.5 h-3.5" /> Quản lý Proxies
        </button>
      </div>
    </div>
  );
};
