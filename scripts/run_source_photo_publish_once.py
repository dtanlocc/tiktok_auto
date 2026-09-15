"""Run one guarded real photo post through the source backend classes."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlmodel import Session

from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter
from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository
from app.infrastructure.email.email_service_factory import create_email_service
from app.use_cases.auth.login_strategies import CookieThenCredentialLoginStrategy
from app.use_cases.orchestration.task_dispatcher import _uuid_to_seed
from app.use_cases.upload.tiktok_upload_video import TikTokUploadMediaUseCase


def _find_account(repo: SQLiteAccountRepository, username: str):
    wanted = username.strip().lstrip("@").casefold()
    return next(
        (
            account
            for account in repo.get_all()
            if str(account.username or "").strip().lstrip("@").casefold() == wanted
        ),
        None,
    )


async def _run(username: str, image_path: Path, caption: str) -> int:
    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    browser = InvisiblePlaywrightAdapter()

    async def log_step(message: str) -> None:
        print(f"SOURCE_PHOTO_LOG {message}", flush=True)

    with Session(engine) as session:
        repo = SQLiteAccountRepository(session)
        account = _find_account(repo, username)
        if account is None:
            raise RuntimeError(f"Account username not found: {username}")

        account_id = account.id
        print(
            "SOURCE_PHOTO_TARGET "
            + json.dumps(
                {
                    "account_id": account_id,
                    "username": account.username,
                    "image_path": str(image_path),
                    "caption": caption,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        email_service = create_email_service()
        login_strategy = CookieThenCredentialLoginStrategy()
        use_case = TikTokUploadMediaUseCase(
            account_repo=repo,
            browser_service=browser,
            login_strategy=login_strategy,
            email_service=email_service,
            step_logger=log_step,
        )

        try:
            await browser.initialize(seed=_uuid_to_seed(account_id))
            await log_step("Kiểm tra đăng nhập trước khi đối chiếu bài ảnh trùng...")
            logged_in = await login_strategy.login(
                browser,
                account,
                step_logger=log_step,
                email_service=email_service,
            )
            if not logged_in:
                raise RuntimeError("Không đăng nhập được để kiểm tra bài ảnh đã tồn tại.")

            ready = await browser.prepare_foryou_home(step_logger=log_step)
            if not ready:
                raise RuntimeError("Trang For You chưa sẵn sàng cho kiểm tra trước đăng.")

            already_present = await browser._verify_post_in_studio(
                caption,
                media_name=image_path.name,
                step_logger=log_step,
                timeout_seconds=20,
            )
            print(
                "SOURCE_PHOTO_PRECHECK "
                + json.dumps({"already_present": already_present}),
                flush=True,
            )
            if already_present:
                print(
                    "SOURCE_PHOTO_RESULT "
                    + json.dumps(
                        {
                            "posted": False,
                            "skipped_duplicate": True,
                            "backend_success": True,
                        }
                    ),
                    flush=True,
                )
                return 0

            success = await use_case.execute(
                account_id,
                image_path=str(image_path),
                caption=caption,
            )
            print(
                "SOURCE_PHOTO_RESULT "
                + json.dumps(
                    {
                        "posted": True,
                        "skipped_duplicate": False,
                        "backend_success": bool(success),
                        "last_publish_acknowledged": bool(
                            browser.last_publish_acknowledged
                        ),
                        "distribution_status": (
                            browser.last_publish_distribution_status
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0 if success else 2
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--caption", required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(
        _run(args.username, args.image.resolve(), args.caption.strip())
    )


if __name__ == "__main__":
    raise SystemExit(main())
