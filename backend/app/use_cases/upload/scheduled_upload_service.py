# File: backend/app/use_cases/upload/scheduled_upload_service.py
"""
HẸN GIỜ ĐĂNG ẢNH/VIDEO PHÍA APP (không phụ thuộc điều kiện account của TikTok).
Lưu ý: TikTok có tuỳ chọn 'Lên lịch' riêng nhưng bị KHOÁ với nhiều nick (bot/mới)
-> ta tự hẹn giờ: tới giờ thì đẩy task UPLOAD_MEDIA vào dispatcher để đăng NGAY.
Dùng APScheduler (AsyncIOScheduler) chạy chung event loop với FastAPI.
"""
import uuid
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger("ScheduledUpload")


class ScheduledUploadService:
    """Quản lý lịch đăng media (in-memory + APScheduler)."""

    def __init__(self, dispatcher):
        self.dispatcher = dispatcher
        self.scheduler = AsyncIOScheduler()
        # id -> metadata (để UI liệt kê)
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("[+] ScheduledUploadService đã khởi động.")

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass

    async def _fire(self, job_id: str):
        """Tới giờ: đẩy task UPLOAD_MEDIA cho từng account."""
        meta = self._jobs.get(job_id)
        if not meta:
            return
        meta["status"] = "RUNNING"
        extra = {
            "image_path": meta.get("image_path"),
            "video_path": meta.get("video_path"),
            "caption": meta["caption"],
            "schedule_at": None,
        }
        submitted = 0
        for acc_id in meta["account_ids"]:
            try:
                await self.dispatcher.submit_task(account_id=acc_id, task_type="UPLOAD_MEDIA", extra_config=extra)
                submitted += 1
            except Exception as e:
                logger.warning(f"[ScheduledUpload] submit lỗi {acc_id}: {e}")
        meta["status"] = "DONE"
        meta["submitted"] = submitted
        logger.info(f"[ScheduledUpload] Lịch {job_id} đã chạy: đẩy {submitted} task.")

    def add(
        self,
        account_ids: List[str],
        video_path: Optional[str],
        caption: str,
        run_at: datetime,
        image_path: Optional[str] = None,
    ) -> str:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "id": job_id, "account_ids": list(account_ids), "image_path": image_path,
            "video_path": video_path,
            "caption": caption, "run_at": run_at.isoformat(timespec="minutes"),
            "status": "PENDING", "submitted": 0,
        }
        self.scheduler.add_job(self._fire, DateTrigger(run_date=run_at), args=[job_id], id=job_id)
        logger.info(f"[ScheduledUpload] Tạo lịch {job_id} lúc {run_at} cho {len(account_ids)} account.")
        return job_id

    def list(self) -> List[Dict[str, Any]]:
        return sorted(self._jobs.values(), key=lambda m: m["run_at"])

    def remove(self, job_id: str) -> bool:
        if job_id not in self._jobs:
            return False
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass
        self._jobs[job_id]["status"] = "CANCELLED"
        return True
