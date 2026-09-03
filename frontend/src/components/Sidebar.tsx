// File: frontend/src/components/Sidebar.tsx
import React, { useState } from 'react';
import { FileInput, FolderSync, Files, Plus } from 'lucide-react';
import { SUPPORTED_COUNTRIES } from '../utils/countries'; // <-- IMPORT DANH SÁCH ĐỘNG

const directoryInputProps: React.InputHTMLAttributes<HTMLInputElement> & {
  webkitdirectory: string;
  directory: string;
} = { webkitdirectory: '', directory: '' };

interface SidebarProps {
  activeTab: 'accounts' | 'proxies' | 'interactions';
  loading: boolean;
  onFileUpload: (event: React.ChangeEvent<HTMLInputElement>, type: 'accounts' | 'proxies', country?: string, batchTag?: string) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, loading, onFileUpload }) => {
  const [importCountry, setImportCountry] = useState<string>('US');
  const [importBatchTag, setImportBatchTag] = useState<string>('');

  return (
    <div className="bg-surface p-3 rounded-xl border border-line-soft h-fit flex flex-col gap-3.5 max-w-full">
      <div className="flex items-center gap-1.5 pb-2 border-b border-line-soft/60">
        <FileInput className="text-brand w-4 h-4" />
        <h3 className="font-bold text-fg text-xs uppercase tracking-wider">Cấu hình Import</h3>
      </div>
      
      {activeTab === 'accounts' ? (
        <div className="space-y-3">
          {/* ĐỌC ĐỘNG TỪ CONTAINER QUỐC GIA ĐĂNG KÝ TẬP TRUNG */}
          <div>
            <label className="text-[9px] text-fg-muted block mb-1 font-bold uppercase tracking-wider">Quốc Gia:</label>
            <select
              value={importCountry}
              onChange={(e) => setImportCountry(e.target.value)}
              className="w-full bg-surface-2 border border-line rounded-lg p-1.5 text-xs text-brand font-semibold focus:outline-none focus:ring-1 focus:ring-teal-400 h-8"
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
            <label className="text-[9px] text-fg-muted block mb-1 font-bold uppercase tracking-wider">Tên Lô hàng (Batch Name):</label>
            <input
              type="text"
              placeholder="Để trống tự đặt theo ngày"
              value={importBatchTag}
              onChange={(e) => setImportBatchTag(e.target.value)}
              className="w-full bg-surface-2 border border-line rounded-lg p-2 text-xs text-fg placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-teal-400 h-8"
            />
          </div>

          <div className="h-[1px] bg-slate-800 my-1"></div>

          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col items-center justify-center border border-dashed border-line rounded-lg py-3 px-2 cursor-pointer hover:border-teal-400 transition-colors bg-surface-2/30 text-center hover:bg-teal-500/5 group">
              <Files className="text-fg-muted w-4 h-4 mb-1 group-hover:text-brand transition-colors" />
              <span className="text-[10px] font-bold text-fg group-hover:text-fg">Chọn tệp .txt</span>
              <input
                {...directoryInputProps}
                type="file"
                accept=".txt"
                multiple={true}
                disabled={loading}
                onChange={(e) => onFileUpload(e, 'accounts', importCountry, importBatchTag)}
                className="hidden"
              />
            </label>

            <label className="flex flex-col items-center justify-center border border-dashed border-line rounded-lg py-3 px-2 cursor-pointer hover:border-teal-400 transition-colors bg-surface-2/30 text-center hover:bg-teal-500/5 group">
              <FolderSync className="text-fg-muted w-4 h-4 mb-1 group-hover:text-brand transition-colors" />
              <span className="text-[10px] font-bold text-fg group-hover:text-fg">Chọn Thư Mục</span>
              <input
                type="file"
                disabled={loading}
                onChange={(e) => onFileUpload(e, 'accounts', importCountry, importBatchTag)}
                className="hidden"
                multiple={true}
              />
            </label>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-[10px] text-fg-muted leading-normal">
            Chọn tệp Proxies `.txt` để nhập hàng loạt vào hệ thống.
          </p>
          <label className="flex flex-col items-center justify-center border border-dashed border-line rounded-lg p-4 cursor-pointer hover:border-teal-400 transition-colors bg-surface-2/30 text-center group">
            <Plus className="text-fg-muted w-5 h-5 mb-1 group-hover:text-brand" />
            <span className="text-[10px] font-bold text-fg">Chọn proxies.txt</span>
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
