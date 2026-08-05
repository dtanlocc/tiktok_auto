import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sử dụng Path để tự động định dạng đường dẫn chuẩn hóa cho cả Linux và Windows
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    # Cấu hình API và App chung
    APP_NAME: str = "TikTok Automation System"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"
    
    # Cấu hình Database (Mặc định dùng SQLite lưu tại thư mục gốc của backend)
    # Tự động sinh đường dẫn độc lập hệ điều hành
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'database.db'}"
    
    # Cấu hình đa luồng (Task Dispatcher)
    MAX_CONCURRENT_TABS: int = 4

    # GIAN CACH (stagger) giua cac lan mo browser lien tiep - tranh mo 4 browser
    # cung luc (thrash dia/CPU + de bi TikTok phat hien nhieu nick 1 IP cung luc).
    # Giam de ramp-up da luong NHANH hon (rui ro phat hien cao hon); tang de an toan.
    STAGGER_MIN_SECONDS: float = 30.0
    STAGGER_MAX_SECONDS: float = 60.0

    # HEADLESS tren Windows dung co che "cloak" (DWMWA_CLOAK) de an cua so.
    # NHUNG cloak KHONG hoat dong tren RDP (Remote Desktop) -> cua so van hien.
    # Vi vay de FALSE (chay hien) va thay vao do dung HIDE_BROWSER_OFFSCREEN
    # (day cua so ra ngoai man hinh) - cach nay chay tren CA RDP lan may that.
    BROWSER_HEADLESS: bool = False

    # AN CUA SO BANG CACH DAY RA NGOAI MAN HINH (thay cho cloak/headless).
    # Cua so van "shown" nen VAN RENDER + chay duoc du KHONG duoc focus (khac
    # minimize/cloak la ngung render -> treo cho tha luong / phai click de kich hoat).
    # Ban khong thay cua so, khong bi che man hinh, va PrintWindow van chup stream
    # duoc. Dat FALSE neu muon NHIN truc tiep cua so (may that, khong qua RDP).
    HIDE_BROWSER_OFFSCREEN: bool = True

    # =========================================================================
    # STREAM ANH MAN HINH TRINH DUYET VE DASHBOARD (xem da luong truc tiep)
    # =========================================================================
    # Chup dinh ky moi trinh duyet va day ve UI qua WebSocket (event BROWSER_FRAME).
    # Tren Windows dung PrintWindow -> anh SACH + giu fingerprint manh (khong dung
    # page.screenshot vi ban Firefox tang hinh nhieu pixel khi readback).
    # INTERVAL_MS: do tre (giam -> muot hon, ton CPU hon). QUALITY 1-100.
    # MAX_WIDTH: thu nho frame ve be rong nay (px) cho nhe bang thong.
    SCREEN_STREAM_ENABLED: bool = True
    SCREEN_STREAM_INTERVAL_MS: int = 500
    SCREEN_STREAM_JPEG_QUALITY: int = 55
    SCREEN_STREAM_MAX_WIDTH: int = 720

    # Cấu hình đọc từ file .env (nếu có)
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    BROWSER_EXTENSIONS_DIR: str = str(BASE_DIR / "extensions")
    OMOCAPTCHA_KEY: str = "OMO_PRPNYKMWZKGSOXG4WE5UITKTPE6NN5LVNDXWZ5YVB2WW7WTZXXDNAEFIJMTIJY1764562155"
    OMOCAPTCHA_MASTER_PROFILE_DIR: str = "D:/tiktok_auto/profiles/master_omocaptcha"

# Khởi tạo một thực thể Singleton duy nhất dùng chung cho toàn bộ dự án
settings = Settings()
