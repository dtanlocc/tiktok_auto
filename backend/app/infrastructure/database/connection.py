import sqlite3
from datetime import datetime
from pathlib import Path

from sqlmodel import create_engine, SQLModel, Session
from sqlalchemy import text, event  # text() thực thi SQL thô; event để gắn PRAGMA
from app.core.config import settings

# ⛔ `check_same_thread` VÀ `timeout` LÀ THAM SỐ RIÊNG CỦA SQLITE. Truyền chúng
# cho Postgres thì driver từ chối ngay khi mở kết nối đầu tiên, nên dòng này
# từng là chỗ chặn Postgres sớm nhất trong cả dự án - trước cả chuyện migration.
# Cùng lý do với hook PRAGMA bên dưới và khối di cư trong init_db().
_IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")
_CONNECT_ARGS = (
    {"check_same_thread": False, "timeout": 10} if _IS_SQLITE else {}
)
engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_CONNECT_ARGS,
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
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cur = dbapi_connection.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA temp_store=MEMORY")
    finally:
        cur.close()


# Gắn có điều kiện, không phải bằng decorator: PRAGMA là cú pháp SQLite, và một
# listener gắn sẵn sẽ chạy trên MỌI kết nối - kể cả Postgres, nơi câu đầu tiên
# đã là lỗi cú pháp. WAL ở trên vẫn cần thiết nguyên vẹn cho bản chạy local.
if _IS_SQLITE:
    event.listens_for(engine, "connect")(_set_sqlite_pragma)

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
    """Khởi tạo toàn bộ các bảng trong Database (nếu chưa tồn tại).

    Thứ tự ba bước dưới đây không đổi được:

    1. `_legacy_sqlite_catch_up()` đưa một DB CŨ lên đúng hình dạng baseline.
    2. `create_all` tạo những bảng còn thiếu.
    3. `_align_schema_version()` đóng dấu baseline rồi nâng theo alembic.

    ⛔ ĐÓNG DẤU TRƯỚC KHI CATCH-UP LÀ MỘT LỜI NÓI DỐI VỚI ALEMBIC: nó sẽ tin
    rằng DB đó đã có mọi cột của baseline, trong khi một bản cài cũ thì chưa, và
    mọi migration sau đó chạy trên một schema không như nó tưởng.
    """
    # ⛔ IMPORT VÌ TÁC DỤNG PHỤ, KHÔNG PHẢI VÌ CẦN TÊN NÀO. `SQLModel.metadata`
    # chỉ có bảng khi module khai báo chúng đã được import; nếu chưa,
    # `create_all` bên dưới tạo ĐÚNG KHÔNG BẢNG NÀO và không báo lỗi gì cả.
    # Đo ngày 05/10/2026: gọi init_db() trên một DB trắng mà chưa import
    # schemas thì DB thu được chỉ có bảng `alembic_version`. Trước đây app
    # sống được nhờ main.py tình cờ import theo thứ tự đúng - tức hàm này dựa
    # vào một điều nó không tự bảo đảm.
    from app.infrastructure.database import schemas  # noqa: F401
    from sqlalchemy import inspect as _inspect

    # ⛔ ĐẾM BẢNG TRƯỚC `create_all`, VÌ SAU ĐÓ KHÔNG PHÂN BIỆT ĐƯỢC NỮA. Một DB
    # trắng được `create_all` dựng theo hình dạng MODEL HIỆN TẠI - tức đã có mọi
    # cột mà các migration sau baseline thêm vào. Đóng dấu nó ở baseline rồi
    # upgrade là đi thêm lại những cột đã có: đo ngày 06/10/2026, ngay khi
    # revision group_id xuất hiện thì DB mới chết ở
    # `ALTER TABLE accounts ADD COLUMN group_id` (trùng cột).
    #
    # Lỗi này chỉ lộ ra khi có migration thật THỨ HAI - nên nếu hôm nay không
    # thêm cột nào, nó sẽ nổ ở bản cài của một khách hàng nào đó về sau.
    _tables_before = set(_inspect(engine).get_table_names()) - {"alembic_version"}

    _migrate_accounts_primary_key_to_email()
    SQLModel.metadata.create_all(engine)
    _legacy_sqlite_catch_up()
    _align_schema_version(brand_new=not _tables_before)


