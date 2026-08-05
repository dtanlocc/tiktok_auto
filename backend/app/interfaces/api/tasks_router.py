from typing import List, Optional
import asyncio
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher
from app.interfaces.api.deps import get_task_dispatcher, get_account_repository
from app.domain.ports.repository import IAccountRepository
from app.use_cases.health_check.quick_check_use_case import quick_health_check_service
from app.use_cases.debug.debug_login_service import debug_login_service


router = APIRouter(prefix="/tasks", tags=["Tasks"])

class BulkLoginRequest(BaseModel):
    account_ids: List[str]
    login_method: str = "COOKIE" # COOKIE hoặc CREDENTIAL
    # So luong chay DONG THOI TREN MOI PROXY (thay cho "so luong tong" truoc day).
    proxy_concurrency: int = 2

class BulkUpdateProfileRequest(BaseModel):
    account_ids: List[str]
    avatar_folder: Optional[str] = None
    proxy_concurrency: int = 2


class ProxyConcurrencyRequest(BaseModel):
    limit: int = 2

class QuickHealthCheckRequest(BaseModel):
    account_ids: List[str]
    concurrency_limit: int = 5

@router.post("/bulk-login")
async def start_bulk_login(
    payload: BulkLoginRequest,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API Đăng nhập hàng loạt tài khoản đã chọn (COOKIE hoặc CREDENTIAL)"""
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản.")
    
    dispatcher.set_proxy_concurrency_limit(payload.proxy_concurrency)
    queued_count = 0
    for acc_id in payload.account_ids:
        account = account_repo.get_by_id(acc_id)
        if not account:
            continue

        # Đẩy tác vụ LOGIN vào hàng đợi
        await dispatcher.submit_task(
            account_id=acc_id,
            task_type=f"LOGIN_{payload.login_method}", # LOGIN_COOKIE hoặc LOGIN_CREDENTIAL
            avatar_folder=None
        )
        queued_count += 1
        
    return {"status": "SUCCESS", "message": f"Đang tiến hành xếp hàng đăng nhập cho {queued_count} tài khoản."}

@router.post("/bulk-update-profile")
async def start_bulk_update_profile(
    payload: BulkUpdateProfileRequest,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API Cập nhật Profile (Avatar & Bio) hàng loạt cho các tài khoản đã chọn"""
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản.")
    
    dispatcher.set_proxy_concurrency_limit(payload.proxy_concurrency)
    queued_count = 0
    for acc_id in payload.account_ids:
        account = account_repo.get_by_id(acc_id)
        if not account:
            continue

        # Đẩy tác vụ UPDATE_PROFILE vào hàng đợi
        await dispatcher.submit_task(
            account_id=acc_id,
            task_type="UPDATE_PROFILE",
            avatar_folder=payload.avatar_folder
        )
        queued_count += 1
        
    return {"status": "SUCCESS", "message": f"Đang tiến hành xếp hàng cập nhật thông tin cho {queued_count} tài khoản."}


# =============================================================================
# ĐIỀU KHIỂN TOÀN CỤC: Bắt đầu / Tạm dừng / Tiếp tục / Dừng khẩn cấp
# =============================================================================

@router.get("/status")
async def get_global_status(
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher)
):
    """Trạng thái hiện tại của dispatcher - dùng để đồng bộ UI khi tải lại trang."""
    return dispatcher.get_global_status()


@router.post("/proxy-concurrency")
async def set_proxy_concurrency(
    payload: ProxyConcurrencyRequest,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher)
):
    """Chỉnh SỐ LUỒNG CHẠY ĐỒNG THỜI TỐI ĐA / 1 PROXY ngay lập tức từ Web UI
    (thay cho 'số luồng chạy' tổng cũ). Áp dụng cho các tác vụ chạy sau đó."""
    if payload.limit <= 0:
        raise HTTPException(status_code=400, detail="Số luồng/proxy phải >= 1.")
    dispatcher.set_proxy_concurrency_limit(payload.limit)
    await dispatcher.broadcast_global_state()
    return {
        "status": "SUCCESS",
        "proxy_max_concurrent": payload.limit,
        "message": f"Đã đặt tối đa {payload.limit} luồng chạy đồng thời trên mỗi proxy.",
    }


