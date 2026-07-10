import asyncio
import json
import os
from sqlmodel import Session, select
from invisible_playwright.async_api import InvisiblePlaywright

# Nhập cấu hình và kết nối cơ sở dữ liệu từ dự án của bạn
from app.infrastructure.database.connection import engine
from app.infrastructure.database.schemas import ProxyDbTable
xpi_path = r"D:\tiktok_auto\backend\extensions\omocaptcha_auto_solve_captcha-1.7.7.xpi"
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

    # # Ép hệ thống đồ họa Linux X11/Mesa phải render bằng CPU (Tránh nhiễu hạt cát)
    # os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
    # os.environ["MOZ_WEBRENDER"] = "0"
    # os.environ["MOZ_ACCELERATED"] = "0"

    print("[*] Đang khởi động trình duyệt tàng hình (Invisible Firefox-13)...")
    async with InvisiblePlaywright(proxy=proxy_config, headless=False, extra_args=[f"--install-extension={xpi_path}"]) as browser:
        print("="*50)
        print("DANH SÁCH CÁC THUỘC TÍNH & HÀM CỦA BROWSER:")
        print("="*50)
        
        # Sử dụng hàm dir() để quét sạch các phương thức nội bộ
        for attr in dir(browser):
            # Lọc bỏ các hàm hệ thống có dấu gạch dưới nếu muốn nhìn cho thoáng, hoặc in hết ra:
            try:
                val = getattr(browser, attr)
                print(f"-> {attr} : {type(val)}")
            except Exception:
                print(f"-> {attr} : (Không thể truy cập giá trị)")
                
        print("="*50)

        await asyncio.sleep(3000)

        print("[+] Kiểm tra hoàn tất. Đang giải phóng tài nguyên...")
            
    # except Exception as e:
    #     print(f"[-] Gặp lỗi trong quá trình kiểm tra IP: {str(e)}")

if __name__ == "__main__":
    # Đăng ký chạy vòng lặp bất đồng bộ của Python
    asyncio.run(test_proxy_ip())