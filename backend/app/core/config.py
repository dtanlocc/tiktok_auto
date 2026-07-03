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

    BROWSER_HEADLESS: bool = False
    
    # Cấu hình đọc từ file .env (nếu có)
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Khởi tạo một thực thể Singleton duy nhất dùng chung cho toàn bộ dự án
settings = Settings()