@router.post("/start-global")
async def start_global(
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher)
):
    """Khởi động (hoặc khởi động lại) vòng lặp xử lý hàng đợi nếu đang tắt."""
    await dispatcher.start()
    await dispatcher.broadcast_global_state()
    return {"status": "SUCCESS", "message": "Đã khởi động hệ thống điều phối."}


@router.post("/pause-global")
async def pause_global(
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher)
):
    """Tạm dừng TOÀN BỘ các luồng đang chạy - mỗi luồng sẽ dừng lại ở checkpoint
    gần nhất (thường chỉ trễ vài giây) và chờ lệnh tiếp tục."""
    dispatcher.pause_global()
    await dispatcher.broadcast_global_state()
    return {"status": "SUCCESS", "message": "Đã tạm dừng toàn bộ hệ thống."}


@router.post("/resume-global")
async def resume_global(
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher)
):
    """Tiếp tục lại toàn bộ hệ thống sau khi tạm dừng."""
    dispatcher.resume_global()
    await dispatcher.broadcast_global_state()
    return {"status": "SUCCESS", "message": "Đã tiếp tục toàn bộ hệ thống."}


@router.post("/stop-global")
async def stop_global(
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher)
):
    """DỪNG KHẨN CẤP: hủy ngay lập tức mọi luồng đang chạy (đóng browser của
    từng luồng) và xóa sạch các tác vụ còn đang chờ trong hàng đợi. Hệ thống
    vẫn sẵn sàng nhận tác vụ MỚI ngay sau đó (không tắt hẳn dispatcher)."""
    await dispatcher.emergency_stop_all()
    return {"status": "SUCCESS", "message": "Đã dừng khẩn cấp toàn bộ hệ thống."}


# =============================================================================
# ĐIỀU KHIỂN TỪNG TÀI KHOẢN: Tạm dừng / Tiếp tục riêng lẻ
# =============================================================================

@router.post("/pause-account/{account_id}")
async def pause_account(
    account_id: str,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher)
):
    """Tạm dừng riêng 1 tài khoản đang chạy để can thiệp thủ công, các tài
    khoản khác không bị ảnh hưởng."""
    dispatcher.pause_account(account_id)
    await dispatcher.broadcast_account_pause_state(account_id)
    return {"status": "SUCCESS", "message": f"Đã tạm dừng tài khoản {account_id}."}


@router.post("/resume-account/{account_id}")
async def resume_account(
    account_id: str,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher)
):
    """Tiếp tục lại 1 tài khoản đã bị tạm dừng riêng."""
    dispatcher.resume_account(account_id)
    await dispatcher.broadcast_account_pause_state(account_id)
    return {"status": "SUCCESS", "message": f"Đã tiếp tục tài khoản {account_id}."}


# =============================================================================
# CHECK NHANH SỐNG/CHẾT (TÁCH RIÊNG HOÀN TOÀN - KHÔNG QUA DISPATCHER CHÍNH)
# Dùng Chromium thường, chỉ đọc title công khai, không cần chống dò vân tay.
# =============================================================================

@router.post("/quick-health-check")
async def start_quick_health_check(payload: QuickHealthCheckRequest):
    """Chạy hàng loạt Check nhanh sống/chết bằng Chromium nhẹ, độc lập hoàn
    toàn với hàng đợi/luồng đăng nhập chính. Trả về ngay lập tức, tiến độ
    được cập nhật qua WebSocket (event ACCOUNT_STATUS_CHANGED cho từng acc,
    và QUICK_CHECK_FINISHED khi xong toàn bộ đợt)."""
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản.")

    if quick_health_check_service.is_running:
        raise HTTPException(
            status_code=409,
            detail="Đang có 1 đợt Check nhanh chạy dở, vui lòng đợi hoàn tất trước khi chạy đợt mới."
        )

    asyncio.create_task(
        quick_health_check_service.run_batch(payload.account_ids, payload.concurrency_limit)
    )

    return {
        "status": "SUCCESS",
        "message": f"Đã bắt đầu Check nhanh cho {len(payload.account_ids)} tài khoản."
    }


