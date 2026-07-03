from fastapi import APIRouter, Depends, HTTPException, status
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher
from app.interfaces.api.deps import get_task_dispatcher, get_account_repository
from app.domain.ports.repository import IAccountRepository

router = APIRouter(prefix="/tasks", tags=["Tasks"])

@router.post("/start")
async def start_task(
    account_id: str,
    login_method: str = "COOKIE",  # Hoặc "CREDENTIAL"
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API kích hoạt chạy tài khoản. Nhận lệnh từ nút bấm trên Dashboard"""
    # 1. Kiểm tra tài khoản có tồn tại không
    account = account_repo.get_by_id(account_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tài khoản không tồn tại trên hệ thống."
        )

    if account.status in ["RUNNING", "QUEUED"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tài khoản này đang trong hàng đợi hoặc đang chạy."
        )

    if login_method not in ["COOKIE", "CREDENTIAL"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phương thức đăng nhập không hợp lệ (COOKIE hoặc CREDENTIAL)."
        )

    # 2. Đưa tài khoản vào hàng đợi bất đồng bộ xử lý
    await dispatcher.submit_task(account_id, login_method)
    
    return {
        "status": "QUEUED",
        "message": f"Tài khoản {account.username} đã được xếp vào hàng đợi xử lý."
    }