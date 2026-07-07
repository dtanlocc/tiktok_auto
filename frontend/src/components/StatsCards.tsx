import React from 'react';
import { Users, CloudLightning, Globe, ShieldAlert } from 'lucide-react';
import { Account, Proxy } from '../store/useAppStore';

interface StatsCardsProps {
  accounts: Account[];
  proxies: Proxy[];
}

export const StatsCards: React.FC<StatsCardsProps> = ({ accounts, proxies }) => {
  const stats = {
    total: accounts.length,
    running: accounts.filter(a => a.status === 'RUNNING').length,
    proxies: proxies.length,
    loggedIn: accounts.filter(a => a.status === 'LOGGED_IN').length,
  };

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
        <div><p className="text-xs text-slate-400 font-semibold">Tài khoản</p><p className="text-2xl font-bold mt-1">{stats.total}</p></div>
        <Users className="text-blue-400 w-7 h-7 opacity-75" />
      </div>
      <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
        <div><p className="text-xs text-slate-400 font-semibold">Đang chạy</p><p className="text-2xl font-bold mt-1 text-amber-400">{stats.running}</p></div>
        <CloudLightning className="text-amber-400 w-7 h-7 opacity-75" />
      </div>
      <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
        <div><p className="text-xs text-slate-400 font-semibold">Proxy Hệ thống</p><p className="text-2xl font-bold mt-1 text-teal-400">{stats.proxies}</p></div>
        <Globe className="text-teal-400 w-7 h-7 opacity-75" />
      </div>
      <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
        <div><p className="text-xs text-slate-400 font-semibold">Đã đăng nhập</p><p className="text-2xl font-bold mt-1 text-emerald-400">{stats.loggedIn}</p></div>
        <ShieldAlert className="text-emerald-400 w-7 h-7 opacity-75" />
      </div>
    </div>
  );
};