@router.get("/quick-health-check/status")
async def get_quick_health_check_status():
    """Trạng thái tiến độ hiện tại của đợt Check nhanh (nếu đang chạy)."""
    return quick_health_check_service.get_status()


# =============================================================================
# CHẾ ĐỘ LIÊN TỤC: tự động lặp lại Check nhanh cho TOÀN BỘ account đang
# health_status="ALIVE" theo chu kỳ - hoàn toàn tách biệt, không đụng gì
# tới ConcurrentTaskDispatcher hay InteractionScheduler.
# =============================================================================
class ContinuousCheckRequest(BaseModel):
    account_ids: List[str]
    gap_seconds: int = 3
    concurrency_limit: int = 15


@router.post("/quick-health-check/start-continuous")
async def start_continuous_quick_check(payload: ContinuousCheckRequest):
    """Bật chế độ quét LIÊN TỤC (không nghỉ dài) CHỈ cho danh sách account_ids
    được chọn, đa luồng, hết vòng chạy ngay vòng kế tiếp - tới khi bấm dừng."""
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản trước khi bật Check nhanh liên tục.")
    started = quick_health_check_service.start_continuous(
        account_ids=payload.account_ids,
        gap_seconds=payload.gap_seconds,
        concurrency_limit=payload.concurrency_limit,
    )
    if not started:
        raise HTTPException(status_code=409, detail="Chế độ liên tục đã đang bật sẵn rồi.")
    return {
        "status": "SUCCESS",
        "message": f"Đã bật Check nhanh liên tục cho {len(payload.account_ids)} tài khoản đã chọn ({payload.concurrency_limit} luồng song song).",
    }


@router.post("/quick-health-check/stop-continuous")
async def stop_continuous_quick_check():
    """Tắt chế độ liên tục - đợt hiện tại (nếu đang chạy dở) sẽ được chạy
    xong rồi mới dừng hẳn, không hủy ngang giữa chừng."""
    stopped = quick_health_check_service.stop_continuous()
    if not stopped:
        raise HTTPException(status_code=409, detail="Chế độ liên tục hiện không bật.")
    return {"status": "SUCCESS", "message": "Đã yêu cầu tắt Check nhanh liên tục."}


@router.get("/quick-health-check/continuous-status")
async def get_continuous_quick_check_status():
    return quick_health_check_service.get_continuous_status()


# =============================================================================
# CHE DO DEBUG (thao tac tay): mo trinh duyet HIEN, dang nhap roi GIU cua so mo
# de user tu tay thao tac toi khi tu dong. TACH RIENG hoan toan voi luong mo
# trinh duyet AN (dispatcher) - moi account toi da 1 phien debug.
# =============================================================================
class DebugLoginRequest(BaseModel):
    account_id: str


@router.post("/debug-login")
async def start_debug_login(payload: DebugLoginRequest):
    """Mo 1 phien DEBUG: trinh duyet HIEN len man hinh, tu dang nhap (uu tien
    cookie, fallback OTP) roi DUNG lai giu cua so mo de ban thao tac tay. Phien
    ket thuc khi ban DONG cua so hoac goi /debug-login/stop."""
    if debug_login_service.is_running(payload.account_id):
        raise HTTPException(
            status_code=409,
            detail="Tài khoản này đã có 1 phiên debug đang mở. Hãy đóng cửa sổ đó trước."
        )
    started = debug_login_service.start(payload.account_id)
    if not started:
        raise HTTPException(status_code=409, detail="Không thể khởi tạo phiên debug (đã đang chạy).")
    return {"status": "SUCCESS", "message": "Đã mở phiên debug — trình duyệt sẽ hiện lên để bạn thao tác tay."}


@router.post("/debug-login/stop")
async def stop_debug_login(payload: DebugLoginRequest):
    """Dong cua so debug tu xa (thay vi bam X tren cua so vat ly)."""
    stopped = await debug_login_service.stop(payload.account_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="Không có phiên debug nào đang mở cho tài khoản này.")
    return {"status": "SUCCESS", "message": "Đã yêu cầu đóng phiên debug."}


@router.get("/debug-login/active")
async def get_active_debug_sessions():
    """Danh sach account_id dang co phien debug mo (de UI hien nut Dung/Mo dung)."""
    return {"active_ids": debug_login_service.active_ids()}

