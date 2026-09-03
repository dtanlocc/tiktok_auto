import React from 'react';
import { Users, Zap, Globe, HeartPulse } from 'lucide-react';
import { Account, Proxy } from '../types';

interface StatsCardsProps {
  accounts: Account[];
  proxies: Proxy[];
}

export const StatsCards: React.FC<StatsCardsProps> = ({ accounts, proxies }) => {
  const total = accounts.length;
  const running = accounts.filter((a) => a.status === 'RUNNING').length;
  const alive = accounts.filter((a) => a.health_status === 'ALIVE').length;

  const items = [
    { label: 'Tài khoản', value: total, Icon: Users, tint: 'text-sky-400', bg: 'bg-sky-400/10 border-sky-400/20' },
    { label: 'Đang chạy', value: running, Icon: Zap, tint: 'text-amber-400', bg: 'bg-amber-400/10 border-amber-400/20' },
    { label: 'Nick sống', value: alive, Icon: HeartPulse, tint: 'text-emerald-400', bg: 'bg-emerald-400/10 border-emerald-400/20' },
    { label: 'Proxy', value: proxies.length, Icon: Globe, tint: 'text-brand', bg: 'bg-brand/10 border-brand/20' },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {items.map(({ label, value, Icon, tint, bg }) => (
        <div key={label} className="card px-3.5 py-2.5 flex items-center justify-between">
          <div className="min-w-0">
            <p className="text-[11px] text-fg-muted font-semibold uppercase tracking-wide truncate">{label}</p>
            <p className="text-xl font-bold mt-0.5 text-fg tabular-nums leading-tight">{value}</p>
          </div>
          <div className={`grid place-items-center w-9 h-9 rounded-lg border ${bg} shrink-0`}>
            <Icon className={`w-[18px] h-[18px] ${tint}`} />
          </div>
        </div>
      ))}
    </div>
  );
};
