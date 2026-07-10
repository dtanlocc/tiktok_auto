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
  // Bạn muốn thêm nước nào trong tương lai? Chỉ cần bỏ ghi chú hoặc thêm dòng mới tại đây:
  // { code: 'CA', flagCode: 'ca', name: 'Canada' },
  // { code: 'FR', flagCode: 'fr', name: 'Pháp (France)' },
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