def _legacy_sqlite_catch_up() -> None:
    """Hệ di cư cũ: dò bằng PRAGMA rồi ALTER TABLE ADD COLUMN.

    ⛔ GIỮ LẠI, KHÔNG XOÁ, DÙ ALEMBIC ĐÃ VÀO. Đây là thứ duy nhất đưa được một
    DB của bản cài cũ lên đúng baseline, và những DB đó đang tồn tại thật -
    bản đo trên máy này ngày 05/10/2026 có 1.974 account. Xoá nó cùng lúc với
    việc thêm alembic sẽ làm hỏng đúng những bản cài chưa kịp cập nhật.

    Chỉ chạy cho SQLite: PRAGMA là cú pháp riêng của nó. Postgres thì bắt đầu
    từ `create_all`, nên không có gì để catch-up.
    """
    if not _IS_SQLITE:
        return

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

            # Proxy management: label/note, on-off switch and last health check.
            proxy_result = session.execute(text("PRAGMA table_info(proxies)")).fetchall()
            proxy_existing = [row[1] for row in proxy_result]
            proxy_columns = {
                "label": "VARCHAR NOT NULL DEFAULT ''",
                "note": "VARCHAR NOT NULL DEFAULT ''",
                "enabled": "BOOLEAN NOT NULL DEFAULT 1",
                "created_at": "VARCHAR NOT NULL DEFAULT ''",
                "check_status": "VARCHAR NOT NULL DEFAULT 'UNCHECKED'",
                "check_error": "VARCHAR NOT NULL DEFAULT ''",
                "checked_at": "VARCHAR NOT NULL DEFAULT ''",
                "exit_ip": "VARCHAR NOT NULL DEFAULT ''",
                "country": "VARCHAR NOT NULL DEFAULT ''",
                "latency_ms": "INTEGER",
                "tiktok_ok": "BOOLEAN",
                "cdn_ok": "BOOLEAN",
            }
            added_proxy_columns = []
            for column_name, column_sql in proxy_columns.items():
                if column_name in proxy_existing:
                    continue
                session.execute(text(
                    f"ALTER TABLE proxies ADD COLUMN {column_name} {column_sql}"
                ))
                added_proxy_columns.append(column_name)
            if added_proxy_columns:
                session.commit()
                print(f"[+] Added proxy management columns: {', '.join(added_proxy_columns)}")

            # Rich public-video diagnostics collected from each exact
            # tiktok.com/@username/video/{id} page. Nullable counters keep
            # "TikTok did not expose this" distinct from a genuine zero.
            video_result = session.execute(
                text("PRAGMA table_info(tiktok_video_metrics)")
            ).fetchall()
            video_columns = [row[1] for row in video_result]
            video_detail_columns = {
                "favorite_count": "INTEGER",
                "repost_count": "INTEGER",
                "download_count": "INTEGER",
                "duration_seconds": "INTEGER",
                "max_quality": "VARCHAR NOT NULL DEFAULT ''",
                "detail_source": "VARCHAR NOT NULL DEFAULT ''",
                "region": "VARCHAR NOT NULL DEFAULT ''",
                "shadow_ban": "VARCHAR NOT NULL DEFAULT 'UNKNOWN'",
                "shadow_ban_reason": "VARCHAR NOT NULL DEFAULT ''",
                "index_enabled": "BOOLEAN",
                "is_reviewing": "BOOLEAN NOT NULL DEFAULT 0",
                "is_private": "BOOLEAN NOT NULL DEFAULT 0",
                "is_taken_down": "BOOLEAN NOT NULL DEFAULT 0",
                "detail_synced_at": "VARCHAR NOT NULL DEFAULT ''",
            }
            added_video_details = []
            for column_name, column_sql in video_detail_columns.items():
                if column_name in video_columns:
                    continue
                session.execute(text(
                    f"ALTER TABLE tiktok_video_metrics ADD COLUMN {column_name} {column_sql}"
                ))
                added_video_details.append(column_name)
            if added_video_details:
                session.commit()
                print(
                    "[+] Added TikTok video detail columns: "
                    + ", ".join(added_video_details)
                )
                
    except Exception as migration_err:
        print(f"[-] Automatic database migration warning: {str(migration_err)}")


