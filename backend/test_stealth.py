import asyncio
# Import trực tiếp lớp wrapper InvisiblePlaywright của thư viện
from invisible_playwright.async_api import InvisiblePlaywright

async def run_test():
    print("[*] Đang khởi động trình duyệt tàng hình (Invisible Firefox-13)...")
    try:
        # InvisiblePlaywright tự động cấu hình và gọi bản vá C++ đã tải trong cache
        # Mặc định headless=False. Nếu chạy trên VPS/Server không có GUI, hãy đặt headless=True
        async with InvisiblePlaywright(headless=False) as browser:
            print("[+] Khởi động trình duyệt thành công!")
            
            # Tạo một trang mới
            page = await browser.new_page()
            
            print("[*] Đang chuyển hướng tới trang kiểm tra bot (sannysoft)...")
            await page.goto("https://bot.sannysoft.com/", wait_until="domcontentloaded")
            
            # Chờ 5 giây để bạn quan sát trực tiếp trên màn hình Pop!_OS
            print("[*] Đang hiển thị trình duyệt trong 5 giây...")
            await asyncio.sleep(5)
            
            print("[+] Kiểm tra thành công! Đang đóng trình duyệt...")
            
    except Exception as e:
        print(f"[-] Lỗi phát sinh: {str(e)}")
        print("[-] Gợi ý: Nếu bạn đang chạy trên Linux headless hoặc VPS, hãy đảm bảo đã cài đặt đầy đủ thư viện đồ họa hệ thống.")

if __name__ == "__main__":
    asyncio.run(run_test())
