# File: backend/app/use_cases/interaction/tiktok_video_interaction.py
import asyncio
import logging
import random
import time
from typing import Callable, Awaitable, Optional, Any, List

from app.domain.ports.repository import IAccountRepository
from app.domain.ports.browser import IBrowserService
from app.domain.ports.email import IEmailService
from app.use_cases.auth.login_strategies import ITikTokLoginStrategy
from app.core.exceptions import AccountBannedException

logger = logging.getLogger("TikTokVideoInteractionUseCase")


class TikTokVideoInteractionUseCase:
    """
    Nghiep vu tuong tac video giong nguoi that: luot Feed For You hoac tim theo
    hashtag, xem video voi thoi gian ngau nhien, tym & binh luan theo xac suat
    cau hinh, chay lien tuc cho toi khi het thoi gian duration_minutes.
    """

    # Cac selector du phong cho tung hanh dong - TikTok hay doi DOM nen luon
    # can nhieu phuong an, giong pattern da dung o cac use case khac trong repo.
    LIKE_SELECTORS = '[data-e2e="like-icon"], [data-e2e="browse-like-icon"]'
    COMMENT_ICON_SELECTORS = '[data-e2e="comment-icon"], [data-e2e="browse-comment-icon"]'
    COMMENT_INPUT_SELECTORS = (
        'div[contenteditable="true"][data-e2e="comment-input"], '
        'div[contenteditable="true"].public-DraftEditor-content, '
        '[data-e2e="comment-text"]'
    )
    COMMENT_POST_SELECTORS = '[data-e2e="comment-post"]:visible, button:has-text("Post"):visible, button:has-text("Đăng"):visible'
    HASHTAG_FIRST_VIDEO_SELECTORS = '[data-e2e="challenge-item"] a, div[data-e2e="challenge-item"]'

    def __init__(
        self,
        account_repo: IAccountRepository,
        browser_service: IBrowserService,
        login_strategy: ITikTokLoginStrategy,
        email_service: IEmailService,
        step_logger: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.account_repo = account_repo
        self.browser_service = browser_service
        self.login_strategy = login_strategy
        self.email_service = email_service
        self.step_logger = step_logger

    async def execute(
        self,
        account_id: str,
        mode: str = "foryou",               # "foryou" hoac "hashtag"
        hashtag: Optional[str] = None,
        duration_minutes: int = 10,
        like_probability: float = 0.4,      # Xac suat tym 1 video (0.0 - 1.0)
        comment_probability: float = 0.05,  # Xac suat binh luan 1 video (0.0 - 1.0)
        comment_list: Optional[List[str]] = None,
        min_watch_seconds: float = 3.0,
        max_watch_seconds: float = 15.0,
    ) -> bool:
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise ValueError("Tài khoản không tồn tại trên hệ thống.")

        try:
            if self.step_logger:
                await self.step_logger("Đang khởi động môi trường trình duyệt tàng hình...")

            # Dang nhap truoc (thuong bang Cookie da luu san) - tuong tac video
            # (tym/binh luan) bat buoc can dang nhap, luot xem thi khong nhung
            # van dang nhap truoc de dam bao dung 1 phien nhat quan.
            login_success = await self.login_strategy.login(
                self.browser_service,
                account,
                step_logger=self.step_logger,
                email_service=self.email_service,
            )
            if not login_success:
                if self.step_logger:
                    await self.step_logger("[-] Đăng nhập thất bại, không thể tương tác video.")
                return False

            page = self.browser_service._page

            # 1. Điều hướng tới nguồn video
            if mode == "hashtag" and hashtag:
                clean_tag = hashtag.strip().lstrip("#")
                target_url = f"https://www.tiktok.com/tag/{clean_tag}"
                if self.step_logger:
                    await self.step_logger(f"Đang vào hashtag #{clean_tag}...")
            else:
                target_url = "https://www.tiktok.com/foryou"
                if self.step_logger:
                    await self.step_logger("Đang vào trang For You...")

            await self.browser_service.navigate_to(target_url)
            await asyncio.sleep(4)

            # Trang hashtag cần bấm vào video đầu tiên để vào chế độ xem toàn màn hình
            if mode == "hashtag" and hashtag:
                try:
                    first_video = page.locator(self.HASHTAG_FIRST_VIDEO_SELECTORS).first
                    await first_video.wait_for(state="visible", timeout=10000)
                    await first_video.click()
                    await asyncio.sleep(3)
                except Exception:
                    if self.step_logger:
                        await self.step_logger("[!] Không tìm thấy video nào trong hashtag này, chuyển về For You.")
                    await self.browser_service.navigate_to("https://www.tiktok.com/foryou")
                    await asyncio.sleep(4)

            comment_list = comment_list or []
            watched_count = 0
            liked_count = 0
            commented_count = 0
            deadline = time.monotonic() + duration_minutes * 60

            while time.monotonic() < deadline:
                watch_seconds = random.uniform(min_watch_seconds, max_watch_seconds)
                if self.step_logger:
                    await self.step_logger(f"Đang xem video #{watched_count + 1} ({watch_seconds:.1f}s)...")
                await asyncio.sleep(watch_seconds)
                watched_count += 1

                # TYM video theo xác suất cấu hình
                if random.random() < like_probability:
                    try:
                        like_btn = page.locator(self.LIKE_SELECTORS).first
                        if await like_btn.count() > 0:
                            await like_btn.click()
                            liked_count += 1
                            if self.step_logger:
                                await self.step_logger(f"❤️ Đã tym video #{watched_count}.")
                            await asyncio.sleep(random.uniform(0.5, 1.5))
                    except Exception as e_like:
                        logger.debug(f"[!] Không thể tym video: {str(e_like)}")

                # BÌNH LUẬN video theo xác suất cấu hình (chỉ khi có sẵn danh sách câu mẫu)
                if comment_list and random.random() < comment_probability:
                    try:
                        comment_text = random.choice(comment_list).strip()
                        if comment_text:
                            comment_icon = page.locator(self.COMMENT_ICON_SELECTORS).first
                            if await comment_icon.count() > 0:
                                await comment_icon.click()
                                await asyncio.sleep(1.5)

                                comment_input = page.locator(self.COMMENT_INPUT_SELECTORS).first
                                await comment_input.wait_for(state="visible", timeout=8000)
                                await comment_input.click()
                                await comment_input.press_sequentially(comment_text, delay=random.randint(80, 180))
                                await asyncio.sleep(random.uniform(0.5, 1.2))

                                post_btn = page.locator(self.COMMENT_POST_SELECTORS).first
                                if await post_btn.count() > 0:
                                    await post_btn.click()
                                    commented_count += 1
                                    if self.step_logger:
                                        await self.step_logger(f'💬 Đã bình luận video #{watched_count}: "{comment_text}"')
                                await asyncio.sleep(random.uniform(1.0, 2.0))
                    except Exception as e_cmt:
                        logger.debug(f"[!] Không thể bình luận video: {str(e_cmt)}")

                if time.monotonic() >= deadline:
                    break

                # LƯỚT sang video kế tiếp - mô phỏng cuộn chuột tự nhiên thay vì
                # nhảy tức thời, cùng tinh thần với cách humanize=True xử lý
                # chuyển động chuột ở phần khởi tạo trình duyệt.
                try:
                    scroll_amount = random.randint(700, 950)
                    await page.mouse.wheel(0, scroll_amount)
                    await asyncio.sleep(random.uniform(0.8, 1.8))
                except Exception as e_scroll:
                    logger.warning(f"[!] Lỗi khi lướt video kế tiếp: {str(e_scroll)}")
                    break

            if self.step_logger:
                await self.step_logger(
                    f"✅ Hoàn tất phiên tương tác: xem {watched_count} video, "
                    f"tym {liked_count}, bình luận {commented_count}."
                )
            logger.info(f"[+] {account.username}: xem={watched_count} tym={liked_count} cmt={commented_count}")
            return True

        except AccountBannedException as e_ban:
            account.status = "ERROR"
            account.health_status = "BANNED"
            account.cookies = []
            account.current_step = "Tài khoản bị Banned (phát hiện khi tương tác video)"
            self.account_repo.save(account)
            raise e_ban

        except Exception as e:
            logger.error(f"[-] Lỗi tương tác video cho {account_id}: {str(e)}")
            if self.step_logger:
                await self.step_logger(f"Lỗi tương tác video: {str(e)}")
            return False
