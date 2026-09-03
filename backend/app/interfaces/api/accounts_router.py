# File: backend/app/interfaces/api/accounts_router.py
import json
import logging
import os
import shutil
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Body, status, UploadFile, File
from pydantic import BaseModel

from app.domain.ports.repository import IAccountRepository, IProxyRepository
from app.domain.entities.account import TikTokAccount
from app.interfaces.api.deps import get_account_repository, get_proxy_repository
from app.interfaces.dto.account_dto import AccountCreateIn, AccountOut
from app.infrastructure.websocket.socket_manager import ws_manager
from app.core.cookie_utils import parse_cookies_any, cookies_to_string
from app.domain.account_rules import is_sold_account
from app.infrastructure.database.connection import engine
from app.infrastructure.database.schemas import TikTokVideoMetricDbTable
from sqlmodel import Session, select

logger = logging.getLogger("AccountsRouter")
router = APIRouter(prefix="/accounts", tags=["Accounts"])


def _performance_fields(account: TikTokAccount) -> dict:
    """Một nguồn ánh xạ duy nhất để mọi response không làm rơi số liệu KPI."""
    return {
        "upload_success_count": account.upload_success_count,
        "upload_failure_count": account.upload_failure_count,
        "last_upload_status": account.last_upload_status,
        "last_upload_at": account.last_upload_at,
        "last_upload_error": account.last_upload_error,
        "video_count": account.video_count,
        "follower_count": account.follower_count,
        "following_count": account.following_count,
        "likes_count": account.likes_count,
        "tiktok_user_id": account.tiktok_user_id,
        "tiktok_sec_uid": account.tiktok_sec_uid,
        "display_name": account.display_name,
        "bio": account.bio,
        "avatar_url": account.avatar_url,
        "verified": account.verified,
        "private_account": account.private_account,
        "website_url": account.website_url,
        "total_views": account.total_views,
        "total_video_likes": account.total_video_likes,
        "total_comments": account.total_comments,
        "total_shares": account.total_shares,
        "collected_video_count": account.collected_video_count,
        "analytics_sync_status": account.analytics_sync_status,
        "analytics_sync_source": account.analytics_sync_source,
        "analytics_sync_error": account.analytics_sync_error,
        "metrics_updated_at": account.metrics_updated_at,
        "is_sold": is_sold_account(account),
    }


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

    canonical_email = payload.email.strip().lower()
    new_account = TikTokAccount(
        id=canonical_email,
        username=payload.username,
        password=payload.password,
        email=canonical_email,
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
                "email": saved_account.email or "",
                "username": saved_account.username,
                "status": saved_account.status,
                "health_status": saved_account.health_status,
                "profile_status": saved_account.profile_status,
                "proxy_id": saved_account.proxy_id,
                "has_cookies": len(saved_account.cookies) > 0,
                "current_step": saved_account.current_step,
                "country": saved_account.country,
                "batch_tag": saved_account.batch_tag,
                "created_at": saved_account.created_at,
                **_performance_fields(saved_account),
            }
        })
        
        return AccountOut(
            id=saved_account.id,
            email=saved_account.email or "",
            username=saved_account.username,
            status=saved_account.status,
            health_status=saved_account.health_status,
            profile_status=saved_account.profile_status,
            current_step=saved_account.current_step,
            proxy_id=saved_account.proxy_id,
            has_cookies=len(saved_account.cookies) > 0,
            country=saved_account.country,
            batch_tag=saved_account.batch_tag,
            created_at=saved_account.created_at,
            **_performance_fields(saved_account),
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
            email=acc.email or "",
            username=acc.username,
            status=acc.status,
            health_status=acc.health_status,          # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
            profile_status=acc.profile_status,        # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
            current_step=acc.current_step,
            proxy_id=acc.proxy_id,
            has_cookies=len(acc.cookies) > 0,
            country=acc.country,                      # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
            batch_tag=acc.batch_tag,                  # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
            created_at=acc.created_at or "",          # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
            note=acc.note or "",
            **_performance_fields(acc),
        )
        for acc in accounts
    ]


