import sqlite3
from datetime import datetime
from pathlib import Path

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
def _migrate_accounts_primary_key_to_email() -> None:
    """One-time, lossless SQLite rebuild: UUID `id` -> case-insensitive email PK."""
    prefix = "sqlite:///"
    if not settings.DATABASE_URL.startswith(prefix):
        return
    database_path = Path(settings.DATABASE_URL[len(prefix):])
    if not database_path.exists():
        return

    with sqlite3.connect(database_path) as connection:
        columns = connection.execute("PRAGMA table_info(accounts)").fetchall()
        if not columns:
            return
        primary_key = next((row[1] for row in columns if row[5] == 1), None)
        if primary_key == "email":
            return
        if primary_key != "id":
            raise RuntimeError(f"Unsupported accounts primary key: {primary_key}")

        duplicate = connection.execute(
            """
            SELECT lower(trim(email)), COUNT(*) FROM accounts
            WHERE email IS NOT NULL AND trim(email) <> ''
            GROUP BY lower(trim(email)) HAVING COUNT(*) > 1 LIMIT 1
            """
        ).fetchone()
        if duplicate:
            raise RuntimeError("Cannot migrate accounts: duplicate email values exist.")

        backup_path = database_path.with_name(
            f"{database_path.stem}.before_email_pk_{datetime.now().strftime('%Y%m%d_%H%M%S')}{database_path.suffix}"
        )
        with sqlite3.connect(backup_path) as backup:
            connection.backup(backup)

        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE accounts_email_pk (
                    email VARCHAR COLLATE NOCASE PRIMARY KEY NOT NULL,
                    username VARCHAR NOT NULL UNIQUE,
                    password VARCHAR, email_password VARCHAR, refresh_token VARCHAR,
                    client_id VARCHAR, cookies_json VARCHAR NOT NULL DEFAULT '[]',
                    status VARCHAR NOT NULL DEFAULT 'IDLE',
                    current_step VARCHAR NOT NULL DEFAULT 'Chua kich hoat',
                    proxy_id VARCHAR REFERENCES proxies(id),
                    health_status VARCHAR DEFAULT 'UNKNOWN',
                    profile_status VARCHAR DEFAULT 'PENDING',
                    country VARCHAR DEFAULT 'US', batch_tag VARCHAR DEFAULT 'DEFAULT',
                    created_at VARCHAR DEFAULT '', note VARCHAR DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                INSERT INTO accounts_email_pk (
                    email, username, password, email_password, refresh_token, client_id,
                    cookies_json, status, current_step, proxy_id, health_status,
                    profile_status, country, batch_tag, created_at, note
                )
                SELECT lower(CASE
                    WHEN email IS NULL OR trim(email) = ''
                    THEN 'legacy+' || lower(id) || '@local.invalid'
                    ELSE trim(email) END),
                    username, password, email_password, refresh_token, client_id,
                    cookies_json, status, current_step, proxy_id, health_status,
                    profile_status, country, batch_tag, created_at, note
                FROM accounts
                """
            )
            connection.execute("DROP TABLE accounts")
            connection.execute("ALTER TABLE accounts_email_pk RENAME TO accounts")
            connection.execute("CREATE INDEX ix_accounts_country ON accounts(country)")
            connection.execute("CREATE INDEX ix_accounts_batch_tag ON accounts(batch_tag)")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")


def init_db() -> None:
    """Khởi tạo toàn bộ các bảng trong Database (nếu chưa tồn tại)"""
    _migrate_accounts_primary_key_to_email()
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

            # Dữ liệu hiệu suất đăng và snapshot thống kê TikTok. Các chỉ số
            # chưa thu thập dùng NULL để UI phân biệt với giá trị thật bằng 0.
            performance_columns = {
                "upload_success_count": "INTEGER NOT NULL DEFAULT 0",
                "upload_failure_count": "INTEGER NOT NULL DEFAULT 0",
                "last_upload_status": "VARCHAR NOT NULL DEFAULT 'NEVER'",
                "last_upload_at": "VARCHAR NOT NULL DEFAULT ''",
                "last_upload_error": "VARCHAR NOT NULL DEFAULT ''",
                "video_count": "INTEGER",
                "follower_count": "INTEGER",
                "following_count": "INTEGER",
                "likes_count": "INTEGER",
                "tiktok_user_id": "VARCHAR NOT NULL DEFAULT ''",
                "tiktok_sec_uid": "VARCHAR NOT NULL DEFAULT ''",
                "display_name": "VARCHAR NOT NULL DEFAULT ''",
                "bio": "VARCHAR NOT NULL DEFAULT ''",
                "avatar_url": "VARCHAR NOT NULL DEFAULT ''",
                "verified": "BOOLEAN NOT NULL DEFAULT 0",
                "private_account": "BOOLEAN NOT NULL DEFAULT 0",
                "website_url": "VARCHAR NOT NULL DEFAULT ''",
                "total_views": "INTEGER",
                "total_video_likes": "INTEGER",
                "total_comments": "INTEGER",
                "total_shares": "INTEGER",
                "collected_video_count": "INTEGER NOT NULL DEFAULT 0",
                "analytics_sync_status": "VARCHAR NOT NULL DEFAULT 'NEVER'",
                "analytics_sync_source": "VARCHAR NOT NULL DEFAULT ''",
                "analytics_sync_error": "VARCHAR NOT NULL DEFAULT ''",
                "metrics_updated_at": "VARCHAR NOT NULL DEFAULT ''",
            }
            added_performance = []
            for column_name, column_sql in performance_columns.items():
                if column_name in existing_columns:
                    continue
                session.execute(text(
                    f"ALTER TABLE accounts ADD COLUMN {column_name} {column_sql}"
                ))
                added_performance.append(column_name)
            if added_performance:
                session.commit()
                print(f"[+] Added account performance columns: {', '.join(added_performance)}")
                
    except Exception as migration_err:
        print(f"[-] Automatic database migration warning: {str(migration_err)}")


def get_db_session():
    """Generator cung cấp database session độc lập cho từng luồng hoặc request"""
    with Session(engine) as session:
        yield session
