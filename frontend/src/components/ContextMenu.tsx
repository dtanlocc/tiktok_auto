import React from 'react';
import { Sparkles, RefreshCw, Key, Video, Trash2, Globe } from 'lucide-react';

interface ContextMenuProps {
  x: number;
  y: number;
  selectedCount: number;
  onBulkLogin: (method: 'COOKIE' | 'CREDENTIAL') => void;
  onBulkUpdateProfile: () => void;
  onAutoAllocateProxies: () => void;
  onBulkDelete: () => void;
}

export const ContextMenu: React.FC<ContextMenuProps> = ({
  x,
  y,
  selectedCount,
  onBulkLogin,
  onBulkUpdateProfile,
  onAutoAllocateProxies,
  onBulkDelete
}) => {
  return (
    <div 
      style={{ top: y, left: x }}
      className="fixed bg-[#040814]/90 border border-cyan-500/20 shadow-[0_10px_40px_rgba(0,0,0,0.95)] rounded-sm p-1.5 z-50 min-w-[240px] text-xs text-slate-300 font-sans backdrop-blur-md border-b-2 border-b-cyan-500"
    >
      <div className="px-2 py-1 text-[8px] text-cyan-500/60 font-black uppercase tracking-widest border-b border-slate-900 mb-1.5 select-none flex items-center gap-1.5">
        <Sparkles className="w-2.5 h-2.5 text-cyan-400" />
        <span>Batch Operations // {selectedCount} Nodes</span>
      </div>

      <button 
        onClick={() => onBulkLogin('COOKIE')}
        className="w-full text-left px-2 py-1.5 hover:bg-cyan-950/20 hover:text-cyan-400 rounded-sm flex items-center gap-2.5 transition-all group font-mono"
      >
        <RefreshCw className="w-3.5 h-3.5 text-slate-600 group-hover:text-cyan-400 transition-transform duration-300 group-hover:rotate-180" />
        <span className="text-[10px] font-black uppercase tracking-widest">LOGIN_VIA_COOKIES</span>
      </button>

      <button 
        onClick={() => onBulkLogin('CREDENTIAL')}
        className="w-full text-left px-2 py-1.5 hover:bg-cyan-950/20 hover:text-cyan-400 rounded-sm flex items-center gap-2.5 transition-all group font-mono"
      >
        <Key className="w-3.5 h-3.5 text-slate-600 group-hover:text-cyan-400" />
        <span className="text-[10px] font-black uppercase tracking-widest">LOGIN_VIA_FORM_OTP</span>
      </button>

      <button 
        onClick={onBulkUpdateProfile}
        className="w-full text-left px-2 py-1.5 hover:bg-cyan-950/20 hover:text-cyan-400 rounded-sm flex items-center gap-2.5 transition-all group font-mono"
      >
        <Video className="w-3.5 h-3.5 text-slate-600 group-hover:text-cyan-400 animate-bounce" />
        <span className="text-[10px] font-black uppercase tracking-widest">UPDATE_AVATAR_BIO</span>
      </button>

      <button 
        onClick={onAutoAllocateProxies}
        className="w-full text-left px-2 py-1.5 hover:bg-amber-950/20 hover:text-amber-400 rounded-sm flex items-center gap-2.5 transition-all group font-mono"
      >
        <Globe className="w-3.5 h-3.5 text-slate-600 group-hover:text-amber-400" />
        <span className="text-[10px] font-black uppercase tracking-widest">AUTO_MAP_PROXIES</span>
      </button>
      
      <div className="h-[1px] bg-slate-900 my-1.5 select-none"></div>
      
      {/* NÂNG CẤP KÍCH HOẠT NÚT XÓA BẢN GHI */}
      <button 
        onClick={onBulkDelete}
        className="w-full text-left px-2 py-1.5 hover:bg-rose-950/20 hover:text-rose-400 rounded-sm flex items-center gap-2.5 transition-all group font-mono text-rose-500"
      >
        <Trash2 className="w-3.5 h-3.5 text-rose-700 group-hover:text-rose-400" />
        <span className="text-[10px] font-black uppercase tracking-widest">DROP_RECORDS</span>
      </button>
    </div>
  );
};