def _find_existing_account(account_repo, email: str, username: str):
    """Tim account DA CO trong DB de UPDATE thay vi tao ban ghi moi.

    Doi chieu theo EMAIL truoc (email la dinh danh ON DINH - khong doi), roi moi
    den username. Ly do: TikTok hay TU DOI username (them duoi, vd 'stor128' ->
    'stor1285') va he thong dong bo lai username do vao DB; neu import lai file cu
    (van con username cu) ma chi so khop theo username thi se KHONG khop -> tao
    ban ghi MOI -> 1 account bi nhan doi trong DB (dung loi dang gap)."""
    email_n = (email or "").strip().lower()
    user_n = (username or "").strip().lower()
    by_user = None
    for acc in account_repo.get_all():
        if email_n and (acc.email or "").strip().lower() == email_n:
            return acc                      # khop EMAIL -> chac chan cung 1 account
        if user_n and (acc.username or "").strip().lower() == user_n:
            by_user = by_user or acc        # du phong: khop username
    return by_user


def _merge_into_existing(existing, *, username, password, email, email_password,
                         refresh_token, client_id, cookies, country, batch_tag):
    """Cap nhat account DA CO bang du lieu moi import, GIU LAI nhung gi quy hon.

    - GIU id, proxy_id, created_at, status/health/profile (tien trinh da chay).
    - GIU username trong DB neu ho so DA COMPLETED (luc do DB dang giu username
      THAT tren web, con file import co the con username CU) -> tranh lam hong.
    - Cookies: chi ghi de khi file co cookies (file khong co thi giu cookies cu).
    """
    if username and not (existing.profile_status == "COMPLETED" and existing.username):
        existing.username = username
    if password:
        existing.password = password
    if email:
        existing.email = email
    if email_password:
        existing.email_password = email_password
    if refresh_token:
        existing.refresh_token = refresh_token
    if client_id:
        existing.client_id = client_id
    if cookies:
        existing.cookies = cookies
    if country:
        existing.country = country
    if batch_tag:
        existing.batch_tag = batch_tag
    return existing


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
        # Toi thieu 6 truong: username|password|email|email_password|refresh_token|client_id
        # Truong thu 7 (cookies JSON) la TUY CHON - cac acc dang nhap bang
        # Credential+OTP khong can cookies san.
        if len(parts) < 6:
            raise HTTPException(
                status_code=400,
                detail="Định dạng dữ liệu không hợp lệ (cần tối thiểu 6 trường: username|password|email|email_password|refresh_token|client_id)."
            )

        username = parts[0].strip()
        password = parts[1].strip()
        email = parts[2].strip()
        email_password = parts[3].strip()
        refresh_token = parts[4].strip()
        client_id = parts[5].strip()
        cookies_raw = parts[6].strip() if len(parts) > 6 else ""

        # Chap nhan CA 2 dang: JSON (mang Playwright) HOAC chuoi 'a=b; c=d'.
        cookies = parse_cookies_any(cookies_raw)

        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not batch_tag:
            batch_tag = f"LÔ_{datetime.now().strftime('%Y%m%d')}"

        # CHONG NHAN DOI: neu account DA CO (theo email, roi den username) -> UPDATE
        # dung ban ghi do thay vi tao uuid moi.
        existing = _find_existing_account(account_repo, email, username)
        if existing is not None:
            merged = _merge_into_existing(
                existing, username=username, password=password, email=email,
                email_password=email_password, refresh_token=refresh_token,
                client_id=client_id, cookies=cookies,
                country=country.upper().strip(), batch_tag=batch_tag.strip(),
            )
            saved_account = account_repo.save(merged)
            await ws_manager.broadcast({
                "event": "ACCOUNT_UPDATED",
                "data": {"id": saved_account.id, "email": saved_account.email or "", "username": saved_account.username,
                         "has_cookies": len(saved_account.cookies) > 0},
            })
            logger.info(f"[Import] Da UPDATE account san co: {saved_account.username} ({email})")
            return {"status": "SUCCESS", "updated": True,
                    "username": saved_account.username, "id": saved_account.id}

        allocated_proxy_id = _get_least_used_proxy_id(account_repo, proxy_repo)
        if not email:
            raise HTTPException(status_code=400, detail="Email/Hotmail là bắt buộc vì đây là khóa chính.")
        account_id = email.strip().lower()

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
                "email": saved_account.email or "",
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
        skipped_existing_count = 0
        skipped_invalid_count = 0
        failed_count = 0
        # Snapshot keys once instead of scanning the full DB for every line.
        # New keys are added after each successful insert, so duplicates across
        # multiple selected files are skipped in the same import request too.
        existing_accounts = account_repo.get_all()
        known_emails = {
            (account.email or account.id or "").strip().lower()
            for account in existing_accounts
            if (account.email or account.id or "").strip()
        }
        known_usernames = {
            (account.username or "").strip().lower()
            for account in existing_accounts
            if (account.username or "").strip()
        }
        for file in files:
            content = await file.read()
            lines = content.decode("utf-8").splitlines()

            for line in lines:
                if not line.strip():
                    continue
                parts = line.strip().split("|")
                # Toi thieu 6 truong (cookies o truong 7 la TUY CHON). Cac dong
                # thieu truong bat buoc se bi bo qua.
                if len(parts) < 6:
                    skipped_invalid_count += 1
                    continue

                username = parts[0].strip()
                password = parts[1].strip()
                email = parts[2].strip()
                email_password = parts[3].strip()
                refresh_token = parts[4].strip()
                client_id = parts[5].strip()
                cookies_raw = parts[6].strip() if len(parts) > 6 else ""

                canonical_email = email.lower()
                canonical_username = username.lower()
                if not canonical_email:
                    skipped_invalid_count += 1
                    logger.warning("[Import] Bỏ qua dòng thiếu email/Hotmail (khóa chính).")
                    continue

                # File import is insert-only. Existing accounts are immutable:
                # do not overwrite username, password, cookies, status, country,
                # batch, proxy, or any accumulated metrics.
                if (
                    canonical_email in known_emails
                    or (canonical_username and canonical_username in known_usernames)
                ):
                    skipped_existing_count += 1
                    logger.info(
                        "[Import] Bỏ qua account đã có/trùng trong tệp: %s (%s)",
                        username,
                        canonical_email,
                    )
                    continue

                # Chap nhan CA 2 dang: JSON (mang Playwright) HOAC chuoi 'a=b; c=d'.
                cookies = parse_cookies_any(cookies_raw)

                allocated_proxy_id = _allocate_next_proxy(proxy_repo, account_repo)
                account_id = canonical_email

                account = TikTokAccount(
                    id=account_id,
                    username=username,
                    password=password,
                    email=canonical_email,
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
                    known_emails.add(canonical_email)
                    if canonical_username:
                        known_usernames.add(canonical_username)

                    await ws_manager.broadcast({
                        "event": "ACCOUNT_ADDED",
                        "data": {
                            "id": account.id,
                            "email": account.email or "",
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
                    failed_count += 1
                    if hasattr(account_repo, "session"):
                        account_repo.session.rollback()
                    continue

        msg = f"Đã nhập {imported_count} tài khoản MỚI vào {batch_tag}."
        if skipped_existing_count:
            msg += f" Bỏ qua {skipped_existing_count} tài khoản đã có/trùng trong tệp; dữ liệu cũ được giữ nguyên."
        if skipped_invalid_count:
            msg += f" Bỏ qua {skipped_invalid_count} dòng sai định dạng."
        if failed_count:
            msg += f" Có {failed_count} dòng lỗi khi lưu."
        return {
            "status": "SUCCESS",
            "imported": imported_count,
            "updated": 0,
            "skipped_existing": skipped_existing_count,
            "skipped_invalid": skipped_invalid_count,
            "failed": failed_count,
            "message": msg,
        }
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
    if is_sold_account(account):
        raise HTTPException(status_code=403, detail="Account ĐÃ BÁN chỉ được lưu trữ; không thay đổi proxy.")
    
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
        email=saved.email or "",
        username=saved.username,
        status=saved.status,
        health_status=saved.health_status,            # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
        profile_status=saved.profile_status,          # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
        current_step=saved.current_step,
        proxy_id=saved.proxy_id,
        has_cookies=len(saved.cookies) > 0,
        country=saved.country,                        # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
        batch_tag=saved.batch_tag,                    # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
        created_at=saved.created_at or "",            # <-- ĐÃ ĐỒNG BỘ SỬA LỖI
        note=saved.note or "",
        **_performance_fields(saved),
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
    skipped_sold = 0
    for acc_id in account_ids:
        account = account_repo.get_by_id(acc_id)
        if not account:
            continue
        if is_sold_account(account):
            skipped_sold += 1
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
        "allocated": allocated_count,
        "skipped_sold": skipped_sold,
        "message": f"Đã phân bổ Proxy cho {allocated_count} tài khoản; bỏ qua {skipped_sold} account ĐÃ BÁN."
    }


def _allocate_next_proxy(proxy_repo: IProxyRepository, account_repo: IAccountRepository) -> Optional[str]:
    """Helper phân bổ Proxy tải trọng nhẹ nhất"""
    try:
        return _get_least_used_proxy_id(account_repo, proxy_repo)
    except Exception:
        return None


@router.get("/{account_id}/analytics")
async def get_account_analytics(
    account_id: str,
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    """Detailed, server-verified Studio metrics for one Hotmail account."""
    account = account_repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
    with Session(engine) as session:
        rows = session.exec(
            select(TikTokVideoMetricDbTable)
            .where(TikTokVideoMetricDbTable.account_email == account.id)
            .order_by(TikTokVideoMetricDbTable.create_time.desc())
        ).all()
    videos = [
        {
            "video_id": row.video_id,
            "title": row.title,
            "create_time": row.create_time,
            "view_count": row.view_count,
            "like_count": row.like_count,
            "comment_count": row.comment_count,
            "share_count": row.share_count,
            "cover_url": row.cover_url,
            "share_url": row.share_url,
            "synced_at": row.synced_at,
        }
        for row in rows
    ]
    return {
        "account_id": account.id,
        "is_sold": is_sold_account(account),
        "sync_status": account.analytics_sync_status,
        "sync_source": account.analytics_sync_source,
        "sync_error": account.analytics_sync_error,
        "metrics_updated_at": account.metrics_updated_at,
        "collected_video_count": account.collected_video_count,
        "profile_video_count": account.video_count,
        "profile": {
            "follower_count": account.follower_count,
            "following_count": account.following_count,
            "likes_count": account.likes_count,
        },
        "totals": {
            "views": account.total_views,
            "likes": account.total_video_likes,
            "comments": account.total_comments,
            "shares": account.total_shares,
        },
        "videos": videos,
    }


@router.delete("/{account_id}", status_code=status.HTTP_200_OK)
async def delete_account(
    account_id: str,
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """API Xóa tài khoản đơn lẻ"""
    success = account_repo.delete(account_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản để xóa.")
    # Per-video rows use the Hotmail primary key and are deliberately removed
    # with the owning account to avoid invisible orphaned analytics history.
    with Session(engine) as metrics_session:
        metric_rows = metrics_session.exec(
            select(TikTokVideoMetricDbTable)
            .where(TikTokVideoMetricDbTable.account_email == account_id)
        ).all()
        for metric_row in metric_rows:
            metrics_session.delete(metric_row)
        metrics_session.commit()
    
    # Gửi tín hiệu thông báo đến toàn bộ Client qua Websocket
    await ws_manager.broadcast({
        "event": "ACCOUNT_DELETED",
        "data": {"id": account_id}
    })
    return {"status": "SUCCESS", "message": "Đã xóa tài khoản thành công."}


class UpdateAccountRequest(BaseModel):
    """Cac truong CO THE SUA truc tiep tren UI (chi cap nhat truong duoc truyen)."""
    username: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None
    email_password: Optional[str] = None
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    country: Optional[str] = None
    batch_tag: Optional[str] = None
    note: Optional[str] = None
    # Trang thai (sua tay tren UI): PENDING/COMPLETED, ALIVE/BANNED/UNKNOWN, IDLE/SUCCESS/ERROR...
    profile_status: Optional[str] = None
    health_status: Optional[str] = None
    status: Optional[str] = None


@router.patch("/{account_id}", response_model=AccountOut)
async def update_account_fields(
    account_id: str,
    payload: UpdateAccountRequest,
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """Cap nhat truc tiep 1 hoac nhieu truong cua account (sua tren UI). Chi cap
    nhat truong duoc gui len (exclude_unset). Phat WebSocket de UI dong bo ngay."""
    account = account_repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")

    old_account_id = account.id
    data = payload.model_dump(exclude_unset=True)
    changed = {}
    for field, value in data.items():
        if value is None:
            continue
        val = value.strip() if isinstance(value, str) else value
        if field == "country":
            val = str(val).upper().strip()
        elif field == "email":
            val = str(val).lower().strip()
        setattr(account, field, val)
        changed[field] = val

    if not changed:
        raise HTTPException(status_code=400, detail="Không có trường nào để cập nhật.")

    try:
        saved = account_repo.save(account)
    except Exception as e:
        # Thuong do trung username (unique) -> rollback + bao loi ro rang.
        if hasattr(account_repo, "session"):
            try: account_repo.session.rollback()
            except Exception: pass
        raise HTTPException(status_code=400, detail=f"Không thể cập nhật (có thể trùng username): {str(e)}")

    # Email is the primary key. Keep every per-video metric attached when the
    # user corrects a Hotmail address instead of silently orphaning the rows.
    if saved.id != old_account_id and hasattr(account_repo, "session"):
        metric_rows = account_repo.session.exec(
            select(TikTokVideoMetricDbTable)
            .where(TikTokVideoMetricDbTable.account_email == old_account_id)
        ).all()
        for metric_row in metric_rows:
            metric_row.account_email = saved.id
            account_repo.session.add(metric_row)
        account_repo.session.commit()

    if "batch_tag" in changed:
        changed["is_sold"] = is_sold_account(saved)

    await ws_manager.broadcast({
        "event": "ACCOUNT_UPDATED",
        "data": {"id": account_id, **changed},
    })
    return AccountOut(
        id=saved.id, email=saved.email or "", username=saved.username, status=saved.status,
        health_status=saved.health_status, profile_status=saved.profile_status,
        current_step=saved.current_step, proxy_id=saved.proxy_id,
        has_cookies=len(saved.cookies) > 0, country=saved.country,
        batch_tag=saved.batch_tag, created_at=saved.created_at or "",
        note=saved.note or "",
        **_performance_fields(saved),
    )


@router.post("/move-to-group", status_code=status.HTTP_200_OK)
async def move_accounts_to_group(
    account_ids: List[str] = Body(..., embed=True),
    batch_tag: str = Body(..., embed=True),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """CHUYEN cac account da chon sang 1 CUM (batch_tag) moi hoac co san. Dung de
    gom nhom theo doi. Phat WebSocket ACCOUNT_UPDATED cho tung account de UI (bang
    + cay Lo) tu di chuyen ngay, khong can reload."""
    target = (batch_tag or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="Tên cụm (Lô) không được để trống.")
    if not account_ids:
        raise HTTPException(status_code=400, detail="Chưa chọn tài khoản nào để chuyển.")

    moved = 0
    for acc_id in account_ids:
        account = account_repo.get_by_id(acc_id)
        if not account:
            continue
        account.batch_tag = target
        account_repo.save(account)
        moved += 1
        await ws_manager.broadcast({
            "event": "ACCOUNT_UPDATED",
            "data": {"id": acc_id, "batch_tag": target, "is_sold": is_sold_account(account)},
        })

    return {
        "status": "SUCCESS",
        "moved": moved,
        "batch_tag": target,
        "message": f"Đã chuyển {moved} tài khoản sang cụm '{target}'.",
    }


@router.post("/clear-cookies", status_code=status.HTTP_200_OK)
async def clear_cookies(
    account_ids: List[str] = Body(..., embed=True),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """XOA COOKIES cua cac account da chon (giu nguyen account, chi xoa cookies ->
    lan sau bat buoc login lai bang Credential+OTP)."""
    cleared = 0
    skipped_sold = 0
    for aid in account_ids:
        account = account_repo.get_by_id(aid)
        if not account:
            continue
        if is_sold_account(account):
            skipped_sold += 1
            continue
        account.cookies = []
        account_repo.save(account)
        cleared += 1
        await ws_manager.broadcast({
            "event": "ACCOUNT_UPDATED",
            "data": {"id": aid, "has_cookies": False},
        })
    return {"status": "SUCCESS", "cleared": cleared, "skipped_sold": skipped_sold,
            "message": f"Đã xóa cookies của {cleared} tài khoản; bỏ qua {skipped_sold} account ĐÃ BÁN."}


@router.post("/export", status_code=status.HTTP_200_OK)
async def export_accounts(
    account_ids: List[str] = Body(..., embed=True),
    quantity: Optional[int] = Body(default=None, embed=True),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """Xuat account ra file txt (dinh dang DAY DU, import lai duoc) roi XOA khoi DB.

    - account_ids: kho ung vien (acc DA CHON, hoac toan bo acc trong LO dang hien).
    - quantity: None = xuat het account_ids; N = chi lay N acc DAU trong kho.
      Neu N > so acc co san -> lay HET so co san (co co took_all_available=True).

    Tra ve noi dung file + so lieu de UI hien popup (da lay bao nhieu / con lai bao nhieu).
    File dinh dang: username|password|email|email_password|refresh_token|client_id|cookies
    (cookies xuat ra dang CHUOI 'name=value; name=value; ...' giong file test_cookies.txt).
    """
    # 1. Loc ra cac account hop le (con ton tai trong DB), giu dung thu tu truyen vao.
    pool = []
    for aid in account_ids:
        acc = account_repo.get_by_id(aid)
        if acc:
            pool.append(acc)

    available = len(pool)
    if available == 0:
        raise HTTPException(status_code=400, detail="Không có tài khoản hợp lệ nào để xuất.")

    # 2. Xac dinh so luong lay ra.
    if quantity is None:
        take = pool
    else:
        take = pool[: max(0, quantity)]
    took = len(take)
    took_all_available = quantity is not None and quantity > available

    # 3. Dung noi dung file (dinh dang day du = giong format import).
    #    Cookies xuat ra dang CHUOI 'name=value; ...' (giong file test_cookies.txt),
    #    KHONG phai JSON. Import chap nhan lai duoc (parse_cookies_any nhan ca 2 dang).
    lines = []
    for acc in take:
        cookies_str = cookies_to_string(acc.cookies or [])
        line = "|".join([
            acc.username or "",
            acc.password or "",
            acc.email or "",
            acc.email_password or "",
            acc.refresh_token or "",
            acc.client_id or "",
            cookies_str,
        ])
        lines.append(line)
    content = "\n".join(lines)

    # 4. XOA cac account da xuat khoi DB (theo lua chon: xuat = lay ra khoi kho).
    for acc in take:
        try:
            account_repo.delete(acc.id)
            await ws_manager.broadcast({"event": "ACCOUNT_DELETED", "data": {"id": acc.id}})
        except Exception as e:
            logger.warning(f"Lỗi khi xóa acc {acc.id} sau khi xuất: {str(e)}")

    filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{took}acc.txt"
    return {
        "status": "SUCCESS",
        "content": content,
        "filename": filename,
        "exported_count": took,
        "available_before": available,
        "remaining_count": available - took,
        "requested": quantity,
        "took_all_available": took_all_available,
    }


@router.post("/copy", status_code=status.HTTP_200_OK)
async def copy_accounts(
    account_ids: List[str] = Body(..., embed=True),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """COPY account ra chuoi (GIONG format export) NHUNG KHONG XOA khoi DB.
    Dung de sao chep acc ra clipboard ma van GIU acc trong app.
    Dinh dang moi dong: username|password|email|email_password|refresh_token|client_id|cookies
    (cookies dang chuoi 'name=value; ...' giong test_cookies.txt)."""
    lines = []
    count = 0
    for aid in account_ids:
        acc = account_repo.get_by_id(aid)
        if not acc:
            continue
        cookies_str = cookies_to_string(acc.cookies or [])
        line = "|".join([
            acc.username or "",
            acc.password or "",
            acc.email or "",
            acc.email_password or "",
            acc.refresh_token or "",
            acc.client_id or "",
            cookies_str,
        ])
        lines.append(line)
        count += 1
    if count == 0:
        raise HTTPException(status_code=400, detail="Không có tài khoản hợp lệ nào để copy.")
    return {"status": "SUCCESS", "content": "\n".join(lines), "copied_count": count}


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
