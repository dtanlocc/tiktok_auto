from sqlmodel import create_engine, SQLModel, Session
from app.core.config import settings

# SQLite yêu cầu cấu hình check_same_thread=False khi sử dụng đa luồng (multi-threading/asyncio)
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False  # Đặt thành True nếu bạn muốn in log câu lệnh SQL ra terminal
)

def init_db() -> None:
    """Khởi tạo toàn bộ các bảng trong Database (nếu chưa tồn tại)"""
    SQLModel.metadata.create_all(engine)

def get_db_session():
    """Generator cung cấp database session độc lập cho từng luồng hoặc request"""
    with Session(engine) as session:
        yield session