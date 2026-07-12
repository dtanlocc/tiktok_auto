# File: backend/app/interfaces/api/interactions_router.py
import os
import platform
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher
from app.infrastructure.scheduler.interaction_scheduler import InteractionScheduler
from app.interfaces.api.deps import get_task_dispatcher, get_interaction_scheduler, get_account_repository
from app.domain.ports.repository import IAccountRepository

router = APIRouter(prefix="/interactions", tags=["Interactions"])


def _load_comment_list(comment_file_path: Optional[str]) -> List[str]:
    """Đọc danh sách câu bình luận từ file .txt (mỗi dòng 1 câu, bỏ dòng trống)."""
    if not comment_file_path:
        return []
    if not os.path.exists(comment_file_path):
        raise HTTPException(status_code=400, detail=f"Không tìm thấy file bình luận: {comment_file_path}")
    try:
        with open(comment_file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [line.strip() for line in f.readlines()]
        return [line for line in lines if line]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi đọc file bình luận: {str(e)}")


class InteractionConfig(BaseModel):
    account_ids: List[str]
    mode: str = "foryou"                    # "foryou" hoặc "hashtag"
    hashtag: Optional[str] = None
    duration_minutes: int = 10
    like_probability: float = 0.4
    comment_probability: float = 0.05
    comment_file_path: Optional[str] = None
    min_watch_seconds: float = 3.0
    max_watch_seconds: float = 15.0
    concurrency_limit: int = 4


class ScheduleConfig(InteractionConfig):
    interval_minutes: int = 60


# =============================================================================
# CHỌN FILE BÌNH LUẬN (.txt) - MỞ OS FILE PICKER, TÁI DÙNG PATTERN
# GIỐNG select-local-folder BÊN accounts_router.py
# =============================================================================
@router.post("/select-comment-file")
def select_comment_file():
    """Mở cửa sổ chọn file .txt hệ thống (OS File Picker) để chọn danh sách câu bình luận."""

    def _picker():
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        file_path = filedialog.askopenfilename(
            title="Chọn file danh sách câu bình luận (.txt, mỗi dòng 1 câu)",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        root.destroy()
        return file_path

    is_headless = False
    if platform.system() == "Linux":
        is_headless = not os.environ.get("DISPLAY")

    if is_headless:
        raise HTTPException(
            status_code=400,
            detail="Hệ thống đang chạy trong môi trường Headless (Docker/VPS). Vui lòng dán đường dẫn thủ công.",
        )

    try:
        with ThreadPoolExecutor() as executor:
            future = executor.submit(_picker)
            selected_path = future.result(timeout=60)

        if selected_path:
            normalized_path = os.path.abspath(selected_path)
            comment_count = len(_load_comment_list(normalized_path))
            return {"status": "SUCCESS", "path": normalized_path, "comment_count": comment_count}
        return {"status": "CANCELLED", "path": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi mở bộ chọn file: {str(e)}")


# =============================================================================
# CHẠY 1 LẦN (KHÔNG LẶP LỊCH) - đẩy thẳng vào Task Dispatcher chung
# =============================================================================
@router.post("/run-once")
async def run_interaction_once(
    payload: InteractionConfig,
    dispatcher: ConcurrentTaskDispatcher = Depends(get_task_dispatcher),
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản.")
    if payload.mode == "hashtag" and not payload.hashtag:
        raise HTTPException(status_code=400, detail="Chế độ hashtag cần nhập từ khóa hashtag.")

    comment_list = _load_comment_list(payload.comment_file_path)

    dispatcher.set_concurrency_limit(payload.concurrency_limit)
    extra_config = {
        "mode": payload.mode,
        "hashtag": payload.hashtag,
        "duration_minutes": payload.duration_minutes,
        "like_probability": payload.like_probability,
        "comment_probability": payload.comment_probability,
        "comment_list": comment_list,
        "min_watch_seconds": payload.min_watch_seconds,
        "max_watch_seconds": payload.max_watch_seconds,
    }

    queued_count = 0
    for acc_id in payload.account_ids:
        account = account_repo.get_by_id(acc_id)
        if not account:
            continue
        await dispatcher.submit_task(
            account_id=acc_id,
            task_type="INTERACT_VIDEOS",
            extra_config=extra_config,
        )
        queued_count += 1

    return {
        "status": "SUCCESS",
        "message": f"Đã xếp hàng tương tác video ({payload.duration_minutes} phút) cho {queued_count} tài khoản.",
        "comment_count_loaded": len(comment_list),
    }


# =============================================================================
# LẬP LỊCH LẶP CHU KỲ: chạy X phút rồi tự dừng, lặp lại sau mỗi Y phút
# =============================================================================
@router.post("/schedule")
async def create_schedule(
    payload: ScheduleConfig,
    scheduler: InteractionScheduler = Depends(get_interaction_scheduler),
    account_repo: IAccountRepository = Depends(get_account_repository),
):
    if not payload.account_ids:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tài khoản.")
    if payload.mode == "hashtag" and not payload.hashtag:
        raise HTTPException(status_code=400, detail="Chế độ hashtag cần nhập từ khóa hashtag.")
    if payload.interval_minutes <= 0:
        raise HTTPException(status_code=400, detail="Chu kỳ lặp lại (interval_minutes) phải lớn hơn 0.")

    # Lọc account tồn tại thật trong DB
    valid_ids = [aid for aid in payload.account_ids if account_repo.get_by_id(aid)]
    if not valid_ids:
        raise HTTPException(status_code=400, detail="Không có tài khoản hợp lệ nào trong danh sách đã chọn.")

    comment_list = _load_comment_list(payload.comment_file_path)

    schedule_id = scheduler.create_schedule(
        account_ids=valid_ids,
        mode=payload.mode,
        hashtag=payload.hashtag,
        duration_minutes=payload.duration_minutes,
        interval_minutes=payload.interval_minutes,
        like_probability=payload.like_probability,
        comment_probability=payload.comment_probability,
        comment_list=comment_list,
        min_watch_seconds=payload.min_watch_seconds,
        max_watch_seconds=payload.max_watch_seconds,
    )

    return {
        "status": "SUCCESS",
        "schedule_id": schedule_id,
        "message": (
            f"Đã tạo lịch: chạy {payload.duration_minutes} phút, "
            f"lặp lại mỗi {payload.interval_minutes} phút, cho {len(valid_ids)} tài khoản."
        ),
    }


@router.get("/schedules")
async def list_schedules(
    scheduler: InteractionScheduler = Depends(get_interaction_scheduler),
):
    return scheduler.list_schedules()


@router.post("/schedule/{schedule_id}/pause")
async def pause_schedule(
    schedule_id: str,
    scheduler: InteractionScheduler = Depends(get_interaction_scheduler),
):
    if not scheduler.pause_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch này.")
    return {"status": "SUCCESS", "message": "Đã tạm dừng lịch."}


@router.post("/schedule/{schedule_id}/resume")
async def resume_schedule(
    schedule_id: str,
    scheduler: InteractionScheduler = Depends(get_interaction_scheduler),
):
    if not scheduler.resume_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch này.")
    return {"status": "SUCCESS", "message": "Đã tiếp tục lịch."}


@router.delete("/schedule/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    scheduler: InteractionScheduler = Depends(get_interaction_scheduler),
):
    if not scheduler.delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch này.")
    return {"status": "SUCCESS", "message": "Đã xoá lịch."}
