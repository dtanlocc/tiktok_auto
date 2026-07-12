# File: backend/app/infrastructure/scheduler/interaction_scheduler.py
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher

logger = logging.getLogger("InteractionScheduler")


class InteractionScheduler:
    """
    Quản lý các "chiến dịch" tương tác video lặp lại theo chu kỳ: chạy
    duration_minutes phút rồi tự dừng (do chính use case tự canh thời gian),
    chờ tới lần lặp kế tiếp sau interval_minutes phút, cứ thế lặp lại.

    Dùng APScheduler (AsyncIOScheduler) chạy chung event loop với FastAPI -
    không cần thread/process riêng.
    """

    def __init__(self, dispatcher: ConcurrentTaskDispatcher):
        self.dispatcher = dispatcher
        self.scheduler = AsyncIOScheduler()
        # schedule_id -> config (giữ để trả cho UI hiển thị danh sách lịch)
        self.schedules: Dict[str, Dict[str, Any]] = {}

    def start(self) -> None:
        self.scheduler.start()
        logger.info("[+] InteractionScheduler đã khởi động.")

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        logger.info("[-] InteractionScheduler đã tắt.")

    async def _fire_campaign(self, schedule_id: str) -> None:
        """Được APScheduler gọi mỗi khi tới chu kỳ - đẩy tác vụ INTERACT_VIDEOS
        vào Task Dispatcher chung cho từng account trong chiến dịch."""
        config = self.schedules.get(schedule_id)
        if not config or not config.get("is_active", True):
            return

        account_ids: List[str] = config["account_ids"]
        extra_config = {
            "mode": config.get("mode", "foryou"),
            "hashtag": config.get("hashtag"),
            "duration_minutes": config.get("duration_minutes", 10),
            "like_probability": config.get("like_probability", 0.4),
            "comment_probability": config.get("comment_probability", 0.05),
            "comment_list": config.get("comment_list", []),
            "min_watch_seconds": config.get("min_watch_seconds", 3.0),
            "max_watch_seconds": config.get("max_watch_seconds", 15.0),
        }

        submitted = 0
        skipped = 0
        for acc_id in account_ids:
            # AN TOAN CHONG CHONG CHEO: neu account nay van dang chay dot truoc
            # (chu ky qua ngan hon thoi gian chay thuc te) thi bo qua dot nay,
            # tranh mo 2 phien trinh duyet cho cung 1 account cung luc.
            if acc_id in self.dispatcher.active_tasks:
                logger.info(f"[!] [Schedule {schedule_id}] Bỏ qua account {acc_id} vì vòng trước vẫn đang chạy.")
                skipped += 1
                continue
            await self.dispatcher.submit_task(
                account_id=acc_id,
                task_type="INTERACT_VIDEOS",
                extra_config=extra_config,
            )
            submitted += 1

        logger.info(
            f"[*] [Schedule {schedule_id}] Đã kích hoạt 1 chu kỳ: "
            f"{submitted} account được xếp hàng, {skipped} account bị bỏ qua (đang bận)."
        )

    def create_schedule(
        self,
        account_ids: List[str],
        mode: str,
        hashtag: Optional[str],
        duration_minutes: int,
        interval_minutes: int,
        like_probability: float,
        comment_probability: float,
        comment_list: List[str],
        min_watch_seconds: float = 3.0,
        max_watch_seconds: float = 15.0,
    ) -> str:
        if duration_minutes > interval_minutes:
            logger.warning(
                f"[!] duration_minutes ({duration_minutes}) lớn hơn interval_minutes "
                f"({interval_minutes}) - các chu kỳ có thể chồng lấn nhau (đã có cơ chế "
                f"tự bỏ qua account đang bận, nhưng nên chỉnh interval lớn hơn duration)."
            )

        schedule_id = str(uuid.uuid4())
        self.schedules[schedule_id] = {
            "account_ids": account_ids,
            "mode": mode,
            "hashtag": hashtag,
            "duration_minutes": duration_minutes,
            "interval_minutes": interval_minutes,
            "like_probability": like_probability,
            "comment_probability": comment_probability,
            "comment_list": comment_list,
            "min_watch_seconds": min_watch_seconds,
            "max_watch_seconds": max_watch_seconds,
            "is_active": True,
            "created_at": datetime.now().isoformat(),
        }

        self.scheduler.add_job(
            self._fire_campaign,
            trigger=IntervalTrigger(minutes=interval_minutes),
            args=[schedule_id],
            id=schedule_id,
            next_run_time=datetime.now(),  # Kích hoạt ngay lần đầu, không đợi hết interval đầu tiên
            replace_existing=True,
        )

        logger.info(f"[+] Đã tạo lịch tương tác video mới: {schedule_id} (mỗi {interval_minutes} phút)")
        return schedule_id

    def pause_schedule(self, schedule_id: str) -> bool:
        if schedule_id not in self.schedules:
            return False
        self.schedules[schedule_id]["is_active"] = False
        self.scheduler.pause_job(schedule_id)
        logger.info(f"[*] Đã tạm dừng lịch {schedule_id}.")
        return True

    def resume_schedule(self, schedule_id: str) -> bool:
        if schedule_id not in self.schedules:
            return False
        self.schedules[schedule_id]["is_active"] = True
        self.scheduler.resume_job(schedule_id)
        logger.info(f"[*] Đã tiếp tục lịch {schedule_id}.")
        return True

    def delete_schedule(self, schedule_id: str) -> bool:
        if schedule_id not in self.schedules:
            return False
        try:
            self.scheduler.remove_job(schedule_id)
        except Exception:
            pass
        del self.schedules[schedule_id]
        logger.info(f"[-] Đã xoá lịch {schedule_id}.")
        return True

    def list_schedules(self) -> List[Dict[str, Any]]:
        result = []
        for sid, cfg in self.schedules.items():
            item = dict(cfg)
            item["schedule_id"] = sid
            result.append(item)
        return result
