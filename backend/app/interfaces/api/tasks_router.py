from typing import List, Optional
import asyncio
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher
from app.interfaces.api.deps import get_task_dispatcher, get_account_repository
from app.domain.ports.repository import IAccountRepository
from app.use_cases.health_check.quick_check_use_case import quick_health_check_service
from app.use_cases.analytics.tiktok_fast_analytics_sync import fast_analytics_sync_service
from app.use_cases.debug.debug_login_service import debug_login_service
from app.core.config import settings
from app.use_cases.upload.media_selection import select_preferred_media
from app.use_cases.upload.video_library import scan_video_paths
from app.domain.account_rules import is_sold_account


router = APIRouter(prefix="/tasks", tags=["Tasks"])


class ProxyModeRequest(BaseModel):
    use_proxy: bool     # True = auto-map proxy (nhu cu); False = mang that (khong proxy)


@router.get("/proxy-mode")
async def get_proxy_mode():
    """Che do proxy hien tai (runtime). True = dung proxy (auto-map); False = mang
    that (khong proxy, dung khi bat VPN toan may)."""
    return {"use_proxy": bool(getattr(settings, "USE_PROXY", True))}


@router.post("/proxy-mode")
async def set_proxy_mode(payload: ProxyModeRequest):
    """Doi che do proxy NGAY (khong can restart backend). Dispatcher + debug-login
    doc settings.USE_PROXY luc chay nen co hieu luc cho cac phien MO SAU do."""
    settings.USE_PROXY = payload.use_proxy
    return {
        "status": "SUCCESS",
        "use_proxy": settings.USE_PROXY,
        "message": ("Đã bật auto-map proxy (dùng proxy như cũ)." if payload.use_proxy
                    else "Đã chuyển sang MẠNG THẬT (không proxy) — dùng VPN toàn máy."),
    }

class BulkLoginRequest(BaseModel):
    account_ids: List[str]
    login_method: str = "COOKIE" # COOKIE hoặc CREDENTIAL
    # So luong chay DONG THOI TREN MOI PROXY (thay cho "so luong tong" truoc day).
    proxy_concurrency: int = 8

class BulkUpdateProfileRequest(BaseModel):
    account_ids: List[str]
    avatar_folder: Optional[str] = None
    proxy_concurrency: int = 8


class ProxyConcurrencyRequest(BaseModel):
    limit: int = 8


class BulkUploadVideoRequest(BaseModel):
    account_ids: List[str]
    image_path: Optional[str] = None      # file ảnh hoặc thư mục ảnh; luôn ưu tiên
    video_path: Optional[str] = None      # video dự phòng nếu không có ảnh hợp lệ
    caption: str = ""
    schedule_at: Optional[str] = None      # 'YYYY-MM-DD HH:MM' -> đặt lịch; None -> đăng ngay
    proxy_concurrency: int = 8


class VideoLibraryScanRequest(BaseModel):
    paths: List[str]


class VideoBatchRequest(BaseModel):
    account_ids: List[str]
    video_paths: List[str]
    videos_per_account: int = 1
    proxy_concurrency: int = 8

class QuickHealthCheckRequest(BaseModel):
    account_ids: List[str]
    concurrency_limit: int = 5


class AnalyticsSyncRequest(BaseModel):
    account_ids: List[str]
    concurrency_limit: int = 12
    force: bool = False


def _operational_accounts(account_repo: IAccountRepository, account_ids: List[str]):
    valid = []
    sold = []
    seen = set()
    for account_id in account_ids:
        canonical = str(account_id or "").strip().lower()
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        account = account_repo.get_by_id(canonical)
        if not account:
            continue
        (sold if is_sold_account(account) else valid).append(account)
    return valid, sold

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
    operational, sold = _operational_accounts(account_repo, payload.account_ids)
    queued_count = 0
    for account in operational:

        # Đẩy tác vụ LOGIN vào hàng đợi
        await dispatcher.submit_task(
            account_id=account.id,
            task_type=f"LOGIN_{payload.login_method}", # LOGIN_COOKIE hoặc LOGIN_CREDENTIAL
            avatar_folder=None
        )
        queued_count += 1
        
    return {"status": "SUCCESS", "queued": queued_count, "skipped_sold": len(sold), "message": f"Đã xếp hàng {queued_count} tài khoản; bỏ qua {len(sold)} tài khoản ĐÃ BÁN."}

