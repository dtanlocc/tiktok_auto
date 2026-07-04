import uuid
import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Body, status, UploadFile, File
from app.domain.ports.repository import IAccountRepository, IProxyRepository
from app.domain.entities.account import TikTokAccount
from app.interfaces.api.deps import get_account_repository, get_proxy_repository
from app.interfaces.dto.account_dto import AccountCreateIn, AccountOut
from app.infrastructure.websocket.socket_manager import ws_manager

logger = logging.getLogger("AccountsRouter")
router = APIRouter(prefix="/accounts", tags=["Accounts"])


def _get_least_used_proxy_id(account_repo: IAccountRepository, proxy_repo: IProxyRepository) -> Optional[str]:
    """
    Thuật toán Least Connections:
    Tìm kiếm và trả về ID của Proxy hiện đang liên kết với ít tài khoản nhất trong hệ thống.
    """
    proxies = proxy_repo.get_all()
    if not proxies:
        return None
        
    accounts = account_repo.get_all()
    # Khởi tạo bộ đếm tải trọng cho từng Proxy
    proxy_usage = {p.id: 0 for p in proxies}
    for acc in accounts:
        if acc.proxy_id in proxy_usage:
            proxy_usage[acc.proxy_id] += 1
            
    # Tìm ra Proxy có bộ đếm nhỏ nhất (ít liên kết nhất)
    best_proxy = min(proxies, key=lambda p: proxy_usage[p.id])
    return best_proxy.id


@router.post("/", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreateIn,
    account_repo: IAccountRepository = Depends(get_account_repository),
    proxy_repo: IProxyRepository = Depends(get_proxy_repository)
):
    """API thêm tài khoản thủ công qua Form (Tự động gán Proxy tải trọng nhẹ nếu không truyền proxy_id)"""
    proxy_id = payload.proxy_id
    if not proxy_id:
        # Tự động tìm Proxy tốt nhất nếu trong DB đã có sẵn proxy
        proxy_id = _get_least_used_proxy_id(account_repo, proxy_repo)

    new_account = TikTokAccount(
        id=str(uuid.uuid4()),
        username=payload.username,
        password=payload.password,
        proxy_id=proxy_id,
        status="IDLE",
        current_step="Chưa kích hoạt"
    )
    
    try:
        saved_account = account_repo.save(new_account)
        
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
    account_repo: IAccountRepository = Depends(get_account_repository),
    proxy_repo: IProxyRepository = Depends(get_proxy_repository)
):
    """API Phân tích cú pháp chuỗi text dán (Tự động phân bổ Proxy tối ưu)"""
    try:
        parts = raw_text.strip().split("|")
        if len(parts) < 7:
            raise HTTPException(
                status_code=400, 
                detail="Định dạng dữ liệu không hợp lệ."
            )

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

        # Chống ghi đè thông minh trùng UUID
        if uuid_str:
            existing = account_repo.get_by_id(uuid_str)
            if existing and existing.username != username:
                uuid_str = str(uuid.uuid4())

        # TỰ ĐỘNG PHÂN BỔ PROXY TỐI ƯU
        allocated_proxy_id = _get_least_used_proxy_id(account_repo, proxy_repo)

        account = TikTokAccount(
            id=uuid_str if uuid_str else str(uuid.uuid4()),
            username=username,
            password=password,
            email=email,
            email_password=email_password,
            device_token=device_token,
            cookies=cookies,
            status="IDLE",
            current_step="Chưa kích hoạt",
            proxy_id=allocated_proxy_id  # <-- Gán Proxy tự động
        )

        saved_account = account_repo.save(account)

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
async def import_accounts_from_files(
    files: List[UploadFile] = File(...),
    account_repo: IAccountRepository = Depends(get_account_repository),
    proxy_repo: IProxyRepository = Depends(get_proxy_repository)
):
    """API Nhập hàng loạt tài khoản từ nhiều file cùng lúc (Tự động phân bổ Proxy tải trọng nhẹ cho từng file)"""
    try:
        imported_count = 0
        for file in files:
            content = await file.read()
            lines = content.decode("utf-8").splitlines()

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

                if uuid_str:
                    existing = account_repo.get_by_id(uuid_str)
                    if existing and existing.username != username:
                        uuid_str = str(uuid.uuid4())

                # TỰ ĐỘNG PHÂN BỔ PROXY TẢI TRỌNG THẤP NHẤT
                allocated_proxy_id = _allocate_next_proxy(proxy_repo, account_repo)

                account = TikTokAccount(
                    id=uuid_str if uuid_str else str(uuid.uuid4()),
                    username=username,
                    password=password,
                    email=email,
                    email_password=email_password,
                    device_token=device_token,
                    cookies=cookies,
                    status="IDLE",
                    current_step="Chưa kích hoạt",
                    proxy_id=allocated_proxy_id
                )
                account_repo.save(account)
                imported_count += 1

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

        return {"status": "SUCCESS", "message": f"Đã nhập thành công {imported_count} tài khoản từ {len(files)} tệp tin."}
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

@router.post("/auto-allocate-proxies")
async def auto_allocate_proxies_endpoint(
    account_ids: List[str] = Body(..., embed=True),
    account_repo: IAccountRepository = Depends(get_account_repository),
    proxy_repo: IProxyRepository = Depends(get_proxy_repository)
):
    """API chuột phải: Tự động phân bổ đều danh sách Proxy cho các tài khoản đã chọn"""
    proxies = proxy_repo.get_all()
    if not proxies:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kho lưu trữ chưa có Proxy nào. Vui lòng nạp Proxy trước."
        )

    # 1. Thống kê tải trọng sử dụng thực tế của từng Proxy hiện tại
    accounts = account_repo.get_all()
    proxy_usage = {p.id: 0 for p in proxies}
    for acc in accounts:
        if acc.proxy_id in proxy_usage:
            proxy_usage[acc.proxy_id] += 1

    allocated_count = 0
    for acc_id in account_ids:
        account = account_repo.get_by_id(acc_id)
        if not account:
            continue
        
        # Thuật toán Least Connections: Tìm Proxy đang gánh ít tài khoản nhất
        best_proxy_id = min(proxies, key=lambda p: proxy_usage[p.id]).id
        
        # Gán Proxy và cập nhật tải trọng tức thời để gán cho tài khoản tiếp theo
        account.proxy_id = best_proxy_id
        proxy_usage[best_proxy_id] += 1
        
        account_repo.save(account)
        allocated_count += 1

        # Bắn tín hiệu WebSocket báo cho Web UI cập nhật lại bảng gán Proxy ngay lập tức
        await ws_manager.broadcast({
            "event": "ACCOUNT_PROXY_CHANGED",
            "data": {
                "id": acc_id,
                "proxy_id": best_proxy_id
            }
        })

    return {
        "status": "SUCCESS", 
        "message": f"Đã tự động phân bổ đều Proxy khả dụng cho {allocated_count} tài khoản."
    }

def _allocate_next_proxy(proxy_repo: IProxyRepository, account_repo: IAccountRepository) -> Optional[str]:
    """Helper phân bổ Proxy tải trọng nhẹ nhất"""
    try:
        return _get_least_used_proxy_id(account_repo, proxy_repo)
    except Exception:
        return None