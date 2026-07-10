// File: frontend/src/components/ImportModal.tsx
import React, { useState } from 'react';
import { X, Globe, Files, FolderSync } from 'lucide-react';
import { SUPPORTED_COUNTRIES } from '../utils/countries';

interface ImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  loading: boolean;
  onFileUpload: (
    event: React.ChangeEvent<HTMLInputElement>,
    type: 'accounts' | 'proxies',
    country?: string,
    batchTag?: string
  ) => void;
}

export const ImportModal: React.FC<ImportModalProps> = ({
  isOpen,
  onClose,
  loading,
  onFileUpload,
}) => {
  const [importCountry, setImportCountry] = useState<string>('US');
  const [importBatchTag, setImportBatchTag] = useState<string>('');

  if (!isOpen) return null;

  // Đóng modal an toàn khi nhấp chuột ra ngoài màn hình nền
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  return (
    <div
      onClick={handleBackdropClick}
      className="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4 transition-all"
    >
      <div className="bg-[#0e1424] border border-slate-800 rounded-2xl w-full max-w-sm shadow-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        
        {/* HEADER MODAL */}
        <div className="flex items-center justify-between p-4 border-b border-slate-800 bg-[#141b2e]/50">
          <div className="flex items-center gap-2">
            <Globe className="text-teal-400 w-4 h-4" />
            <h3 className="font-bold text-xs text-slate-100 uppercase tracking-wider">
              Nhập tài khoản hàng loạt
            </h3>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-100 rounded-lg p-1 hover:bg-slate-800 transition-colors cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* CẤU HÌNH NHẬP (BODY MODAL) */}
        <div className="p-4 flex flex-col gap-4">
          
          {/* LỰA CHỌN QUỐC GIA (Dạng chữ chuẩn né hoàn toàn lỗi hiển thị ô vuông của Windows) */}
          <div>
            <label className="text-[9px] text-slate-400 block mb-1 font-bold uppercase tracking-wider">
              1. Chọn Quốc Gia:
            </label>
            <select
              value={importCountry}
              onChange={(e) => setImportCountry(e.target.value)}
              className="w-full bg-[#182032] border border-slate-700 rounded-lg p-2 text-xs text-teal-400 font-semibold focus:outline-none focus:ring-1 focus:ring-teal-400 h-9"
            >
              {SUPPORTED_COUNTRIES.map((country) => (
                <option key={country.code} value={country.code}>
                  {country.code} - {country.name}
                </option>
              ))}
            </select>
          </div>

          {/* ĐIỀN TÊN LÔ HÀNG */}
          <div>
            <label className="text-[9px] text-slate-400 block mb-1 font-bold uppercase tracking-wider">
              2. Đặt tên Lô hàng (Batch Name):
            </label>
            <input
              type="text"
              placeholder="Để trống sẽ tự động đặt theo ngày"
              value={importBatchTag}
              onChange={(e) => setImportBatchTag(e.target.value)}
              className="w-full bg-[#182032] border border-slate-700 rounded-lg p-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-teal-400 h-9"
            />
          </div>

          <div className="h-[1px] bg-slate-800"></div>

          {/* 2 PHƯƠNG THỨC TẢI LÊN COMPACT */}
          <div className="grid grid-cols-2 gap-3">
            
            {/* TẢI LÊN FILE LẺ */}
            <label className="flex flex-col items-center justify-center border border-dashed border-slate-700 rounded-xl py-4 px-2 cursor-pointer hover:border-teal-400 transition-all bg-[#182032]/30 text-center hover:bg-teal-500/5 group">
              <Files className="text-slate-400 w-5 h-5 mb-1 group-hover:text-teal-400 transition-colors" />
              <span className="text-[10px] font-bold text-slate-200">Chọn tệp lẻ .txt</span>
              <span className="text-[8px] text-slate-500 mt-0.5">Tải một hoặc nhiều file</span>
              <input
                type="file"
                accept=".txt"
                multiple={true}
                disabled={loading}
                onChange={(e) => {
                  onFileUpload(e, 'accounts', importCountry, importBatchTag);
                  onClose && onFileUpload(e, 'accounts', importCountry, importBatchTag);
                }}
                className="hidden"
              />
            </label>

            {/* TẢI LÊN CẢ THƯ MỤC ĐỆ QUY */}
            <label className="flex flex-col items-center justify-center border border-dashed border-slate-700 rounded-lg py-3 px-2 cursor-pointer hover:border-teal-400 transition-colors bg-[#182032]/30 text-center hover:bg-teal-500/5 group">
              <FolderSync className="text-slate-400 w-5 h-5 mb-1 group-hover:text-teal-400 transition-colors" />
              <span className="text-[10px] font-bold text-slate-200">Chọn cả Thư mục</span>
              <span className="text-[8px] text-slate-500 mt-0.5">Quét đệ quy lấy file .txt</span>
              <input
                type="file"
                disabled={loading}
                onChange={(e) => {
                  onFileUpload(e, 'accounts', importCountry, importBatchTag);
                }}
                className="hidden"
                // @ts-ignore
                webkitdirectory=""
                // @ts-ignore
                directory=""
                multiple={true}
              />
            </label>

          </div>
        </div>

        {/* FOOTER MODAL */}
        <div className="bg-[#141b2e]/30 px-4 py-2.5 border-t border-slate-800 flex justify-end">
          <button
            onClick={onClose}
            className="bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg py-1.5 px-3 text-[10px] font-bold transition-colors cursor-pointer"
          >
            Đóng
          </button>
        </div>

      </div>
    </div>
  );
};