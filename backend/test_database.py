import os
from app.infrastructure.database.connection import init_db, engine, Session
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository, SQLiteProxyRepository
from app.domain.entities.account import TikTokAccount
from app.domain.entities.proxy import Proxy

def run_db_test():
    print("[*] Đang khởi tạo cơ sở dữ liệu...")
    init_db()

    # Sử dụng Session cục bộ phục vụ kiểm thử
    with Session(engine) as session:
        # Khởi tạo các Adapters
        account_repo = SQLiteAccountRepository(session)
        proxy_repo = SQLiteProxyRepository(session)

        print("[*] Thử nghiệm tạo và lưu Proxy mới...")
        test_proxy = Proxy(
            id="proxy-1",
            host="127.0.0.1",
            port=8080,
            username="admin",
            password="password123",
            protocol="socks5"
        )
        saved_proxy = proxy_repo.save(test_proxy)
        print(f"[+] Đã lưu Proxy: ID = {saved_proxy.id} | Connection = {saved_proxy.connection_string}")

        print("[*] Thử nghiệm tạo và lưu TikTok Account mới...")
        test_account = TikTokAccount(
            id="acc-1",
            username="tiktok_developer_test",
            password="secure_password",
            cookies=[{"name": "session_id", "value": "xyz123"}],
            status="IDLE",
            proxy_id=saved_proxy.id
        )
        saved_account = account_repo.save(test_account)
        print(f"[+] Đã lưu Account: ID = {saved_account.id} | Username = {saved_account.username}")

        # Thử nghiệm truy vấn dữ liệu
        print("[*] Thử nghiệm truy vấn lại từ Database...")
        fetched_account = account_repo.get_by_id("acc-1")
        if fetched_account:
            print(f"[+] Lấy dữ liệu thành công!")
            print(f"    - Username: {fetched_account.username}")
            print(f"    - Status: {fetched_account.status}")
            print(f"    - Cookies: {fetched_account.cookies}")
            print(f"    - Proxy ID: {fetched_account.proxy_id}")
        else:
            print("[-] Lỗi: Không tìm thấy tài khoản đã lưu.")

if __name__ == "__main__":
    run_db_test()