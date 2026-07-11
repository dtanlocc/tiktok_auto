from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher
from app.interfaces.api.deps import get_task_dispatcher, get_account_repository
from app.domain.ports.repository import IAccountRepository


router = APIRouter(prefix="/tasks", tags=["Tasks"])

class BulkLoginRequest(BaseModel):
    account_ids: List[str]
    login_method: str = "COOKIE" # COOKIE hoặc CREDENTIAL
    concurrency_limit: int = 4

class BulkUpdateProfileRequest(BaseModel):
    account_ids: List[str]
    avatar_folder: Optional[str] = None
    concurrency_limit: int = 4

@router.post("/bulk-login")
async def start_bulk_login(
    payload: BulkLoginRequest,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API Đăng nhập hàng loạt tài khoản đã chọn (COOKIE hoặc CREDENTIAL)"""
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản.")
    
    dispatcher.set_concurrency_limit(payload.concurrency_limit)
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
    
    dispatcher.set_concurrency_limit(payload.concurrency_limit)
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

