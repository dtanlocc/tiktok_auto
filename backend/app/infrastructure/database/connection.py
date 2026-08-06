from sqlmodel import create_engine, SQLModel, Session
from sqlalchemy import text, event  # text() thực thi SQL thô; event để gắn PRAGMA
from app.core.config import settings

# SQLite yêu cầu cấu hình check_same_thread=False khi sử dụng đa luồng (multi-threading/asyncio)
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 10},
    echo=False  # Đặt thành True nếu bạn muốn in log câu lệnh SQL ra terminal
)


# =============================================================================
# TỐI ƯU ĐA LUỒNG: BẬT WAL cho SQLite trên MỖI kết nối
# =============================================================================
# Mặc định SQLite dùng rollback-journal: 1 writer KHOÁ toàn bộ DB, reader cũng
# bị chặn -> khi N luồng đồng thời cập nhật trạng thái/step liên tục sẽ nghẽn +
# "database is locked". WAL (Write-Ahead Log): reader KHÔNG chặn writer và ngược
# lại -> đồng thời mượt hơn hẳn. synchronous=NORMAL (an toàn với WAL, nhanh hơn
# FULL). busy_timeout=5000: khi gặp khoá thì CHỜ 5s thay vì lỗi ngay.
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cur = dbapi_connection.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA temp_store=MEMORY")
    finally:
        cur.close()

# [Cập nhật trong hàm init_db của connection.py]
def init_db() -> None:
    """Khởi tạo toàn bộ các bảng trong Database (nếu chưa tồn tại)"""
    SQLModel.metadata.create_all(engine)

    # TỰ ĐỘNG DI CƯ THÊM 3 CỘT QUỐC GIA, PHÂN LÔ VÀ NGÀY TẠO
    try:
        with Session(engine) as session:
            result = session.execute(text("PRAGMA table_info(accounts)")).fetchall()
            existing_columns = [row[1] for row in result]
            
            if "health_status" not in existing_columns:
                session.execute(text("ALTER TABLE accounts ADD COLUMN health_status VARCHAR DEFAULT 'ALIVE'"))
                session.commit()
            if "profile_status" not in existing_columns:
                session.execute(text("ALTER TABLE accounts ADD COLUMN profile_status VARCHAR DEFAULT 'PENDING'"))
                session.commit()
                
            # --- CÁC CỘT PHÂN LÔ MỚI ---
            if "country" not in existing_columns:
                session.execute(text("ALTER TABLE accounts ADD COLUMN country VARCHAR DEFAULT 'US'"))
                session.commit()
                print("[+] Tự động di cư thêm cột 'country' thành công!")
                
            if "batch_tag" not in existing_columns:
                session.execute(text("ALTER TABLE accounts ADD COLUMN batch_tag VARCHAR DEFAULT 'DEFAULT'"))
                session.commit()
                print("[+] Tự động di cư thêm cột 'batch_tag' thành công!")
                
            if "created_at" not in existing_columns:
                session.execute(text("ALTER TABLE accounts ADD COLUMN created_at VARCHAR DEFAULT ''"))
                session.commit()
                print("[+] Tự động di cư thêm cột 'created_at' thành công!")

            if "note" not in existing_columns:
                session.execute(text("ALTER TABLE accounts ADD COLUMN note VARCHAR DEFAULT ''"))
                session.commit()
                print("[+] Tự động di cư thêm cột 'note' thành công!")
                
    except Exception as migration_err:
        print(f"[-] Cảnh báo lỗi tiến trình di cư tự động: {str(migration_err)}")


def get_db_session():
    """Generator cung cấp database session độc lập cho từng luồng hoặc request"""
    with Session(engine) as session:
        yield session