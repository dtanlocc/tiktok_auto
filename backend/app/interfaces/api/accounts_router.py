import uuid
import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Body, status, UploadFile, File
from app.domain.ports.repository import IAccountRepository
from app.domain.entities.account import TikTokAccount
from app.interfaces.api.deps import get_account_repository
from app.interfaces.dto.account_dto import AccountCreateIn, AccountOut
from app.infrastructure.websocket.socket_manager import ws_manager

logger = logging.getLogger("AccountsRouter")
router = APIRouter(prefix="/accounts", tags=["Accounts"])

@router.post("/", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreateIn,
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API thêm tài khoản thủ công qua Form"""
    new_account = TikTokAccount(
        id=str(uuid.uuid4()),
        username=payload.username,
        password=payload.password,
        proxy_id=payload.proxy_id,
        status="IDLE",
        current_step="Chưa kích hoạt"
    )
    
    try:
        saved_account = account_repo.save(new_account)
        
        # Phát WebSocket báo cho Web UI vẽ thêm hàng tài khoản mới
        await ws_manager.broadcast({
            "event": "ACCOUNT_ADDED",
            "data": {
                "id": saved_account.id,
                "username": saved_account.username,
                "status": saved_account.status,
                "proxy_id": saved_account.proxy_id,
                "has_cookies": len(saved_account.cookies) > 0,
                "current_step": saved_account.current_step
            }
        })
        
        return AccountOut(
            id=saved_account.id,
            username=saved_account.username,
            status=saved_account.status,
            current_step=saved_account.current_step,
            proxy_id=saved_account.proxy_id,
            has_cookies=len(saved_account.cookies) > 0
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tài khoản đã tồn tại hoặc dữ liệu không hợp lệ: {str(e)}"
        )

@router.get("/", response_model=List[AccountOut])
async def list_accounts(
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API lấy toàn bộ danh sách tài khoản hiển thị lên Dashboard"""
    accounts = account_repo.get_all()
    return [
        AccountOut(
            id=acc.id,
            username=acc.username,
            status=acc.status,
            current_step=acc.current_step,
            proxy_id=acc.proxy_id,
            has_cookies=len(acc.cookies) > 0
        )
        for acc in accounts
    ]

@router.post("/import-raw", status_code=status.HTTP_201_CREATED)
async def import_raw_account(
    raw_text: str = Body(..., media_type="text/plain"),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """
    API Phân tích cú pháp chuỗi text tài khoản dán trực tiếp
    Định dạng dán: Username|Password|Email|EmailPassword|DeviceToken|UUID|CookiesJSON
    """
    try:
        parts = raw_text.strip().split("|")
        if len(parts) < 7:
            raise HTTPException(
                status_code=400, 
                detail="Định dạng dữ liệu không hợp lệ. Phải chứa ít nhất 7 trường phân tách bởi ký tự '|'."
            )

        username = parts[0].strip()
        password = parts[1].strip()
        email = parts[2].strip()
        email_password = parts[3].strip()
        device_token = parts[4].strip()
        uuid_str = parts[5].strip()
        cookies_raw = parts[6].strip()

        # Phân tích cú pháp cookies từ chuỗi JSON
        try:
            cookies = json.loads(cookies_raw)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400, 
                detail="Phần dữ liệu Cookies không đúng định dạng JSON."
            )

        # Tạo thực thể Domain hoàn chỉnh đầy đủ thông tin
        account = TikTokAccount(
            id=uuid_str if uuid_str else str(uuid.uuid4()),
            username=username,
            password=password,
            email=email,
            email_password=email_password,
            device_token=device_token,
            cookies=cookies,
            status="IDLE",
            current_step="Chưa kích hoạt"
        )

        saved_account = account_repo.save(account)

        # Phát WebSocket báo cho Web UI vẽ thêm hàng tài khoản mới
        await ws_manager.broadcast({
            "event": "ACCOUNT_ADDED",
            "data": {
                "id": saved_account.id,
                "username": saved_account.username,
                "status": saved_account.status,
                "proxy_id": saved_account.proxy_id,
                "has_cookies": len(saved_account.cookies) > 0,
                "current_step": saved_account.current_step
            }
        })

        return {"status": "SUCCESS", "username": saved_account.username, "id": saved_account.id}

    except Exception as e:
        logger.error(f"Lỗi import tài khoản: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Không thể xử lý dữ liệu: {str(e)}")

@router.post("/import-file", status_code=status.HTTP_201_CREATED)
async def import_accounts_from_file(
    file: UploadFile = File(...),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API Nhập hàng loạt tài khoản bằng cách tải lên file .txt phân tách bằng ký tự đứng |"""
    try:
        content = await file.read()
        lines = content.decode("utf-8").splitlines()
        imported_count = 0

        for line in lines:
            if not line.strip():
                continue
            parts = line.strip().split("|")
            if len(parts) < 7:
                continue

            username = parts[0].strip()
            password = parts[1].strip()
            email = parts[2].strip()
            email_password = parts[3].strip()
            device_token = parts[4].strip()
            uuid_str = parts[5].strip()
            cookies_raw = parts[6].strip()

            try:
                cookies = json.loads(cookies_raw)
            except json.JSONDecodeError:
                cookies = []

            account = TikTokAccount(
                id=uuid_str if uuid_str else str(uuid.uuid4()),
                username=username,
                password=password,
                email=email,
                email_password=email_password,
                device_token=device_token,
                cookies=cookies,
                status="IDLE",
                current_step="Chưa kích hoạt"
            )
            account_repo.save(account)
            imported_count += 1

            # Phát WebSocket thông báo tài khoản mới được nạp thành công
            await ws_manager.broadcast({
                "event": "ACCOUNT_ADDED",
                "data": {
                    "id": account.id,
                    "username": account.username,
                    "status": account.status,
                    "proxy_id": account.proxy_id,
                    "has_cookies": len(account.cookies) > 0,
                    "current_step": account.current_step
                }
            })

        return {"status": "SUCCESS", "message": f"Đã nhập thành công {imported_count} tài khoản."}
    except Exception as e:
        logger.error(f"Lỗi đọc file tài khoản: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Không thể xử lý tệp: {str(e)}")

@router.put("/{account_id}/proxy", response_model=AccountOut)
async def bind_proxy_to_account(
    account_id: str,
    proxy_id: Optional[str] = Body(default=None, embed=True),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API gán hoặc gỡ Proxy cho một tài khoản cụ thể"""
    account = account_repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    
    account.proxy_id = proxy_id
    saved = account_repo.save(account)

    # Phát tín hiệu báo cho Frontend biết tài khoản đã đổi IP Proxy
    await ws_manager.broadcast({
        "event": "ACCOUNT_PROXY_CHANGED",
        "data": {
            "id": account_id,
            "proxy_id": proxy_id
        }
    })

    return AccountOut(
        id=saved.id,
        username=saved.username,
        status=saved.status,
        current_step=saved.current_step,
        proxy_id=saved.proxy_id,
        has_cookies=len(saved.cookies) > 0
    )