"""Publish photos or a fallback video to TikTok for one account."""

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.core.exceptions import StudioReauthenticationRequired
from app.core.tiktok_cookies import (
    TIKTOK_AUTH_COOKIE_NAMES as _TIKTOK_AUTH_COOKIE_NAMES,
    has_tiktok_auth_cookies as _has_tiktok_auth_cookies,
)
from app.use_cases.upload.media_selection import select_preferred_media


logger = logging.getLogger("UploadMediaUseCase")

#: Everything after this marker in ``note`` belongs to the uploader and is
#: rewritten on every batch. Everything before it was typed by a person -
#: five accounts carry hand-written notes like "video đăng tay" - so the
#: uploader appends, never replaces the whole field.
_AUTO_NOTE_MARK = "[auto]"

#: How much of a filename survives into the note. Long enough to recognise the
#: clip, short enough that two failed slots still fit next to a human note.
_NOTE_NAME_CHARS = 26


def _short_media_name(name: str) -> str:
    """The head of a filename, without its extension."""
    stem = Path(name).stem.strip()
    if len(stem) <= _NOTE_NAME_CHARS:
        return stem
    return stem[:_NOTE_NAME_CHARS].rstrip() + "…"


def _failed_slot_summary(slots: list[dict[str, Any]]) -> str:
    """``2/2 "01. Cabai dicampur andal…"`` - the slot first, then the clip.

    The slot is what distinguishes a partial batch from a total loss, so it
    leads. Without it a reader sees a filename and cannot tell whether the
    account published anything at all.
    """
    parts = []
    for slot in slots:
        label = f"{slot.get('index')}/{slot.get('total')}"
        name = _short_media_name(str(slot.get("name") or ""))
        parts.append(f'{label} "{name}"' if name else label)
    return ", ".join(parts)


def _merge_upload_note(existing: Any, auto_text: str) -> str:
    """Put ``auto_text`` in the uploader's half of ``note``, keep the rest.

    An empty ``auto_text`` clears only the uploader's half, so an account that
    publishes everything on the next run stops advertising a stale failure
    while the operator's own note stays where they left it.
    """
    manual = str(existing or "")
    marker_at = manual.find(_AUTO_NOTE_MARK)
    if marker_at != -1:
        manual = manual[:marker_at]
    manual = manual.strip().rstrip("|").strip()
    if not auto_text:
        return manual
    auto = f"{_AUTO_NOTE_MARK} {auto_text}"
    return f"{manual} | {auto}"[:500] if manual else auto[:500]


