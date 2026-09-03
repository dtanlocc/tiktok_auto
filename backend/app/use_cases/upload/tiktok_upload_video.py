"""Publish photos or a fallback video to TikTok for one account."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.use_cases.upload.media_selection import select_preferred_media


logger = logging.getLogger("UploadMediaUseCase")


class TikTokUploadMediaUseCase:
    def __init__(self, account_repo, browser_service, login_strategy, email_service, step_logger=None):
        self.account_repo = account_repo
        self.browser_service = browser_service
        self.login_strategy = login_strategy
        self.email_service = email_service
        self.step_logger = step_logger

    async def _log(self, message: str) -> None:
        if self.step_logger:
            await self.step_logger(message)

    async def execute(
        self,
        account_id: str,
        image_path: Optional[str] = None,
        video_path: Optional[str] = None,
        caption: str = "",
        schedule_at: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        """Chạy upload và ghi lịch sử thật, kể cả khi Playwright phát sinh lỗi."""
        self._media_validated = False
        try:
            ok = await self._execute_impl(
                account_id,
                image_path=image_path,
                video_path=video_path,
                caption=caption,
                schedule_at=schedule_at,
                **kwargs,
            )
        except Exception as exc:
            if self._media_validated:
                self._record_upload_result(account_id, False, str(exc))
            raise
        if self._media_validated:
            self._record_upload_result(
                account_id,
                ok,
                "" if ok else "TikTok không xác nhận bài đăng.",
            )
        return ok

    def _record_upload_result(self, account_id: str, success: bool, error: str) -> None:
        account = self.account_repo.get_by_id(account_id)
        if account is None:
            return
        now = datetime.now().isoformat(timespec="seconds")
        if success:
            account.upload_success_count = int(getattr(account, "upload_success_count", 0) or 0) + 1
            account.last_upload_status = "SUCCESS"
            account.last_upload_error = ""
        else:
            account.upload_failure_count = int(getattr(account, "upload_failure_count", 0) or 0) + 1
            account.last_upload_status = "FAILED"
            account.last_upload_error = (error or "Upload thất bại")[:500]
        account.last_upload_at = now
        self.account_repo.save(account)

    async def execute_video_batch(
        self,
        account_id: str,
        video_paths: list[str],
        captions: Optional[list[str]] = None,
        result_sink: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        """Publish multiple distinct videos through one authenticated browser.

        Only the first item consumes the mandatory For You readiness ticket.
        Later items return from Studio Posts to Studio Upload in the same page
        context, keeping cookies, browser processes and the dispatcher slot.
        """
        if not video_paths:
            raise ValueError("Không có video để đăng.")

        resolved_paths: list[str] = []
        seen: set[str] = set()
        for raw_path in video_paths:
            try:
                media = select_preferred_media(image_path=None, video_path=raw_path)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            path = str(media.video_path or "")
            key = path.casefold()
            if key in seen:
                raise ValueError("Danh sách của một account không được chứa video trùng nhau.")
            seen.add(key)
            resolved_paths.append(path)

        resolved_captions = []
        for index, path in enumerate(resolved_paths):
            supplied = captions[index].strip() if captions and index < len(captions) else ""
            resolved_captions.append(supplied or Path(path).stem)

        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise Exception("Không tìm thấy tài khoản.")

        await self._log(
            f"Đã nhận {len(resolved_paths)} video không trùng. "
            "Đang đăng nhập một lần tại trang For You..."
        )
        try:
            logged_in = await self.login_strategy.login(
                self.browser_service,
                account,
                step_logger=self.step_logger,
                email_service=self.email_service,
            )
            if not logged_in:
                raise Exception("Đăng nhập thất bại — không thể đăng danh sách video.")

            home_ready = await self.browser_service.prepare_foryou_home(
                step_logger=self.step_logger
            )
            if not home_ready:
                raise Exception(
                    "Trang For You chưa tải xong hoặc chưa xác nhận được phiên đăng nhập."
                )

            try:
                fresh = await self.browser_service.extract_cookies()
                if fresh:
                    account = self.account_repo.get_by_id(account_id)
                    account.cookies = fresh
                    account.health_status = "ALIVE"
                    self.account_repo.save(account)
            except Exception as exc:
                logger.warning("[UploadBatch] Không lưu được cookie %s: %s", account_id, exc)
        except Exception as exc:
            for path in resolved_paths:
                self._record_upload_result(account_id, False, str(exc))
                if result_sink is not None:
                    result_sink.append({
                        "video_path": path,
                        "success": False,
                        "error": str(exc),
                    })
            raise

        total = len(resolved_paths)
        successes = 0
        failures: list[str] = []
        for index, (path, caption) in enumerate(
            zip(resolved_paths, resolved_captions), start=1
        ):
            await self._log(
                f"[{index}/{total}] Chuẩn bị đăng {Path(path).name} "
                "trong cùng phiên trình duyệt..."
            )
            error = ""
            try:
                ok = await self.browser_service.publish_media(
                    image_paths=None,
                    video_path=path,
                    caption=caption,
                    schedule_at=None,
                    step_logger=self.step_logger,
                    continue_session=index > 1,
                )
                if not ok:
                    error = "TikTok không xác nhận bài đăng trong Studio Posts."
            except Exception as exc:
                ok = False
                error = str(exc)
                logger.exception(
                    "[UploadBatch] Video %d/%d failed for %s", index, total, account_id
                )

            self._record_upload_result(account_id, ok, error)
            if result_sink is not None:
                result_sink.append({
                    "video_path": path,
                    "success": bool(ok),
                    "error": error,
                })
            if ok:
                successes += 1
                await self._log(
                    f"[{index}/{total}] Đã xác minh video xuất hiện trong Studio Posts."
                )
            else:
                failures.append(f"{Path(path).name}: {error or 'không xác nhận được'}")
                if index < total:
                    await self._log(
                        f"[{index}/{total}] Video lỗi; tiếp tục video kế tiếp trong cùng phiên..."
                    )

        account = self.account_repo.get_by_id(account_id)
        if account:
            if failures:
                account.last_upload_status = "FAILED"
                account.last_upload_error = "; ".join(failures)[:500]
                account.current_step = (
                    f"⚠ Đã đăng {successes}/{total} video trong cùng phiên; "
                    f"lỗi {len(failures)} video"
                )
            else:
                account.last_upload_status = "SUCCESS"
                account.last_upload_error = ""
                account.current_step = f"✅ Đã đăng {successes}/{total} video trong cùng phiên"
            self.account_repo.save(account)
        return not failures

    async def _execute_impl(
        self,
        account_id: str,
        image_path: Optional[str] = None,
        video_path: Optional[str] = None,
        caption: str = "",
        schedule_at: Optional[str] = None,
        **_ignore: Any,
    ) -> bool:
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise Exception("Không tìm thấy tài khoản.")

        try:
            media = select_preferred_media(image_path=image_path, video_path=video_path)
        except ValueError as exc:
            raise Exception(str(exc)) from exc
        self._media_validated = True

        # Blank caption defaults to the supplied video filename even when a
        # valid photo wins media selection. If there is no video, use the first
        # selected photo name instead.
        if not caption.strip():
            caption_source = video_path or (media.image_paths[0] if media.image_paths else media.video_path)
            caption = Path(caption_source).stem if caption_source else ""

        media_label = f"{len(media.image_paths)} ảnh" if media.kind == "photo" else "video dự phòng"
        await self._log(f"Đã chọn {media_label}. Đang đăng nhập tại trang For You...")
        logged_in = await self.login_strategy.login(
            self.browser_service,
            account,
            step_logger=self.step_logger,
            email_service=self.email_service,
        )
        if not logged_in:
            raise Exception("Đăng nhập thất bại — không thể đăng nội dung.")

        # Studio is never opened before the signed-in For You home is stable.
        home_ready = await self.browser_service.prepare_foryou_home(step_logger=self.step_logger)
        if not home_ready:
            raise Exception("Trang For You chưa tải xong hoặc chưa xác nhận được phiên đăng nhập.")

        try:
            fresh = await self.browser_service.extract_cookies()
            if fresh:
                account = self.account_repo.get_by_id(account_id)
                account.cookies = fresh
                account.health_status = "ALIVE"
                self.account_repo.save(account)
        except Exception as exc:
            logger.warning("[Upload] Không lưu được cookie %s: %s", account_id, exc)

        ok = await self.browser_service.publish_media(
            image_paths=list(media.image_paths) or None,
            video_path=media.video_path,
            caption=caption,
            schedule_at=schedule_at,
            step_logger=self.step_logger,
        )
        if ok:
            account = self.account_repo.get_by_id(account_id)
            account.status = "SUCCESS"
            distribution = getattr(
                self.browser_service,
                "last_publish_distribution_status",
                "UNKNOWN",
            )
            if distribution == "FYF_INELIGIBLE":
                account.current_step = "⚠️ Đã đăng · TikTok báo không đủ điều kiện For You"
            elif distribution == "UNDER_REVIEW":
                account.current_step = "⏳ Đã đăng · TikTok đang xét duyệt"
            elif schedule_at:
                account.current_step = "✅ Đã lên lịch đăng"
            elif media.kind == "photo":
                account.current_step = f"✅ Đã đăng {len(media.image_paths)} ảnh"
            else:
                account.current_step = "✅ Đã đăng video"
            self.account_repo.save(account)
        return ok


# Backward-compatible import for existing callers/plugins.
TikTokUploadVideoUseCase = TikTokUploadMediaUseCase
