# File: backend/app/interfaces/api/accounts_router.py
import uuid
import json
import logging
import os
import shutil
from datetime import datetime
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
    proxy_usage = {p.id: 0 for p in proxies}
    for acc in accounts:
        if acc.proxy_id in proxy_usage:
            proxy_usage[acc.proxy_id] += 1
            
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
        proxy_id = _get_least_used_proxy_id(account_repo, proxy_repo)

    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_date_str = datetime.now().strftime("%Y%m%d")

    new_account = TikTokAccount(
        id=str(uuid.uuid4()),
        username=payload.username,
        password=payload.password,
        proxy_id=proxy_id,
        status="IDLE",
        health_status="UNKNOWN",
        profile_status="PENDING",
        current_step="Chưa kích hoạt",
        country="US",
        batch_tag=f"MANUAL_{current_date_str}",
        created_at=current_time_str
    )
    
    try:
        saved_account = account_repo.save(new_account)
        
        await ws_manager.broadcast({
            "event": "ACCOUNT_ADDED",
            "data": {
                "id": saved_account.id,
                "username": saved_account.username,
                "status": saved_account.status,
                "health_status": saved_account.health_status,
                "profile_status": saved_account.profile_status,
                "proxy_id": saved_account.proxy_id,
                "has_cookies": len(saved_account.cookies) > 0,
                "current_step": saved_account.current_step,
                "country": saved_account.country,
                "batch_tag": saved_account.batch_tag,
                "created_at": saved_account.created_at
            }
        })
        
        return AccountOut(
            id=saved_account.id,
            username=saved_account.username,
            status=saved_account.status,
            health_status=saved_account.health_status,
            profile_status=saved_account.profile_status,
            current_step=saved_account.current_step,
            proxy_id=saved_account.proxy_id,
            has_cookies=len(saved_account.cookies) > 0,
            country=saved_account.country,
            batch_tag=saved_account.batch_tag,
            created_at=saved_account.created_at
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
    """API lấy toàn bộ danh sách tài khoản hiển thị lên Dashboard (Đã đồng bộ đủ tham số)"""
    accounts = account_repo.get_all()
    return [
        AccountOut(
            id=acc.id,
            username=acc.username,
            status=acc.status,
            health_status=acc.health_status,          # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
            profile_status=acc.profile_status,        # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
            current_step=acc.current_step,
            proxy_id=acc.proxy_id,
            has_cookies=len(acc.cookies) > 0,
            country=acc.country,                      # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
            batch_tag=acc.batch_tag,                  # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
            created_at=acc.created_at or ""           # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
        )
        for acc in accounts
    ]


@router.post("/import-raw", status_code=status.HTTP_201_CREATED)
async def import_raw_account(
    raw_text: str = Body(..., media_type="text/plain"),
    country: str = "US",
    batch_tag: Optional[str] = None,
    account_repo: IAccountRepository = Depends(get_account_repository),
    proxy_repo: IProxyRepository = Depends(get_proxy_repository)
):
    """API Phân tích cú pháp chuỗi text dán (Tự động cấp phát ID hệ thống độc nhất)"""
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
        refresh_token = parts[4].strip()  
        client_id = parts[5].strip()      
        cookies_raw = parts[6].strip()

        try:
            cookies = json.loads(cookies_raw)
        except json.JSONDecodeError:
            cookies = []

        allocated_proxy_id = _get_least_used_proxy_id(account_repo, proxy_repo)
        
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not batch_tag:
            batch_tag = f"LÔ_{datetime.now().strftime('%Y%m%d')}"

        account_id = str(uuid.uuid4())

        account = TikTokAccount(
            id=account_id,
            username=username,
            password=password,
            email=email,
            email_password=email_password,
            refresh_token=refresh_token,
            client_id=client_id,
            cookies=cookies,
            status="IDLE",
            health_status="UNKNOWN",
            profile_status="PENDING",
            current_step="Chưa kích hoạt",
            proxy_id=allocated_proxy_id,
            country=country.upper().strip(),
            batch_tag=batch_tag.strip(),
            created_at=current_time_str
        )

        saved_account = account_repo.save(account)

        await ws_manager.broadcast({
            "event": "ACCOUNT_ADDED",
            "data": {
                "id": saved_account.id,
                "username": saved_account.username,
                "status": saved_account.status,
                "health_status": saved_account.health_status,
                "profile_status": saved_account.profile_status,
                "proxy_id": saved_account.proxy_id,
                "has_cookies": len(saved_account.cookies) > 0,
                "current_step": saved_account.current_step,
                "country": saved_account.country,
                "batch_tag": saved_account.batch_tag,
                "created_at": saved_account.created_at
            }
        })

        return {"status": "SUCCESS", "username": saved_account.username, "id": saved_account.id}

    except Exception as e:
        logger.error(f"Lỗi import tài khoản: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Không thể xử lý dữ liệu: {str(e)}")


@router.post("/import-file", status_code=status.HTTP_201_CREATED)
async def import_accounts_from_files(
    files: List[UploadFile] = File(...),
    country: str = "US",
    batch_tag: Optional[str] = None,
    account_repo: IAccountRepository = Depends(get_account_repository),
    proxy_repo: IProxyRepository = Depends(get_proxy_repository)
):
    """API Nhập hàng loạt tài khoản từ nhiều file cùng lúc"""
    try:
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not batch_tag:
            batch_tag = f"LÔ_{datetime.now().strftime('%Y%m%d')}"

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
                refresh_token = parts[4].strip()  
                client_id = parts[5].strip()      
                cookies_raw = parts[6].strip()

                try:
                    cookies = json.loads(cookies_raw)
                except json.JSONDecodeError:
                    cookies = []

                allocated_proxy_id = _allocate_next_proxy(proxy_repo, account_repo)
                account_id = str(uuid.uuid4())

                account = TikTokAccount(
                    id=account_id,
                    username=username,
                    password=password,
                    email=email,
                    email_password=email_password,
                    refresh_token=refresh_token,
                    client_id=client_id,
                    cookies=cookies,
                    status="IDLE",
                    health_status="UNKNOWN",
                    profile_status="PENDING",
                    current_step="Chưa kích hoạt",
                    proxy_id=allocated_proxy_id,
                    country=country.upper().strip(),
                    batch_tag=batch_tag.strip(),
                    created_at=current_time_str
                )

                try:
                    account_repo.save(account)
                    imported_count += 1

                    await ws_manager.broadcast({
                        "event": "ACCOUNT_ADDED",
                        "data": {
                            "id": account.id,
                            "username": account.username,
                            "status": account.status,
                            "health_status": account.health_status,
                            "profile_status": account.profile_status,
                            "proxy_id": account.proxy_id,
                            "has_cookies": len(account.cookies) > 0,
                            "current_step": account.current_step,
                            "country": account.country,
                            "batch_tag": account.batch_tag,
                            "created_at": account.created_at
                        }
                    })
                except Exception as db_err:
                    logger.warning(f"Bỏ qua dòng lỗi hoặc trùng lặp vấp phải: {str(db_err)}")
                    if hasattr(account_repo, "session"):
                        account_repo.session.rollback()
                    continue

        return {"status": "SUCCESS", "message": f"Đã nhập thành công {imported_count} tài khoản vào {batch_tag}."}
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
        health_status=saved.health_status,            # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
        profile_status=saved.profile_status,          # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
        current_step=saved.current_step,
        proxy_id=saved.proxy_id,
        has_cookies=len(saved.cookies) > 0,
        country=saved.country,                        # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
        batch_tag=saved.batch_tag,                    # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
        created_at=saved.created_at or ""             # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
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
        
        best_proxy_id = min(proxies, key=lambda p: proxy_usage[p.id]).id
        
        account.proxy_id = best_proxy_id
        proxy_usage[best_proxy_id] += 1
        
        account_repo.save(account)
        allocated_count += 1

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


@router.delete("/{account_id}", status_code=status.HTTP_200_OK)
async def delete_account(
    account_id: str,
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API Xóa tài khoản đơn lẻ"""
    success = account_repo.delete(account_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản để xóa.")
    
    # Gửi tín hiệu thông báo đến toàn bộ Client qua Websocket
    await ws_manager.broadcast({
        "event": "ACCOUNT_DELETED",
        "data": {"id": account_id}
    })
    return {"status": "SUCCESS", "message": "Đã xóa tài khoản thành công."}


@router.post("/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete_accounts(
    account_ids: List[str] = Body(..., embed=True),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API Xóa hàng loạt tài khoản đã chọn"""
    deleted_count = 0
    for acc_id in account_ids:
        if account_repo.delete(acc_id):
            deleted_count += 1
            await ws_manager.broadcast({
                "event": "ACCOUNT_DELETED",
                "data": {"id": acc_id}
            })
            
    return {
        "status": "SUCCESS", 
        "message": f"Đã tiến hành gỡ bỏ và xóa sạch hoàn toàn {deleted_count} tài khoản khỏi cơ sở dữ liệu."
    }


@router.post("/select-local-folder")
def select_local_folder():
    """
    Mở cửa sổ chọn thư mục hệ thống (OS Folder Picker) trực tiếp từ Backend.
    Hoạt động hoàn hảo khi chạy cục bộ trên Windows/macOS/Linux.
    """
    import platform
    import os
    from concurrent.futures import ThreadPoolExecutor
    
    # Hàm chạy trong luồng riêng để tránh khóa luồng chính (Main Event Loop) của FastAPI
    def _picker():
        import tkinter as tk
        from tkinter import filedialog
        
        root = tk.Tk()
        root.withdraw()                    # Ẩn cửa sổ trống của Tkinter
        root.attributes('-topmost', True)   # Đẩy cửa sổ chọn thư mục lên trên cùng màn hình
        
        folder_path = filedialog.askdirectory(title="Chọn thư mục chứa ảnh đại diện (Avatar Folder)")
        root.destroy()
        return folder_path

    # Phòng thủ: Kiểm tra xem có môi trường đồ họa không (Tránh sập khi chạy trên VPS/Docker không màn hình)
    is_headless = False
    if platform.system() == "Linux":
        is_headless = not os.environ.get("DISPLAY") or os.environ.get("BROWSER_HEADLESS") == "True"
    
    if is_headless:
        raise HTTPException(
            status_code=400,
            detail="Hệ thống đang chạy trong môi trường Headless (Docker/VPS). Vui lòng dán đường dẫn thủ công."
        )
        
    try:
        with ThreadPoolExecutor() as executor:
            future = executor.submit(_picker)
            selected_path = future.result(timeout=60) # Chờ tối đa 60 giây
            
        if selected_path:
            # Chuẩn hóa định dạng dấu gạch chéo của Windows/Linux
            normalized_path = os.path.abspath(selected_path)
            return {"status": "SUCCESS", "path": normalized_path}
        return {"status": "CANCELLED", "path": ""}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Không thể mở bộ chọn thư mục: {str(e)}. Vui lòng dán đường dẫn thủ công."
        )


@router.post("/upload-avatars", status_code=status.HTTP_200_OK)
async def upload_avatars_folder(
    files: List[UploadFile] = File(...)
):
    """
    API Thương mại cao cấp: Tải lên cả thư mục ảnh đại diện từ Web UI.
    Hệ thống sẽ lưu trữ tập trung trên máy chủ và trả về đường dẫn tuyệt đối 
    để tự động điền vào cấu hình luồng chạy, bypass giới hạn bảo mật đường dẫn của trình duyệt.
    """
    try:
        # Đường dẫn lưu trữ ảnh đại diện tập trung ngay trong thư mục dự án backend
        upload_dir = os.path.join(os.getcwd(), "uploaded_avatars")
        os.makedirs(upload_dir, exist_ok=True)
        
        saved_count = 0
        for file in files:
            if not file.filename:
                continue
            
            # Chỉ lọc lấy định dạng ảnh phổ biến
            ext = file.filename.lower().split('.')[-1]
            if ext in ['jpg', 'jpeg', 'png', 'webp', 'heic']:
                # Trích xuất tên tệp an toàn để lưu
                safe_filename = os.path.basename(file.filename)
                target_path = os.path.join(upload_dir, safe_filename)
                
                # Sao chép file nhị phân vào ổ đĩa máy chủ
                with open(target_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
                saved_count += 1
                
        return {
            "status": "SUCCESS",
            "avatar_folder_path": upload_dir,
            "message": f"Đã nạp thành công {saved_count} ảnh đại diện lên máy chủ tại: {upload_dir}"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi lưu trữ ảnh đại diện trên máy chủ: {str(e)}"
        )