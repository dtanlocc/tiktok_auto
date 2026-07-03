import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.infrastructure.database.connection import init_db
from app.infrastructure.websocket.socket_manager import ws_manager
from app.interfaces.api.accounts_router import router as accounts_router
from app.interfaces.api.proxies_router import router as proxies_router  # <-- Dòng import bị thiếu
from app.interfaces.api.tasks_router import router as tasks_router
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Application")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý vòng đời khởi chạy và tắt ứng dụng"""
    logger.info("[*] Hệ thống đang khởi động...")
    # 1. Khởi tạo DB SQLite
    init_db()
    logger.info("[+] Khởi tạo Cơ sở dữ liệu thành công.")

    # 2. Khởi tạo Task Dispatcher và lưu vào app state
    dispatcher = ConcurrentTaskDispatcher(max_tabs=settings.MAX_CONCURRENT_TABS)
    await dispatcher.start()
    app.state.dispatcher = dispatcher
    logger.info("[+] Khởi tạo ConcurrentTaskDispatcher thành công.")

    yield

    # 3. Dọn dẹp dập tắt các luồng chạy ngầm khi đóng app
    logger.info("[-] Hệ thống đang tắt...")
    await app.state.dispatcher.stop()
    logger.info("[-] Đã tắt ConcurrentTaskDispatcher an toàn.")

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Đăng ký các API Routers
app.include_router(accounts_router, prefix=settings.API_V1_STR)
app.include_router(proxies_router, prefix=settings.API_V1_STR)  # Đăng ký Router Proxy
app.include_router(tasks_router, prefix=settings.API_V1_STR)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Lỗi kết nối WebSocket: {str(e)}")
        ws_manager.disconnect(websocket)

@app.get("/")
def read_root():
    return {"status": "ONLINE", "service": settings.APP_NAME}