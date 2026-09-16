# File: backend/app/main.py
import sys
import asyncio

# =============================================================================
# KHẮC PHỤC LỖI NOTIMPLEMENTEDERROR TRÊN WINDOWS (PROACTOR LOOP ENFORCEMENT)
# =============================================================================
# Ép buộc Python sử dụng ProactorEventLoop trên Windows trước khi nạp bất kỳ tác vụ nào.
# Điều này giúp Playwright có thể tạo tiến trình con (Subprocess) khởi chạy Firefox tàng hình mượt mà.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.infrastructure.database.connection import init_db
from app.infrastructure.websocket.socket_manager import screen_ws_manager, ws_manager
from app.interfaces.api.accounts_router import router as accounts_router
from app.interfaces.api.proxies_router import router as proxies_router
from app.interfaces.api.tasks_router import router as tasks_router
from app.interfaces.api.interactions_router import router as interactions_router
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher
from app.infrastructure.scheduler.interaction_scheduler import InteractionScheduler
from app.security.license import EntitlementError
from app.security.local_auth import SignedLocalAuthMiddleware
from app.security.runtime import build_entitlement_gate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Application")
_DISTRIBUTED_MODE = settings.SECURITY_MODE.lower() in {"production", "friends"}


def _windows_loop_exception_handler(
    loop: asyncio.AbstractEventLoop, context: dict
) -> None:
    """Bỏ qua đúng lỗi đóng socket Proactor vô hại trên Windows.

    Khi browser/frontend đóng TCP hoặc WebSocket trước, Proactor gọi ``shutdown``
    trên socket đã bị reset và asyncio mặc định in một traceback WinError 10054.
    Không nuốt ConnectionResetError phát sinh trong task nghiệp vụ; chỉ callback
    ``_call_connection_lost`` của transport mới được lọc.
    """
    exc = context.get("exception")
    callback_text = f"{context.get('message', '')} {context.get('handle', '')}"
    if (
        sys.platform == "win32"
        and isinstance(exc, ConnectionResetError)
        and getattr(exc, "winerror", None) == 10054
        and "_ProactorBasePipeTransport._call_connection_lost" in callback_text
    ):
        logger.debug("Client reset a closing Proactor socket (WinError 10054).")
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý vòng đời khởi chạy và tắt ứng dụng"""
    asyncio.get_running_loop().set_exception_handler(_windows_loop_exception_handler)
    logger.info("[*] Hệ thống đang khởi động...")
    entitlement_gate = build_entitlement_gate(settings)
    app.state.entitlement_gate = entitlement_gate
    logger.info("[+] Runtime entitlement mode: %s", entitlement_gate.status()["mode"])
    # 1. Khởi tạo DB SQLite (Đã tích hợp Auto-Migration tự sửa lỗi thiếu cột)
    init_db()
    logger.info("[+] Khởi tạo Cơ sở dữ liệu thành công.")

    # 1b. Chế độ mạng người dùng đã chọn lần trước (proxy / mạng thật).
    from app.core.extension_settings import load_proxy_mode
    settings.USE_PROXY = load_proxy_mode()
    logger.info(
        "[+] Chế độ mạng: %s",
        "PROXY (theo proxy gán cho từng account)" if settings.USE_PROXY
        else "MẠNG THẬT (không proxy, đi qua mạng/VPN của máy)",
    )

    # 2. Khởi tạo Task Dispatcher và lưu vào app state
    dispatcher = ConcurrentTaskDispatcher(
        max_tabs=settings.MAX_CONCURRENT_TABS,
        entitlement_gate=entitlement_gate,
    )
    dispatcher.recover_orphaned_runtime_state()
    await dispatcher.start()
    app.state.dispatcher = dispatcher
    logger.info("[+] Khởi tạo ConcurrentTaskDispatcher thành công.")

    # 2b. Khởi tạo Interaction Scheduler (lập lịch chiến dịch tương tác video lặp chu kỳ)
    interaction_scheduler = InteractionScheduler(dispatcher)
    interaction_scheduler.start()
    app.state.interaction_scheduler = interaction_scheduler
    logger.info("[+] Khởi tạo InteractionScheduler thành công.")

    # 2c. Khởi tạo Scheduled Upload Service (hẹn giờ đăng video phía app)
    from app.use_cases.upload.scheduled_upload_service import ScheduledUploadService

    scheduled_upload = ScheduledUploadService(dispatcher)
    scheduled_upload.start()
    app.state.scheduled_upload = scheduled_upload
    from app.use_cases.upload.bulk_video_queue_service import BulkVideoQueueService

    def resolve_video_task_result(account_email: str) -> dict[str, str]:
        from sqlmodel import Session
        from app.infrastructure.database.connection import engine
        from app.infrastructure.database.sqlite_repository import (
            SQLiteAccountRepository,
        )

        with Session(engine) as session:
            account = SQLiteAccountRepository(session).get_by_id(account_email)
            return {
                "status": account.status if account else "ERROR",
                "step": (
                    account.current_step if account else "Account not found after task."
                ),
            }

    app.state.video_batch = BulkVideoQueueService(
        dispatcher, result_resolver=resolve_video_task_result
    )
    logger.info("[+] Khởi tạo ScheduledUploadService thành công.")

    yield

    # 3. Dọn dẹp dập tắt các luồng chạy ngầm khi đóng app
    logger.info("[-] Hệ thống đang tắt...")
    try:
        await app.state.video_batch.shutdown()
    except Exception:
        pass
    try:
        from app.use_cases.analytics.tiktok_fast_analytics_sync import (
            fast_analytics_sync_service,
        )

        await fast_analytics_sync_service.shutdown()
    except Exception:
        pass
    try:
        app.state.scheduled_upload.shutdown()
    except Exception:
        pass
    app.state.interaction_scheduler.shutdown()
    await app.state.dispatcher.stop()
    logger.info("[-] Đã tắt ConcurrentTaskDispatcher an toàn.")


app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    docs_url=None if _DISTRIBUTED_MODE else "/docs",
    redoc_url=None if _DISTRIBUTED_MODE else "/redoc",
    openapi_url=None if _DISTRIBUTED_MODE else "/openapi.json",
    lifespan=lifespan,
)


@app.exception_handler(EntitlementError)
async def entitlement_error_handler(request: Request, exc: EntitlementError):
    return JSONResponse(
        status_code=403,
        content={"detail": str(exc), "code": "LICENSE_DENIED"},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        ["http://tauri.localhost", "tauri://localhost"]
        if _DISTRIBUTED_MODE
        else ["*"]
    ),
    allow_credentials=not _DISTRIBUTED_MODE,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SignedLocalAuthMiddleware,
    security_mode=settings.SECURITY_MODE,
    session_secret=settings.LOCAL_SESSION_SECRET,
    max_skew_seconds=settings.LOCAL_AUTH_MAX_SKEW_SECONDS,
    replay_ttl_seconds=settings.LOCAL_AUTH_REPLAY_TTL_SECONDS,
    max_body_bytes=settings.LOCAL_AUTH_MAX_BODY_BYTES,
)

# Đăng ký các API Routers
app.include_router(accounts_router, prefix=settings.API_V1_STR)
app.include_router(proxies_router, prefix=settings.API_V1_STR)
app.include_router(tasks_router, prefix=settings.API_V1_STR)
app.include_router(interactions_router, prefix=settings.API_V1_STR)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    try:
        websocket.app.state.entitlement_gate.require("app.start")
    except EntitlementError:
        await websocket.close(code=4403, reason="License denied")
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            # Main socket is server -> client only. Receiving here merely keeps
            # the connection alive and notices a clean client disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Lỗi kết nối WebSocket: {str(e)}")
        ws_manager.disconnect(websocket)


@app.websocket("/ws/screens")
async def screen_websocket_endpoint(websocket: WebSocket):
    """Kênh một chiều chỉ phát frame trực tiếp tới dashboard.

    Không nhận chuột, bàn phím hoặc lệnh pause automation. Tách luồng ảnh khỏi
    socket sự kiện chính để frame base64 không làm chậm log/trạng thái.
    """
    try:
        websocket.app.state.entitlement_gate.require("app.start")
    except EntitlementError:
        await websocket.close(code=4403, reason="License denied")
        return
    await screen_ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        screen_ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Lỗi kết nối WebSocket màn hình: {str(e)}")
        screen_ws_manager.disconnect(websocket)


@app.get("/")
def read_root(request: Request):
    request.app.state.entitlement_gate.require("app.start")
    return {"status": "ONLINE", "service": settings.APP_NAME}