@router.post("/bulk-upload-video")
@router.post("/bulk-upload-media")
async def start_bulk_upload_media(
    payload: BulkUploadVideoRequest,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher),
    account_repo: IAccountRepository = Depends(get_account_repository)
):
    """Đăng ảnh ưu tiên hoặc video dự phòng lên các tài khoản đã chọn."""
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản.")
    try:
        selected = select_preferred_media(payload.image_path, payload.video_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    dispatcher.set_proxy_concurrency_limit(payload.proxy_concurrency)
    extra = {
        "image_path": payload.image_path,
        "video_path": payload.video_path,
        "caption": payload.caption,
        "schedule_at": payload.schedule_at,
    }
    operational, sold = _operational_accounts(account_repo, payload.account_ids)
    queued = 0
    for account in operational:
        accepted = await dispatcher.submit_task(account_id=account.id, task_type="UPLOAD_MEDIA", extra_config=extra)
        queued += int(accepted)
    kind = "đặt lịch đăng" if payload.schedule_at else "đăng"
    media_name = f"{len(selected.image_paths)} ảnh" if selected.kind == "photo" else "video dự phòng"
    return {"status": "SUCCESS", "queued": queued, "skipped_sold": len(sold), "message": f"Đã xếp hàng {kind} {media_name} cho {queued} tài khoản; bỏ qua {len(sold)} tài khoản ĐÃ BÁN."}


@router.post("/video-library/scan")
async def scan_video_library(payload: VideoLibraryScanRequest):
    videos = scan_video_paths(payload.paths)
    return {"status": "SUCCESS", "videos": videos, "count": len(videos)}


def _pick_local_video_paths(pick_folder: bool) -> list[str]:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if pick_folder:
            selected = filedialog.askdirectory(title="Chọn thư mục video")
            return [selected] if selected else []
        selected = filedialog.askopenfilenames(
            title="Chọn nhiều video",
            filetypes=[("Video", "*.mp4 *.mov *.webm *.m4v"), ("Tất cả tệp", "*.*")],
        )
        return list(selected)
    finally:
        root.destroy()


@router.post("/video-library/pick-files")
async def pick_video_files():
    paths = await asyncio.to_thread(_pick_local_video_paths, False)
    videos = scan_video_paths(paths)
    return {"status": "SUCCESS" if videos else "CANCELLED", "videos": videos, "count": len(videos)}


@router.post("/video-library/pick-folder")
async def pick_video_folder():
    paths = await asyncio.to_thread(_pick_local_video_paths, True)
    videos = scan_video_paths(paths)
    return {"status": "SUCCESS" if videos else "CANCELLED", "videos": videos, "count": len(videos)}


@router.post("/video-batches")
async def create_video_batch(
    payload: VideoBatchRequest,
    request: Request,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher),
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    account_emails = [value.strip().lower() for value in dict.fromkeys(payload.account_ids) if value.strip()]
    if not account_emails:
        raise HTTPException(status_code=400, detail="Chọn ít nhất một Hotmail.")
    operational, sold = _operational_accounts(account_repo, account_emails)
    valid_emails = [account.id for account in operational]
    if not valid_emails:
        raise HTTPException(status_code=400, detail="Không tìm thấy Hotmail hợp lệ.")
    videos = scan_video_paths(payload.video_paths)
    if not videos:
        raise HTTPException(status_code=400, detail="Không tìm thấy video hợp lệ (.mp4/.mov/.webm/.m4v).")
    if payload.videos_per_account < 1:
        raise HTTPException(status_code=400, detail="Số video mỗi account phải lớn hơn 0.")
    if payload.videos_per_account > len(videos):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cần ít nhất {payload.videos_per_account} video khác nhau; "
                f"kho hiện chỉ có {len(videos)} video."
            ),
        )
    dispatcher.set_proxy_concurrency_limit(payload.proxy_concurrency)
    batch = request.app.state.video_batch.add(
        valid_emails,
        [video["path"] for video in videos],
        videos_per_account=payload.videos_per_account,
    )
    total_assignments = len(valid_emails) * payload.videos_per_account
    return {
        "status": "SUCCESS",
        "batch": batch,
        "skipped_sold": len(sold),
        "message": (
            f"Đã ghép {total_assignments} lượt đăng: "
            f"{payload.videos_per_account} video không trùng cho mỗi "
            f"{len(valid_emails)} Hotmail; video được phép dùng lại ở Hotmail khác, "
            f"bỏ qua {len(sold)} tài khoản ĐÃ BÁN."
        ),
    }


