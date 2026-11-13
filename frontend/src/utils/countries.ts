// File mới: frontend/src/utils/countries.ts

export interface CountryConfig {
  code: string;        // Mã ISO 2 chữ cái viết hoa lưu trong DB (Ví dụ: 'US', 'VN')
  flagCode: string;    // Mã code map với FlagCDN để lấy ảnh cờ (Ví dụ: 'gb' cho Anh)
  name: string;        // Tên hiển thị trực quan tiếng Việt trên giao diện
}

// =============================================================================
// DANH SÁCH QUỐC GIA ĐƯỢC HỖ TRỢ (DỄ DÀNG THÊM MỚI CHỈ TRONG 1 DÒNG)
// =============================================================================
export const SUPPORTED_COUNTRIES: CountryConfig[] = [
  { code: 'US', flagCode: 'us', name: 'Mỹ (United States)' },
  { code: 'DE', flagCode: 'de', name: 'Đức (Germany)' },
  { code: 'GB', flagCode: 'gb', name: 'Anh (United Kingdom)' },
  { code: 'KR', flagCode: 'kr', name: 'Hàn Quốc (South Korea)' },
  { code: 'VN', flagCode: 'vn', name: 'Việt Nam' },
  { code: 'CA', flagCode: 'ca', name: 'Canada' },
  { code: 'FR', flagCode: 'fr', name: 'Pháp (France)' },
  { code: 'AU', flagCode: 'au', name: 'Úc (Australia)' },
  { code: 'IT', flagCode: 'it', name: 'Ý (Italy)' },
  { code: 'ES', flagCode: 'es', name: 'Tây Ban Nha (Spain)' },
  { code: 'NL', flagCode: 'nl', name: 'Hà Lan (Netherlands)' },
  { code: 'PL', flagCode: 'pl', name: 'Ba Lan (Poland)' },
  { code: 'SE', flagCode: 'se', name: 'Thụy Điển (Sweden)' },
  { code: 'JP', flagCode: 'jp', name: 'Nhật Bản (Japan)' },
  { code: 'TW', flagCode: 'tw', name: 'Đài Loan (Taiwan)' },
  { code: 'TH', flagCode: 'th', name: 'Thái Lan (Thailand)' },
  { code: 'ID', flagCode: 'id', name: 'Indonesia' },
  { code: 'PH', flagCode: 'ph', name: 'Philippines' },
  { code: 'MY', flagCode: 'my', name: 'Malaysia' },
  { code: 'SG', flagCode: 'sg', name: 'Singapore' },
  { code: 'BR', flagCode: 'br', name: 'Brazil' },
  { code: 'MX', flagCode: 'mx', name: 'Mexico' },
  // Thêm nước mới: một dòng ở đây là đủ, không phải sửa chỗ nào khác.
];

// =============================================================================
// BỘ ĐỊNH VỊ ẢNH CỜ TỰ SỬA LỖI ĐỒNG BỘ TOÀN HỆ THỐNG
// =============================================================================
export const getCountryFlagUrl = (code: string): string => {
  if (!code) return 'https://flagcdn.com/w40/us.png';
  
  let cleaned = code.trim();
  
  // 1. Bộ giải mã nhị phân Unicode: Tự động dịch ngược các Emoji Flag thô cũ từ DB thành mã ISO chữ cái
  const chars = Array.from(cleaned);
  if (chars.length === 2) {
    const codePoints = chars.map(c => c.codePointAt(0) || 0);
    if (codePoints.every(cp => cp >= 0x1F1E6 && cp <= 0x1F1FF)) {
      cleaned = String.fromCharCode(...codePoints.map(cp => cp - 0x1F1E6 + 65)).toUpperCase();
    }
  }
  
  cleaned = cleaned.toUpperCase();

  // 2. Bộ lọc biệt danh (Aliases Map) để dọn dẹp dữ liệu gõ tay lỗi từ người dùng
  const aliases: Record<string, string> = {
    'UK': 'GB',
    'ANH': 'GB',
    'DUC': 'DE',
    'HAN': 'KR',
    'KOREA': 'KR',
  };
  
  const targetCode = aliases[cleaned] || cleaned;

  // 3. Tra cứu trong danh sách cấu hình động
  const found = SUPPORTED_COUNTRIES.find(c => c.code === targetCode);
  const finalFlagCode = found ? found.flagCode : targetCode.toLowerCase();
  
  return `https://flagcdn.com/w40/${finalFlagCode}.png`;
};