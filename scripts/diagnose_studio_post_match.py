from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.infrastructure.automation.playwright_adapter import (  # noqa: E402
    InvisiblePlaywrightAdapter,
)


def _load_cookies(database: Path, username: str) -> list[dict]:
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        row = connection.execute(
            "SELECT cookies_json FROM accounts WHERE username = ?", (username,)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError(f"Account not found: {username}")
    value = json.loads(row[0])
    if not isinstance(value, list):
        raise RuntimeError("Account cookies are invalid.")
    return value


async def run(database: Path, username: str, title: str) -> None:
    browser = InvisiblePlaywrightAdapter()

    async def step(message: str) -> None:
        print(message, flush=True)

    try:
        await browser.initialize(seed=1_905_732, force_visible=False)
        await browser.inject_cookies(_load_cookies(database, username))
        if not await browser.prepare_foryou_home(step_logger=step):
            raise RuntimeError("For You readiness check failed.")
        await browser.navigate_to(
            "https://www.tiktok.com/tiktokstudio/content?lang=en"
        )
        verified = await browser._verify_post_in_studio(
            title,
            media_name=title,
            step_logger=step,
            timeout_seconds=25,
        )
        print(f"STUDIO_POST_MATCH={verified}", flush=True)
        if not verified:
            raise RuntimeError("Studio Posts did not show the requested title.")
        print("Read-only Studio Posts verification passed; no publish action was taken.")
    finally:
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(run(args.database, args.username, args.title))


if __name__ == "__main__":
    main()
