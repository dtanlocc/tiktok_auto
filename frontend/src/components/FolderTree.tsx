// File: frontend/src/components/FolderTree.tsx
import React from 'react';
import { Folder, FolderOpen, ChevronDown, ChevronRight, Globe, Plus, PanelLeftClose } from 'lucide-react';
import { Account } from '../types';
import { getCountryFlagUrl } from '../utils/countries'; 

interface FolderTreeProps {
  accounts: Account[];
  selectedCountry: string | null;
  selectedBatch: string | null;
  expandedCountries: string[];
  onSelectBatch: (country: string, batch: string) => void;
  onToggleCountry: (country: string) => void;
  onOpenImportModal: () => void;
  onCollapse: () => void;
}

export const FolderTree: React.FC<FolderTreeProps> = ({
  accounts,
  selectedCountry,
  selectedBatch,
  expandedCountries,
  onSelectBatch,
  onToggleCountry,
  onOpenImportModal,
  onCollapse
}) => {
  // Phân nhóm tài khoản động
  const treeData: Record<string, Record<string, Account[]>> = {};
  accounts.forEach((acc) => {
    const country = acc.country || 'US';
    const batch = acc.batch_tag || 'DEFAULT';
    
    if (!treeData[country]) {
      treeData[country] = {};
    }
    if (!treeData[country][batch]) {
      treeData[country][batch] = [];
    }
    treeData[country][batch].push(acc);
  });

  const countries = Object.keys(treeData).sort();

  return (
    <div className="bg-surface rounded-2xl border border-line-soft p-4 flex flex-col gap-3 min-h-[450px]">
      
      {/* 1. HEADER CARD CHỈ HIỆN DUY NHẤT 1 LẦN Ở ĐẦU TRANG */}
      <div className="flex items-center justify-between pb-3 border-b border-line-soft">
        <div className="flex items-center gap-2">
          <Globe className="text-brand w-4 h-4" />
          <h3 className="font-bold text-xs text-fg uppercase tracking-wider">Cây Thư Mục Quốc Gia</h3>
        </div>
        
        <div className="flex items-center gap-1.5">
          {/* NÚT BẤM KÍCH HOẠT POPUP IMPORT TINH TẾ */}
          <button
            onClick={onOpenImportModal}
            className="bg-teal-500 hover:bg-teal-600 text-slate-950 rounded-lg p-1 px-2.5 text-[10px] font-bold flex items-center gap-1 transition-all shadow-md shadow-teal-500/10 cursor-pointer"
            title="Nhập tài khoản hàng loạt (.txt)"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>Nhập Acc</span>
          </button>

          {/* NÚT THU GỌN CÂY THƯ MỤC - ĐỂ NHƯỜNG CHỖ CHO BẢNG XEM FULL DATABASE */}
          <button
            onClick={onCollapse}
            className="bg-slate-900 hover:bg-slate-800 text-fg-muted hover:text-fg border border-line-soft rounded-lg p-1.5 transition-all cursor-pointer"
            title="Thu gọn cây thư mục (xem toàn bộ tài khoản)"
          >
            <PanelLeftClose className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* 2. KHU VỰC HIỂN THỊ DANH SÁCH THƯ MỤC CÂY */}
      <div className="flex-1 overflow-y-auto space-y-1 pr-1 max-h-[500px]">
        {countries.length === 0 ? (
          <div className="text-fg-subtle text-xs text-center py-8 italic font-medium">
            Chưa có thư mục nào. Vui lòng nạp file tài khoản.
          </div>
        ) : (
          countries.map((country) => {
            const batches = Object.keys(treeData[country]).sort();
            const isExpanded = expandedCountries.includes(country);
            const totalAccInCountry = Object.values(treeData[country]).reduce(
              (sum, list) => sum + list.length, 0
            );

            return (
              <div key={country} className="space-y-0.5">
                
                {/* THƯ MỤC CHA (QUỐC GIA) */}
                <div 
                  onClick={() => onToggleCountry(country)}
                  className="flex items-center justify-between p-2 rounded-lg hover:bg-slate-800/40 cursor-pointer transition-all select-none group"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xs">
                      {isExpanded ? <ChevronDown className="w-3.5 h-3.5 text-fg-subtle" /> : <ChevronRight className="w-3.5 h-3.5 text-fg-subtle" />}
                    </span>
                    <img 
                      src={getCountryFlagUrl(country)} 
                      alt={country} 
                      className="w-4.5 h-3.5 object-cover rounded-sm border border-line-soft shadow-sm shrink-0"
                      onError={(e) => { e.currentTarget.style.display = 'none'; }}
                    />
                    <span className="text-xs font-bold text-fg tracking-wider group-hover:text-fg uppercase truncate">{country}</span>
                  </div>
                  <span className="bg-slate-900 text-fg-muted border border-line-soft text-[9px] px-2 py-0.5 rounded-full font-bold">
                    {totalAccInCountry}
                  </span>
                </div>

                {/* THƯ MỤC CON (LÔ HÀNG NGÀY) */}
                {isExpanded && (
                  <div className="pl-6 border-l border-line-soft/80 ml-3.5 space-y-0.5 transition-all">
                    {batches.map((batch) => {
                      const count = treeData[country][batch].length;
                      const isSelected = selectedCountry === country && selectedBatch === batch;

                      return (
                        <div
                          key={batch}
                          onClick={() => onSelectBatch(country, batch)}
                          className={`flex items-center justify-between p-2 rounded-md cursor-pointer transition-all ${
                            isSelected 
                              ? 'bg-brand/10 text-brand border border-teal-500/25 font-bold shadow-[0_0_15px_rgba(20,184,166,0.05)]' 
                              : 'text-fg-muted hover:bg-slate-800/20 hover:text-fg border border-transparent'
                          }`}
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            {isSelected ? (
                              <FolderOpen className="w-3.5 h-3.5 text-brand shrink-0" />
                            ) : (
                              <Folder className="w-3.5 h-3.5 text-fg-subtle shrink-0" />
                            )}
                            <span className="text-[11px] truncate">{batch}</span>
                          </div>
                          <span className={`text-[9px] px-1.5 py-0.2 rounded-md font-bold shrink-0 ${
                            isSelected 
                              ? 'bg-teal-500/20 text-brand' 
                              : 'bg-slate-900 text-fg-subtle border border-line-soft'
                          }`}>
                            {count} acc
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};