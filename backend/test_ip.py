import asyncio
import json
import os
from sqlmodel import Session, select
from invisible_playwright.async_api import InvisiblePlaywright

# Nhập cấu hình và kết nối cơ sở dữ liệu từ dự án của bạn
from app.infrastructure.database.connection import engine
from app.infrastructure.database.schemas import ProxyDbTable

async def test_proxy_ip():
    print("[*] Đang truy vấn danh sách Proxy từ cơ sở dữ liệu SQLite...")
    
    # 1. Lấy thông tin Proxy đầu tiên trong Database để làm mẫu kiểm tra
    proxy_config = None
    with Session(engine) as session:
        statement = select(ProxyDbTable)
        db_proxy = session.exec(statement).first()
        if db_proxy:
            print(f"[+] Tìm thấy Proxy trong cơ sở dữ liệu:")
            print(f"    - Giao thức: {db_proxy.protocol}")
            print(f"    - Host/IP : {db_proxy.host}")
            print(f"    - Cổng    : {db_proxy.port}")
            print(f"    - Tài khoản: {db_proxy.username if db_proxy.username else 'Không yêu cầu'}")
            
            # Khởi tạo dải cấu hình chuẩn của Playwright
            proxy_config = {
                "server": f"{db_proxy.protocol}://{db_proxy.host}:{db_proxy.port}"
            }
            if db_proxy.username and db_proxy.password:
                proxy_config["username"] = db_proxy.username
                proxy_config["password"] = db_proxy.password
        else:
            print("[!] CẢNH BÁO: Cơ sở dữ liệu trống, không có Proxy nào để kiểm tra.")
            print("[!] Robot sẽ chạy bằng IP mạng thật (Direct) để kiểm tra kết nối.")

    # Ép hệ thống đồ họa Linux X11/Mesa phải render bằng CPU (Tránh nhiễu hạt cát)
    os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
    os.environ["MOZ_WEBRENDER"] = "0"
    os.environ["MOZ_ACCELERATED"] = "0"

    print("[*] Đang khởi động trình duyệt tàng hình (Invisible Firefox-13)...")
    try:
        async with InvisiblePlaywright(proxy=proxy_config, headless=False) as browser:
            print("[+] Khởi động trình duyệt thành công!")
            page = await browser.new_page()

            # BƯỚC 1: Quét IP thô từ api.ipify.org
            print("[*] Đang kiểm tra IP thô từ api.ipify.org...")
            await page.goto("https://api.ipify.org?format=json", wait_until="domcontentloaded")
            body_text = await page.locator("body").text_content()
            try:
                ip_data = json.loads(body_text)
                print(f"[+] Địa chỉ IP thô ghi nhận: {ip_data.get('ip')}")
            except Exception:
                print(f"[!] Không thể parse JSON từ ipify. Nội dung thô: {body_text}")

            # BƯỚC 2: Quét chi tiết Quốc gia, Thành phố, Nhà mạng từ ipinfo.io
            print("[*] Đang truy vấn thông tin định vị chi tiết từ ipinfo.io...")
            await page.goto("https://ipinfo.io/json", wait_until="domcontentloaded")
            body_text_detailed = await page.locator("body").text_content()
            try:
                geo_data = json.loads(body_text_detailed)
                print("[+] THÔNG TIN ĐỊNH VỊ CHI TIẾT GHI NHẬN:")
                print(f"    - IP Ngoại vi      : {geo_data.get('ip')}")
                print(f"    - Quốc gia (Country): {geo_data.get('country')}")
                print(f"    - Thành phố (City)  : {geo_data.get('city')}")
                print(f"    - Nhà mạng (Org)    : {geo_data.get('org')}")
                print(f"    - Tọa độ (Loc)      : {geo_data.get('loc')}")
            except Exception:
                print(f"[!] Không thể parse JSON từ ipinfo. Nội dung thô: {body_text_detailed}")

            # BƯỚC 3: Đi tới Whoer.net để kiểm tra độ tàng hình trực quan
            print("[*] Đang điều hướng tới Whoer.net...")
            await page.goto("https://whoer.net", wait_until="domcontentloaded")
            print("[*] Đang giữ màn hình Whoer.net trong 15 giây để bạn quan sát trực quan...")
            await asyncio.sleep(15)

            print("[+] Kiểm tra hoàn tất. Đang giải phóng tài nguyên...")
            
    except Exception as e:
        print(f"[-] Gặp lỗi trong quá trình kiểm tra IP: {str(e)}")

if __name__ == "__main__":
    # Đăng ký chạy vòng lặp bất đồng bộ của Python
    asyncio.run(test_proxy_ip())