@router.get("/video-batches")
async def list_video_batches(request: Request):
    return {"batches": request.app.state.video_batch.list()}


@router.delete("/video-batches/{batch_id}")
async def cancel_video_batch(batch_id: str, request: Request):
    if not request.app.state.video_batch.cancel(batch_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt đang chờ hoặc đang chạy.")
    return {"status": "SUCCESS", "message": "Đã dừng cấp thêm video; task hiện tại sẽ hoàn tất an toàn."}


class ScheduleUploadRequest(BaseModel):
    account_ids: List[str]
    image_path: Optional[str] = None
    video_path: Optional[str] = None
    caption: str = ""
    run_at: str                            # ISO 'YYYY-MM-DDTHH:MM' (giờ máy chạy)


@router.post("/schedule-upload-video")
@router.post("/schedule-upload-media")
async def schedule_upload_media(
    payload: ScheduleUploadRequest,
    request: Request,
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    """Hẹn giờ đăng ảnh/video phía app; tới giờ mới tạo task UPLOAD_MEDIA."""
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản.")
    try:
        selected = select_preferred_media(payload.image_path, payload.video_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        run_at = datetime.fromisoformat(payload.run_at)
    except Exception:
        raise HTTPException(status_code=400, detail="run_at không hợp lệ (cần 'YYYY-MM-DDTHH:MM').")
    if run_at <= datetime.now():
        raise HTTPException(status_code=400, detail="Thời điểm hẹn phải ở tương lai.")
    operational, sold = _operational_accounts(account_repo, payload.account_ids)
    if not operational:
        raise HTTPException(status_code=400, detail=f"Không có account để hẹn đăng; bỏ qua {len(sold)} tài khoản ĐÃ BÁN.")
    operational_ids = [account.id for account in operational]
    svc = request.app.state.scheduled_upload
    job_id = svc.add(
        operational_ids,
        payload.video_path,
        payload.caption,
        run_at,
        image_path=payload.image_path,
    )
    media_name = f"{len(selected.image_paths)} ảnh" if selected.kind == "photo" else "video"
    return {"status": "SUCCESS", "job_id": job_id,
            "skipped_sold": len(sold),
            "message": f"Đã hẹn đăng {media_name} cho {len(operational_ids)} tài khoản; bỏ qua {len(sold)} ĐÃ BÁN, lúc {run_at.strftime('%H:%M %d/%m')}."}


@router.get("/scheduled-uploads")
async def list_scheduled_uploads(request: Request):
    return {"jobs": request.app.state.scheduled_upload.list()}


@router.delete("/scheduled-uploads/{job_id}")
async def cancel_scheduled_upload(job_id: str, request: Request):
    ok = request.app.state.scheduled_upload.remove(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch.")
    return {"status": "SUCCESS", "message": "Đã hủy lịch."}


# =============================================================================
# TRINH DUYET TRANG (khong account) - de test tay captcha/thao tac
# =============================================================================
class BlankBrowserRequest(BaseModel):
    url: str = "about:blank"              # trang muon mo san (de trong = trang trang)
    proxy_id: Optional[str] = None        # None = truc tiep; co id = chay qua proxy do


@router.post("/debug-blank")
async def start_blank_browser(payload: BlankBrowserRequest):
    """Mo 1 trinh duyet HIEN, co cac extension ngoai da cau hinh nhung
    KHONG nap cookies/khong dang nhap account nao -> test tay thoai mai."""
    if debug_login_service.is_blank_running():
        raise HTTPException(status_code=400, detail="Trình duyệt trắng đang mở rồi.")
    debug_login_service.start_blank(payload.url or "about:blank", payload.proxy_id)
    return {"status": "SUCCESS", "message": "Đang mở trình duyệt trắng (chỉ có extension captcha)..."}


@router.post("/debug-blank/stop")
async def stop_blank_browser():
    ok = await debug_login_service.stop_blank()
    if not ok:
        raise HTTPException(status_code=404, detail="Không có trình duyệt trắng nào đang mở.")
    return {"status": "SUCCESS", "message": "Đã đóng trình duyệt trắng."}


@router.get("/debug-blank/active")
async def blank_browser_active():
    return {"active": debug_login_service.is_blank_running()}


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
    operational, sold = _operational_accounts(account_repo, payload.account_ids)
    queued_count = 0
    for account in operational:

        # Đẩy tác vụ UPDATE_PROFILE vào hàng đợi
        await dispatcher.submit_task(
            account_id=account.id,
            task_type="UPDATE_PROFILE",
            avatar_folder=payload.avatar_folder
        )
        queued_count += 1
        
    return {"status": "SUCCESS", "queued": queued_count, "skipped_sold": len(sold), "message": f"Đã xếp hàng cập nhật {queued_count} tài khoản; bỏ qua {len(sold)} tài khoản ĐÃ BÁN."}


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
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher),
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    """Tạm dừng riêng 1 tài khoản đang chạy để can thiệp thủ công, các tài
    khoản khác không bị ảnh hưởng."""
    if is_sold_account(account_repo.get_by_id(account_id)):
        raise HTTPException(status_code=403, detail="Account ĐÃ BÁN chỉ lưu trữ; không điều khiển tác vụ.")
    dispatcher.pause_account(account_id)
    await dispatcher.broadcast_account_pause_state(account_id)
    return {"status": "SUCCESS", "message": f"Đã tạm dừng tài khoản {account_id}."}


@router.post("/resume-account/{account_id}")
async def resume_account(
    account_id: str,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher),
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    """Tiếp tục lại 1 tài khoản đã bị tạm dừng riêng."""
    if is_sold_account(account_repo.get_by_id(account_id)):
        raise HTTPException(status_code=403, detail="Account ĐÃ BÁN chỉ lưu trữ; không điều khiển tác vụ.")
    dispatcher.resume_account(account_id)
    await dispatcher.broadcast_account_pause_state(account_id)
    return {"status": "SUCCESS", "message": f"Đã tiếp tục tài khoản {account_id}."}


# =============================================================================
# ĐỒNG BỘ HIỆU SUẤT NHANH (TÁCH RIÊNG HOÀN TOÀN - KHÔNG QUA DISPATCHER)
# Profile dùng HTTP hydration; tầng video dùng một signer invisible dùng chung.
# Không OAuth, không đăng nhập từng account và không mở Studio theo account.
# =============================================================================

@router.post("/sync-analytics")
async def start_analytics_sync(
    payload: AnalyticsSyncRequest,
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    operational, sold = _operational_accounts(account_repo, payload.account_ids)
    if not operational:
        raise HTTPException(status_code=400, detail=f"Không có account để đồng bộ; bỏ qua {len(sold)} tài khoản ĐÃ BÁN.")
    if not fast_analytics_sync_service.start_batch(
        [account.id for account in operational],
        concurrency_limit=max(1, min(payload.concurrency_limit, 24)),
        force=payload.force,
    ):
        raise HTTPException(status_code=409, detail="Đang có một đợt đồng bộ nhanh chạy dở.")
    return {
        "status": "SUCCESS", "queued": len(operational), "skipped_sold": len(sold),
        "mode": "PUBLIC_PROFILE",
        "message": (
            f"Đã bắt đầu đồng bộ nhanh {len(operational)} tài khoản; "
            "profile dùng HTTP công khai, chi tiết video dùng một browser ẩn dùng chung khi cần; "
            f"bỏ qua {len(sold)} tài khoản ĐÃ BÁN."
        ),
    }


@router.get("/sync-analytics/status")
async def get_analytics_sync_status():
    return fast_analytics_sync_service.get_status()

@router.post("/quick-health-check")
async def start_quick_health_check(
    payload: QuickHealthCheckRequest,
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    """Check ba tầng trực tiếp từ server TikTok (account-info/oEmbed/profile),
    độc lập hoàn toàn với hàng đợi/luồng đăng nhập chính. Trả về ngay lập tức, tiến độ
    được cập nhật qua WebSocket (event ACCOUNT_STATUS_CHANGED cho từng acc,
    và QUICK_CHECK_FINISHED khi xong toàn bộ đợt)."""
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản.")

    if quick_health_check_service.is_running:
        raise HTTPException(
            status_code=409,
            detail="Đang có 1 đợt Check nhanh chạy dở, vui lòng đợi hoàn tất trước khi chạy đợt mới."
        )

    operational, sold = _operational_accounts(account_repo, payload.account_ids)
    if not operational:
        raise HTTPException(status_code=400, detail=f"Không có account để check; bỏ qua {len(sold)} tài khoản ĐÃ BÁN.")
    ids = [account.id for account in operational]
    asyncio.create_task(quick_health_check_service.run_batch(ids, payload.concurrency_limit))

    return {
        "status": "SUCCESS",
        "skipped_sold": len(sold),
        "message": f"Đã bắt đầu Check nhanh cho {len(ids)} tài khoản; bỏ qua {len(sold)} tài khoản ĐÃ BÁN."
    }


@router.get("/quick-health-check/status")
async def get_quick_health_check_status():
    """Trạng thái tiến độ hiện tại của đợt Check nhanh (nếu đang chạy)."""
    return quick_health_check_service.get_status()


# =============================================================================
# CHẾ ĐỘ LIÊN TỤC: lặp lại Check nhanh cho đúng danh sách được chọn. Có cooldown
# chống rate-limit; hoàn toàn tách biệt Dispatcher và InteractionScheduler.
# =============================================================================
class ContinuousCheckRequest(BaseModel):
    account_ids: List[str]
    gap_seconds: int = 30
    concurrency_limit: int = 8


@router.post("/quick-health-check/start-continuous")
async def start_continuous_quick_check(
    payload: ContinuousCheckRequest,
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    """Bật quét lặp lại danh sách đã chọn, với cooldown 15-300 giây."""
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản trước khi bật Check nhanh liên tục.")
    operational, sold = _operational_accounts(account_repo, payload.account_ids)
    if not operational:
        raise HTTPException(status_code=400, detail=f"Không có account để check; bỏ qua {len(sold)} tài khoản ĐÃ BÁN.")
    started = quick_health_check_service.start_continuous(
        account_ids=[account.id for account in operational],
        gap_seconds=payload.gap_seconds,
        concurrency_limit=payload.concurrency_limit,
    )
    if not started:
        raise HTTPException(status_code=409, detail="Chế độ liên tục đã đang bật sẵn rồi.")
    return {
        "status": "SUCCESS",
        "message": (
            f"Đã bật Check nhanh liên tục cho {len(operational)} tài khoản, bỏ qua {len(sold)} ĐÃ BÁN "
            f"({quick_health_check_service.get_continuous_status()['concurrency_limit']} luồng tổng)."
        ),
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
async def start_debug_login(
    payload: DebugLoginRequest,
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    """Mo 1 phien DEBUG: trinh duyet HIEN len man hinh, tu dang nhap (uu tien
    cookie, fallback OTP) roi DUNG lai giu cua so mo de ban thao tac tay. Phien
    ket thuc khi ban DONG cua so hoac goi /debug-login/stop."""
    account = account_repo.get_by_id(payload.account_id)
    if is_sold_account(account):
        raise HTTPException(status_code=403, detail="Tài khoản thuộc mục ĐÃ BÁN; debug bị khóa để chỉ lưu trữ.")
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


@router.post("/screen-view-ping")
async def screen_view_ping():
    """Frontend (tab Màn Hình Trực Tiếp) gọi định kỳ khi đang mở. Chỉ khi có ping
    gần đây thì streamer mới chụp & gửi frame -> không ai xem thì không tốn CPU."""
    from app.infrastructure.streaming.screen_streamer import note_screen_view_ping
    note_screen_view_ping()
    return {"status": "OK"}
