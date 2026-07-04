from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher
from app.interfaces.api.deps import get_task_dispatcher, get_account_repository
from app.domain.ports.repository import IAccountRepository

router = APIRouter(prefix="/tasks", tags=["Tasks"])

class BulkStartRequest(BaseModel):
    account_ids: List[str]
    login_method: str = "COOKIE"
    avatar_folder: Optional[str] = None
    concurrency_limit: int = 4

@router.post("/bulk-start")
async def start_bulk_tasks(
    payload: BulkStartRequest,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API Nhận danh sách ID tài khoản được tích chọn từ Web UI và đẩy vào hàng đợi chạy song song"""
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản để chạy.")

    # Cập nhật số luồng chạy song song động trên Dispatcher
    dispatcher.set_concurrency_limit(payload.concurrency_limit)

    queued_count = 0
    for acc_id in payload.account_ids:
        account = account_repo.get_by_id(acc_id)
        if not account:
            continue
        
        # Đẩy từng tác vụ vào hàng đợi điều phối kèm thư mục chứa ảnh
        await dispatcher.submit_task(
            account_id=acc_id,
            login_method=payload.login_method,
            avatar_folder=payload.avatar_folder
        )
        queued_count += 1

    return {
        "status": "QUEUED",
        "message": f"Đã xếp hàng đợi xử lý thành công cho {queued_count} tài khoản với giới hạn {payload.concurrency_limit} luồng song song."
    }