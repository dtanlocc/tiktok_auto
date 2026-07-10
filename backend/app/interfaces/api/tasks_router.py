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

