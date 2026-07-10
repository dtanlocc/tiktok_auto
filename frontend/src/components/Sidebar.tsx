// File: frontend/src/components/Sidebar.tsx
import React, { useState } from 'react';
import { FileInput, FolderSync, Files, Plus } from 'lucide-react';
import { SUPPORTED_COUNTRIES } from '../utils/countries'; // <-- IMPORT DANH SÁCH ĐỘNG

interface SidebarProps {
  activeTab: 'accounts' | 'proxies';
  loading: boolean;
  onFileUpload: (event: React.ChangeEvent<HTMLInputElement>, type: 'accounts' | 'proxies', country?: string, batchTag?: string) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, loading, onFileUpload }) => {
  const [importCountry, setImportCountry] = useState<string>('US');
  const [importBatchTag, setImportBatchTag] = useState<string>('');

  return (
    <div className="bg-[#0e1424] p-3 rounded-xl border border-slate-800 h-fit flex flex-col gap-3.5 max-w-full">
      <div className="flex items-center gap-1.5 pb-2 border-b border-slate-800/60">
        <FileInput className="text-teal-400 w-4 h-4" />
        <h3 className="font-bold text-slate-200 text-xs uppercase tracking-wider">Cấu hình Import</h3>
      </div>
      
      {activeTab === 'accounts' ? (
        <div className="space-y-3">
          {/* ĐỌC ĐỘNG TỪ CONTAINER QUỐC GIA ĐĂNG KÝ TẬP TRUNG */}
          <div>
            <label className="text-[9px] text-slate-400 block mb-1 font-bold uppercase tracking-wider">Quốc Gia:</label>
            <select
              value={importCountry}
              onChange={(e) => setImportCountry(e.target.value)}
              className="w-full bg-[#182032] border border-slate-700 rounded-lg p-1.5 text-xs text-teal-400 font-semibold focus:outline-none focus:ring-1 focus:ring-teal-400 h-8"
            >
              {SUPPORTED_COUNTRIES.map((country) => (
                <option key={country.code} value={country.code}>
                  {country.code} - {country.name}
                </option>
              ))}
            </select>
          </div>

          {/* ĐIỀN TÊN LÔ */}
          <div>
            <label className="text-[9px] text-slate-400 block mb-1 font-bold uppercase tracking-wider">Tên Lô hàng (Batch Name):</label>
            <input
              type="text"
              placeholder="Để trống tự đặt theo ngày"
              value={importBatchTag}
              onChange={(e) => setImportBatchTag(e.target.value)}
              className="w-full bg-[#182032] border border-slate-700 rounded-lg p-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-teal-400 h-8"
            />
          </div>

          <div className="h-[1px] bg-slate-800 my-1"></div>

          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col items-center justify-center border border-dashed border-slate-700 rounded-lg py-3 px-2 cursor-pointer hover:border-teal-400 transition-colors bg-[#182032]/30 text-center hover:bg-teal-500/5 group">
              <Files className="text-slate-400 w-4 h-4 mb-1 group-hover:text-teal-400 transition-colors" />
              <span className="text-[10px] font-bold text-slate-300 group-hover:text-slate-100">Chọn tệp .txt</span>
              <input
                type="file"
                accept=".txt"
                multiple={true}
                disabled={loading}
                onChange={(e) => onFileUpload(e, 'accounts', importCountry, importBatchTag)}
                className="hidden"
              />
            </label>

            <label className="flex flex-col items-center justify-center border border-dashed border-slate-700 rounded-lg py-3 px-2 cursor-pointer hover:border-teal-400 transition-colors bg-[#182032]/30 text-center hover:bg-teal-500/5 group">
              <FolderSync className="text-slate-400 w-4 h-4 mb-1 group-hover:text-teal-400 transition-colors" />
              <span className="text-[10px] font-bold text-slate-300 group-hover:text-slate-100">Chọn Thư Mục</span>
              <input
                type="file"
                disabled={loading}
                onChange={(e) => onFileUpload(e, 'accounts', importCountry, importBatchTag)}
                className="hidden"
                webkitdirectory=""
                directory=""
                multiple={true}
              />
            </label>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-[10px] text-slate-400 leading-normal">
            Chọn tệp Proxies `.txt` để nhập hàng loạt vào hệ thống.
          </p>
          <label className="flex flex-col items-center justify-center border border-dashed border-slate-700 rounded-lg p-4 cursor-pointer hover:border-teal-400 transition-colors bg-[#182032]/30 text-center group">
            <Plus className="text-slate-400 w-5 h-5 mb-1 group-hover:text-teal-400" />
            <span className="text-[10px] font-bold text-slate-300">Chọn proxies.txt</span>
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