def _alembic_config():
    """Alembic đọc file revision từ ĐĨA, nên đường dẫn phải đúng cả khi đóng gói.

    ⛔ ĐÂY LÀ DỮ LIỆU CHỈ ĐỌC CỦA CHƯƠNG TRÌNH, nên tra theo `__file__` là ĐÚNG -
    khác hẳn `bios.txt`. Bản onefile giải nén dữ liệu kèm theo vào thư mục tạm
    rồi xoá khi thoát; với thứ người dùng phải sửa được thì như vậy là vô dụng
    (xem `_bios_file_path`), nhưng với file revision thì đó chính xác là điều
    mình muốn: chúng đi cùng bản build và không ai sửa chúng lúc chạy.

    ⛔ NHƯNG PHẢI ĐƯỢC ĐÓNG VÀO GÓI. Alembic không `import` các file này nên
    Nuitka không tự thấy chúng; build script phải có
    `--include-data-dir=<backend/migrations>=migrations`. Thiếu nó thì bản .exe
    sẽ chạy đến đây rồi báo không tìm thấy thư mục script.
    """
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parents[3]
    config = Config()
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    return config


def _already_at_model_shape() -> bool:
    """DB đã có đủ mọi cột mà model khai báo chưa?

    ⛔ TRẠNG THÁI NÀY LÀ THẬT, KHÔNG PHẢI GIẢ ĐỊNH PHÒNG XA. Một bản `cp` của
    `database.db` mà bỏ file `-wal` cho ra đúng nó: schema mới nguyên nhưng
    **không có bảng `alembic_version`**, vì bảng đó vừa được tạo và còn nằm
    trong WAL. Gặp ngày 06/10/2026, trên chính máy này, bằng chính một lệnh `cp`
    của tôi. Đóng dấu nó ở baseline rồi upgrade sẽ đi thêm lại những cột đã có
    và chết ở `duplicate column name`.

    So theo TÊN CỘT, không so NOT NULL hay index. Dự án đang có lệch sẵn ở hai
    thứ đó (model khai `str` trong khi hệ di cư cũ thêm cột nullable), và lệch
    đó cố ý chưa sửa - nếu tính vào đây thì mọi DB đều bị coi là chưa tới head.
    Với migration chỉ-thêm, tên cột là đủ để trả lời câu hỏi này.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    live = set(inspector.get_table_names())
    for name, table in SQLModel.metadata.tables.items():
        if name not in live:
            return False
        have = {column["name"] for column in inspector.get_columns(name)}
        if not {column.name for column in table.columns} <= have:
            return False
    return True


def _align_schema_version(*, brand_new: bool) -> None:
    """Đưa DB về đúng phiên bản schema mà alembic biết, theo ba trường hợp.

    1. **Đã có `alembic_version`**: alembic đang nắm rồi, chỉ cần `upgrade head`.
    2. **DB vừa được tạo trắng** (`brand_new`): `create_all` đã dựng nó theo
       model hiện tại, nên nó ĐÃ ở head. Đóng dấu **head** - không phải
       baseline, xem lý do trong `init_db()`.
    3. **DB có từ trước khi alembic vào**: nó ở hình dạng baseline, đã qua
       catch-up. Đóng dấu **baseline** rồi `upgrade head` để chạy đúng những
       migration nó còn thiếu.

    Lỗi ở đây KHÔNG làm app chết. Trước khi có alembic app vẫn chạy; biến nó
    thành điều kiện sống còn ngay trong commit giới thiệu nó là tự tạo thêm một
    cách để không mở được máy. Lỗi được in ra và đi tiếp.
    """
    try:
        from alembic import command
        from sqlalchemy import inspect

        config = _alembic_config()
        if "alembic_version" in inspect(engine).get_table_names():
            command.upgrade(config, "head")
            return
        if brand_new or _already_at_model_shape():
            command.stamp(config, "head")
            return
        command.stamp(config, "0001_baseline")
        command.upgrade(config, "head")
    except Exception as schema_err:
        print(f"[-] Schema version alignment warning: {schema_err}")


def get_db_session():
    """Generator cung cấp database session độc lập cho từng luồng hoặc request"""
    with Session(engine) as session:
        yield session
