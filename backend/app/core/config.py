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
    
    MAX_CONCURRENT_TABS: int = 4

    # Gia tri khoi tao cua bo dieu khien UI: moi proxy khi USE_PROXY=True, hoac tong
    # so phien khi USE_PROXY=False. Day khong phai hard cap; co the doi luc dang chay.
    PROXY_MAX_CONCURRENT: int = 4

    # DUNG PROXY hay khong. False -> KHONG gan proxy, moi phien chay TRUC TIEP qua
    # mang that cua may (dung khi bat VPN TOAN MAY). True -> gan proxy can bang nhu
    # cu. (Du lieu proxy trong DB van GIU nguyen, chi khong dung.)
    USE_PROXY: bool = False

    # GIAN CACH (stagger) giua cac lan mo browser lien tiep - tranh mo 4 browser
    # cung luc (thrash dia/CPU + de bi TikTok phat hien nhieu nick 1 IP cung luc).
    # Giam de ramp-up da luong NHANH hon (rui ro phat hien cao hon); tang de an toan.
    STAGGER_MIN_SECONDS: float = 30.0
    STAGGER_MAX_SECONDS: float = 60.0

    # GIAN CACH khi KHONG dung proxy (USE_PROXY=False, chay thang qua mang/VPN).
    # Che do nay TRUOC DAY dung chung 30-60s -> moi luong bi NOI DUOI nhau (dat 4
    # luong nhung chay gan nhu tuan tu). Chi can giãn NGAN de khong mo dồn browser
    # cung luc; so luong song song da do o "số luồng" tren UI khong che.
    STAGGER_DIRECT_MIN_SECONDS: float = 3.0
    STAGGER_DIRECT_MAX_SECONDS: float = 8.0

    # Dung che do headed-cloaked CHINH THUC cua invisible_playwright: Firefox van
    # chay renderer/GPU headed binh thuong, nhung patched binary tu DWMWA_CLOAK
    # chrome window tren Windows. Khong con day mot cua so that ra ngoai man hinh.
    BROWSER_HEADLESS: bool = True

    # False = giu headed renderer + cloak. True moi la native Firefox headless.
    # Khong bat true-headless cho upload: renderer khac va khong co HWND stream.
    BROWSER_TRUE_HEADLESS: bool = False

    # TikTok Studio does not consistently honor ``?lang=en``.  The browser
    # locale is the authoritative input for navigator.language and the
    # Accept-Language request header, so every product session uses English US
    # even when the proxy exits from another country.
    TIKTOK_WEB_LOCALE: str = "en-US"

    # AN CUA SO BANG CACH DAY RA NGOAI MAN HINH (thay cho cloak/headless).
    # Cua so van "shown" nen VAN RENDER + chay duoc du KHONG duoc focus (khac
    # minimize/cloak la ngung render -> treo cho tha luong / phai click de kich hoat).
    # Ban khong thay cua so, khong bi che man hinh, va PrintWindow van chup stream
    # duoc. Dat FALSE neu muon NHIN truc tiep cua so (may that, khong qua RDP).
    HIDE_BROWSER_OFFSCREEN: bool = True

    # =========================================================================
    # SO LAN MO TRINH DUYET DUOC PHEP CHONG NHAU (cong launch)
    # =========================================================================
    # ===================== SO DO THUC TE 25/08/2026, 6 phien =====================
    #   so launch chong nhau  ->  thoi gian MOI launch
    #        1                ->   8.7s
    #        2                ->  12.5s - 19.2s
    #        3                ->  23.2s
    #        6                ->  32.6s - 53.3s
    #
    # NGUYEN NHAN (da profile, khong phai suy doan): mot phan cua initialize() CHEN
    # HAN event loop. cProfile 1 lan launch: psutil_windows.proc_environ 973 loi goi
    # = 2.96s CPU THUAN tren main thread. Do la LifetimeGuard.bind() cua thu vien -
    # no quet MOI process tren may va doc bien moi truong tung cai de nhan dien cay
    # process cua phien (co che nay chinh la thu ngan RO RI process, khong nen tat).
    # Do nhip tim 0.2s trong luc mo 3 phien: chi 23/122 nhip chay, khoang trong lon
    # nhat 13.7s -> trong luc launch thi WEBSOCKET, STREAM, API va ca dong ho
    # timeout deu DONG BANG. Vi bi chen nen cac launch KHONG that su chay song song:
    # mo dong loat chi lam MOI launch cham gap N lan chu khong xong som hon.
    #
    # VI SAO CHAN: khong phai de "nhanh hon" - do thuc te cho thay wall-time gan nhu
    # KHONG doi (nut that la thong luong launch cua may). Chan de:
    #   1. Moi launch giu duoc do tre thap (7.8-16.0s thay vi 32.6-53.3s) -> con
    #      bien an toan lon so voi BROWSER_LAUNCH_TIMEOUT.
    #   2. Gioi han so doan chen loop chong nhau -> dashboard do it giat hon.
    # Do thuc te khong tach bach ro giua 1 va 2; de 2 de van co song song o phan
    # cho I/O. Ha ve 1 neu muon do tre tung launch thap nhat.
    # Day CHI gioi han luc MO; mo xong roi ca 8 luong chay song song binh thuong.
    BROWSER_LAUNCH_GATE: int = 2

    # BUDGET (giay) cho 1 lan MO trinh duyet: qua han thi HUY + THU LAI thay vi cho
    # het 180s cua playwright. Phai LON HON HAN thoi gian mo that o muc GATE o tren
    # (gate=2 -> do duoc toi da 20.3s) de khong cat nham launch tot; launch HONG thi
    # treo vinh vien nen van bi cat. 25s cu qua sat 20.3s -> nang len 45s.
    #
    # LUU Y QUAN TRONG: dong ho nay KHONG dang tin cay tuyet doi. Vi initialize()
    # co doan CHEN event loop (xem BROWSER_LAUNCH_GATE), timer cua asyncio.wait_for
    # khong chay duoc trong luc bi chen. Da do: dat timeout=25s ma launch ton 32.6s
    # VAN bao "OK sau 32.6s (lan 1/2)" - khong he bi cat. Nen coi day la luoi bat
    # launch TREO HAN (treo vinh vien thi loop ranh, timer chay duoc), dung trong cay
    # no de canh gio chinh xac.
    BROWSER_LAUNCH_TIMEOUT: int = 45
    # Chi la luoi an toan cho launch hi huu bi treo -> 2 la du.
    BROWSER_LAUNCH_MAX_TRIES: int = 2

    # Mot upload binh thuong (login + tai file + TikTok xu ly + xac minh Posts)
    # thuong xong trong 2-6 phut. Cat session qua 10 phut de browser/driver da
    # chet khong giu slot dispatcher vo han; task khac van tiep tuc binh thuong.
    UPLOAD_TASK_TIMEOUT_SECONDS: int = 600

    # =========================================================================
    # STREAM ANH MAN HINH TRINH DUYET VE DASHBOARD (xem da luong truc tiep)
    # =========================================================================
    # Chup dinh ky moi trinh duyet va day ve UI qua WebSocket (event BROWSER_FRAME).
    # True-headless khong co HWND, nen doc frame tu page.screenshot va phat qua
    # kenh /ws/screens rieng, khong anh huong WebSocket thong bao tac vu.
    # INTERVAL_MS: do tre (giam -> muot hon, ton CPU hon). QUALITY 1-100.
    # MAX_WIDTH: thu nho frame ve be rong nay (px) cho nhe bang thong.
    SCREEN_STREAM_ENABLED: bool = True
    # 400ms ~= 2.5 FPS: du muot de theo doi captcha/progress. Frame duoc chup
    # bang PrintWindow trong worker thread, khong dung kenh Playwright cua upload.
    SCREEN_STREAM_INTERVAL_MS: int = 400
    # Uu tien do ro de quan sat browser chay nen.
    SCREEN_STREAM_JPEG_QUALITY: int = 92
    SCREEN_STREAM_MAX_WIDTH: int = 1280

    # Neu caption/title chua co hashtag, thu toi da QUERY_LIMIT tu khoa rut ra tu
    # title va CHI giu toi da MAX hashtag khi TikTok Studio that su hien goi y.
    # Khong chen #fyp/#viral hay trend khong lien quan de tranh caption spam.
    AUTO_HASHTAGS_ENABLED: bool = True
    AUTO_HASHTAGS_MAX: int = 3
    AUTO_HASHTAG_QUERY_LIMIT: int = 6
    # Cấu hình đọc từ file .env (nếu có)
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    # Firefox extensions are attached dynamically to every fresh browser profile.
    # Drop one or more .xpi/.zip files (or unpacked directories with manifest.json)
    # in this directory. BROWSER_EXTENSION_PATHS, when set, overrides discovery and
    # accepts a semicolon-separated list of files/directories.
    BROWSER_EXTENSIONS_DIR: str = str(BASE_DIR / "extensions")
    BROWSER_EXTENSION_PATHS: str = ""
    BROWSER_EXTENSIONS_REQUIRED: bool = True
    # Generic optional configuration, supplied through .env. Shapes:
    #   BROWSER_EXTENSION_UUIDS_JSON={"addon@example.com":"uuid"}
    #   BROWSER_EXTENSION_JSON_OVERRIDES={"addon@example.com":{"config.json":{"key":"value"}}}
    BROWSER_EXTENSION_UUIDS_JSON: str = "{}"
    BROWSER_EXTENSION_JSON_OVERRIDES: str = "{}"
    # NGUON LAY OTP: 'graph' = tu goi Microsoft OAuth2 + Graph API (mac dinh, khong
    # phu thuoc ben thu 3); 'dongvan' = quay lai API trung gian dongvanfb.
    OTP_PROVIDER: str = "graph"

    # Public-profile analytics does not launch a browser. Repeated clicks inside
    # this window reuse the last successful snapshot instead of hammering TikTok.
    FAST_ANALYTICS_CACHE_TTL_SECONDS: int = 120
    FAST_ANALYTICS_FETCH_VIDEOS: bool = True
    FAST_ANALYTICS_MAX_VIDEOS_PER_ACCOUNT: int = 60

    OMOCAPTCHA_KEY: str = "OMO_PRPNYKMWZKGSOXG4WE5UITKTPE6NN5LVNDXWZ5YVB2WW7WTZXXDNAEFIJMTIJY1764562155"
    # Backward-compatible OmoCaptcha convenience. The generic extension loader
    # injects this key into configs.json when the OmoCaptcha addon is present.
    # Keep secrets in .env in production.
    # UUID ổn định dành riêng cho OmoCaptcha. Extension khác được cấp UUID v5
    # ổn định từ Gecko ID hoặc có thể override bằng JSON ở trên.
    OMOCAPTCHA_EXTENSION_UUID: str = "d6105ea0-8d34-41ab-85a7-2eb0c66d55bb"

# Khởi tạo một thực thể Singleton duy nhất dùng chung cho toàn bộ dự án
settings = Settings()