def _normalize_public_caption(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _matches_recent_public_post(
    videos: list[dict[str, Any]],
    caption: str,
    not_before_epoch: int,
) -> bool:
    """Match a newly-created public post without accepting an older duplicate."""
    expected = _normalize_public_caption(caption)
    if not expected:
        return False
    # Studio and the public profile may truncate a long title or slightly edit
    # an auto-selected hashtag. The unchanged caption prefix is the stable part.
    needle = expected[:48]
    for video in videos:
        try:
            created_at = int(video.get("create_time") or 0)
        except (TypeError, ValueError):
            continue
        if created_at < int(not_before_epoch) - 30:
            continue
        if needle in _normalize_public_caption(video.get("title")):
            return True
    return False


class TikTokUploadMediaUseCase:
    def __init__(
        self,
        account_repo,
        browser_service,
        login_strategy,
        email_service,
        step_logger=None,
        public_video_client_factory=None,
        credential_login_strategy_factory=None,
    ):
        self.account_repo = account_repo
        self.browser_service = browser_service
        self.login_strategy = login_strategy
        self.email_service = email_service
        self.step_logger = step_logger
        self.public_video_client_factory = public_video_client_factory
        self.credential_login_strategy_factory = credential_login_strategy_factory

    async def _log(self, message: str) -> None:
        if self.step_logger:
            await self.step_logger(message)

    async def _persist_authenticated_cookie_snapshot(self, account_id: str, account):
        """Replace stored cookies only with a snapshot that still has auth."""
        try:
            fresh = await self.browser_service.extract_cookies()
        except Exception as exc:
            logger.warning("[Upload] Khong doc duoc cookie moi %s: %s", account_id, exc)
            return account

        if not _has_tiktok_auth_cookies(fresh):
            logger.warning(
                "[Upload] Bo qua snapshot cookie thieu auth cua %s; giu cookie cu.",
                account_id,
            )
            return account

        latest = self.account_repo.get_by_id(account_id) or account
        latest.cookies = fresh
        latest.health_status = "ALIVE"
        self.account_repo.save(latest)
        return latest

    async def _persist_cookies_after_login_if_needed(self, account_id: str, account):
        # Reusing a valid cookie must not rewrite the DB with Studio's temporary
        # cookie jar. Credential/OTP login is the path that creates new auth.
        if getattr(self.login_strategy, "last_login_method", None) != "CREDENTIAL":
            return account
        return await self._persist_authenticated_cookie_snapshot(account_id, account)

    async def _checkpoint_cookies_before_browser_close(self, account_id: str, account):
        """Persist the live, verified session after a successful publish.

        TikTok can refresh the auth cookie while Studio is open. Keeping only
        the pre-upload cookie loses that refreshed session when the temporary
        browser profile is deleted. Return to For You first so a Studio-only or
        logged-out cookie jar can never replace the last known usable jar.
        """
        try:
            await self._log(
                "Bài đăng đã xong; đang xác nhận lại phiên For You và lưu cookie trước khi đóng browser..."
            )
            home_ready = await self.browser_service.prepare_foryou_home(
                step_logger=None
            )
            if not home_ready:
                logger.warning(
                    "[Upload] Khong checkpoint cookie %s: For You khong xac nhan dang nhap; giu cookie cu.",
                    account_id,
                )
                return account

            identity_validator = getattr(
                self.browser_service, "validate_authenticated_identity", None
            )
            if identity_validator is not None and not await identity_validator(
                account.username
            ):
                logger.warning(
                    "[Upload] Khong checkpoint cookie %s: identity sau publish khong khop @%s.",
                    account_id,
                    account.username,
                )
                await self._log(
                    "⚠️ Không lưu đè cookie sau đăng vì chưa xác minh đúng username."
                )
                return account

            previous = account.cookies if account else []
            previous_auth = {
                (cookie.get("name"), cookie.get("domain"), cookie.get("value"))
                for cookie in previous or []
                if isinstance(cookie, dict)
                and cookie.get("name") in _TIKTOK_AUTH_COOKIE_NAMES
                and cookie.get("value")
            }
            fresh = await self.browser_service.extract_cookies()
            if not _has_tiktok_auth_cookies(fresh):
                logger.warning(
                    "[Upload] Snapshot sau publish cua %s thieu sessionid; giu cookie cu.",
                    account_id,
                )
                await self._log(
                    "⚠️ Phiên For You vẫn mở nhưng snapshot thiếu sessionid; đã giữ nguyên cookie cũ."
                )
                return account

            updated = self.account_repo.get_by_id(account_id) or account
            updated.cookies = fresh
            updated.health_status = "ALIVE"
            self.account_repo.save(updated)
            updated_auth = {
                (cookie.get("name"), cookie.get("domain"), cookie.get("value"))
                for cookie in (updated.cookies if updated else []) or []
                if isinstance(cookie, dict)
                and cookie.get("name") in _TIKTOK_AUTH_COOKIE_NAMES
                and cookie.get("value")
            }
            logger.info(
                "[Upload] Da checkpoint cookie sau publish cho %s (auth_refreshed=%s).",
                account_id,
                previous_auth != updated_auth,
            )
            await self._log(
                "✅ Đã xác nhận For You còn đăng nhập và lưu cookie phiên mới trước khi đóng browser."
            )
            return updated
        except Exception as exc:
            # Cookie checkpoint is defensive persistence after the post was
            # already verified. Never turn a successful post into a failure.
            logger.warning(
                "[Upload] Khong checkpoint duoc cookie sau publish %s: %s; giu cookie cu.",
                account_id,
                exc,
            )
            return account

    async def _verify_recent_public_post(
        self,
        account,
        caption: str,
        not_before_epoch: int,
    ) -> bool:
        """Recover from a Studio false negative using the exact public profile.

        This fallback runs only after TikTok acknowledged the publish action.
        It also requires a new creation timestamp, so an older post with the
        same caption cannot turn a failed upload into a false success.
        """
        username = str(getattr(account, "username", "") or "").lstrip("@").strip()
        sec_uid = str(getattr(account, "tiktok_sec_uid", "") or "").strip()
        if not username or not sec_uid:
            return False

        factory = self.public_video_client_factory
        if factory is None:
            from app.use_cases.analytics.tiktok_public_video_client import (
                TikTokPublicVideoClient,
            )

            factory = TikTokPublicVideoClient

        client = factory()
        await self._log(
            "Studio chưa hiển thị bài mới; đang đối chiếu hồ sơ TikTok công khai..."
        )
        try:
            for attempt in range(3):
                if attempt:
                    await asyncio.sleep(8 if attempt == 1 else 15)
                try:
                    videos, _complete = await client.fetch_videos(
                        username=username,
                        sec_uid=sec_uid,
                        max_videos=10,
                    )
                except Exception as exc:
                    logger.warning(
                        "[UploadBatch] Public verification attempt %d failed for %s: %s",
                        attempt + 1,
                        username,
                        exc,
                    )
                    continue
                if _matches_recent_public_post(videos, caption, not_before_epoch):
                    await self._log(
                        "Đã xác minh bài mới trên hồ sơ TikTok công khai; "
                        "bỏ qua kết quả âm tính giả từ Studio Posts."
                    )
                    return True
            return False
        finally:
            try:
                await client.close()
            except Exception:
                pass

    async def _reauthenticate_for_studio(self, account_id: str, account):
        """Force one credential/OTP login when Studio rejects a web cookie."""
        await self._log(
            "TikTok Studio yêu cầu đăng nhập lại; "
            "đang xóa phiên cookie cũ và chuyển sang đăng nhập OTP..."
        )
        await self.browser_service.clear_auth_session()

        factory = self.credential_login_strategy_factory
        if factory is None:
            from app.use_cases.auth.login_strategies import (
                CredentialEmailOtpLoginStrategy,
            )

            factory = CredentialEmailOtpLoginStrategy

        logged_in = await factory().login(
            self.browser_service,
            account,
            step_logger=self.step_logger,
            email_service=self.email_service,
        )
        if not logged_in:
            raise RuntimeError(
                "TikTok Studio yêu cầu đăng nhập lại nhưng login OTP thất bại."
            )

        home_ready = await self.browser_service.prepare_foryou_home(
            step_logger=self.step_logger
        )
        if not home_ready:
            raise RuntimeError(
                "Đăng nhập OTP xong nhưng trang For You chưa sẵn sàng để thử lại Studio."
            )

        account = await self._persist_authenticated_cookie_snapshot(account_id, account)
        await self._log(
            "Đăng nhập OTP lại thành công; đang thử lại TikTok Studio một lần..."
        )
        return account

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

            account = await self._persist_cookies_after_login_if_needed(
                account_id, account
            )
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
        failed_slots: list[dict[str, Any]] = []
        failure_codes: list[str] = []
        for index, (path, caption) in enumerate(
            zip(resolved_paths, resolved_captions), start=1
        ):
            await self._log(
                f"[{index}/{total}] Chuẩn bị đăng {Path(path).name} "
                "trong cùng phiên trình duyệt..."
            )
            error = ""
            failure_code = ""
            publish_started_at = int(time.time())
            try:
                for publish_attempt in range(2):
                    try:
                        ok = await self.browser_service.publish_media(
                            image_paths=None,
                            video_path=path,
                            caption=caption,
                            schedule_at=None,
                            step_logger=self.step_logger,
                            continue_session=index > 1 and publish_attempt == 0,
                        )
                        break
                    except StudioReauthenticationRequired:
                        if publish_attempt > 0:
                            raise
                        account = await self._reauthenticate_for_studio(
                            account_id,
                            account,
                        )
                if not ok:
                    failure_code = str(getattr(
                        self.browser_service, "last_publish_failure_code", ""
                    ) or "")
                    failure_detail = str(getattr(
                        self.browser_service, "last_publish_failure_detail", ""
                    ) or "")
                    if failure_code == "VIDEO_DUPLICATE":
                        error = (
                            "VIDEO_TRUNG: TikTok báo video đã tồn tại; "
                            "Post now không xuất hiện."
                        )
                    elif failure_code == "VIDEO_SWALLOWED":
                        error = (
                            "VIDEO_BI_NUOT: Đã bấm Post now nhưng video không "
                            "xuất hiện trong Studio Posts."
                        )
                    else:
                        error = "TikTok không xác nhận bài đăng trong Studio Posts."
                    if failure_detail:
                        error = f"{error} {failure_detail}"
            except Exception as exc:
                ok = False
                error = str(exc)
                failure_code = str(getattr(
                    self.browser_service, "last_publish_failure_code", ""
                ) or "")
                logger.exception(
                    "[UploadBatch] Video %d/%d failed for %s", index, total, account_id
                )

            if not ok and failure_code not in {
                "VIDEO_DUPLICATE", "VIDEO_SWALLOWED"
            } and bool(
                getattr(self.browser_service, "last_publish_acknowledged", False)
            ):
                latest_account = self.account_repo.get_by_id(account_id) or account
                if await self._verify_recent_public_post(
                    latest_account,
                    caption,
                    publish_started_at,
                ):
                    ok = True
                    error = ""
                    failure_code = ""

            self._record_upload_result(account_id, ok, error)
            if result_sink is not None:
                result_sink.append({
                    "video_path": path,
                    "success": bool(ok),
                    "error": error,
                    "code": failure_code,
                })
            if ok:
                successes += 1
                await self._log(
                    f"[{index}/{total}] Đã xác minh video xuất hiện trong Studio Posts."
                )
            else:
                failure_reason = error or "không xác nhận được"
                failures.append(f"{Path(path).name}: {failure_reason}")
                # Which SLOT failed, not just which file. "2/2" is what tells a
                # reader that one video of the pair got through; the filename
                # alone cannot say that.
                failed_slots.append({
                    "index": index,
                    "total": total,
                    "name": Path(path).name,
                    "code": failure_code,
                })
                if failure_code:
                    failure_codes.append(failure_code)
                if failure_code == "VIDEO_DUPLICATE":
                    await self._log(
                        f"[{index}/{total}] ⚠️ VIDEO_TRUNG · {Path(path).name}: "
                        "đang yêu cầu hàng đợi tìm video khác."
                    )
                elif failure_code == "VIDEO_SWALLOWED":
                    await self._log(
                        f"[{index}/{total}] ❌ VIDEO_BI_NUOT · {Path(path).name}: "
                        "không xuất hiện trong Studio Posts."
                    )
                else:
                    await self._log(
                        f"[{index}/{total}] ❌ {Path(path).name}: {failure_reason}"
                    )
                if index < total:
                    await self._log(
                        f"[{index}/{total}] Video lỗi; tiếp tục video kế tiếp trong cùng phiên..."
                    )

        if successes:
            latest_account = self.account_repo.get_by_id(account_id) or account
            account = await self._checkpoint_cookies_before_browser_close(
                account_id, latest_account
            )

        account = self.account_repo.get_by_id(account_id)
        if account:
            if failures and successes:
                # PARTIAL. One video of the batch reached Studio Posts, so the
                # account is not broken and must not be dragged to ERROR with
                # the accounts that published nothing: the operator retries
                # those two cases differently. The failure is still reported -
                # in the step line, in last_upload_error, and in the note -
                # it just no longer decides the account's state.
                account.status = "SUCCESS"
                account.last_upload_status = "SUCCESS"
                account.last_upload_error = "; ".join(failures)[:500]
                account.current_step = (
                    f"⚠️ Đăng được {successes}/{total} · lỗi "
                    f"{_failed_slot_summary(failed_slots)}"
                )
                account.note = _merge_upload_note(
                    getattr(account, "note", ""),
                    f"lỗi {_failed_slot_summary(failed_slots)}",
                )
            elif failures:
                account.status = "ERROR"
                account.last_upload_status = "FAILED"
                account.last_upload_error = "; ".join(failures)[:500]
                account.note = _merge_upload_note(
                    getattr(account, "note", ""),
                    f"hỏng toàn bộ {total}/{total} video",
                )
                if "VIDEO_SWALLOWED" in failure_codes:
                    account.current_step = (
                        f"❌ VIDEO_BI_NUOT · Đã đăng {successes}/{total}; "
                        "video không xuất hiện trong Studio Posts"
                    )
                elif "VIDEO_DUPLICATE" in failure_codes:
                    account.current_step = (
                        f"⚠️ VIDEO_TRUNG · Đã đăng {successes}/{total}; "
                        "TikTok không hiện Post now"
                    )
                else:
                    account.current_step = (
                        f"⚠ Đã đăng {successes}/{total} video trong cùng phiên; "
                        f"lỗi {len(failures)} video"
                    )
            else:
                # Do not leave a previous task's ERROR/QUEUED state attached to
                # a batch which Studio Posts has just verified successfully.
                # The dispatcher reloads this field to choose the final UI
                # state, and the use case is also invoked directly by tools.
                account.status = "SUCCESS"
                account.last_upload_status = "SUCCESS"
                account.last_upload_error = ""
                account.current_step = f"✅ Đã đăng {successes}/{total} video trong cùng phiên"
                account.note = _merge_upload_note(getattr(account, "note", ""), "")
            self.account_repo.save(account)
        # One published video is a published account. Returning False for a
        # partial batch sent the dispatcher down its failure branch, which
        # overwrites the step line and reports the whole account as failed.
        return successes > 0

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

        account = await self._persist_cookies_after_login_if_needed(account_id, account)

        for publish_attempt in range(2):
            try:
                ok = await self.browser_service.publish_media(
                    image_paths=list(media.image_paths) or None,
                    video_path=media.video_path,
                    caption=caption,
                    schedule_at=schedule_at,
                    step_logger=self.step_logger,
                )
                break
            except StudioReauthenticationRequired:
                if publish_attempt > 0:
                    raise
                account = await self._reauthenticate_for_studio(
                    account_id,
                    account,
                )
        if ok:
            account = await self._checkpoint_cookies_before_browser_close(
                account_id, account
            )
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
        else:
            failure_code = str(getattr(
                self.browser_service, "last_publish_failure_code", ""
            ) or "")
            if failure_code == "VIDEO_DUPLICATE":
                account.current_step = "⚠️ VIDEO_TRUNG · TikTok không hiện Post now"
            elif failure_code == "VIDEO_SWALLOWED":
                account.current_step = (
                    "❌ VIDEO_BI_NUOT · Không xuất hiện trong Studio Posts"
                )
            self.account_repo.save(account)
        return ok


# Backward-compatible import for existing callers/plugins.
TikTokUploadVideoUseCase = TikTokUploadMediaUseCase
