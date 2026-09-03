import asyncio
import json
import sqlite3
import time

import websockets


ACCOUNT_ID = "curiocamarzenet@hotmail.com"
DATABASE = r"D:\tiktok_auto\backend\database.db"


def read_state() -> dict:
    connection = sqlite3.connect(DATABASE, timeout=2)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """select username,status,current_step,last_upload_status,
                      last_upload_error,upload_success_count,upload_failure_count
               from accounts where email=?""",
            (ACCOUNT_ID,),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        connection.close()


async def main() -> None:
    deadline = time.monotonic() + 50
    previous = None
    async with websockets.connect("ws://127.0.0.1:9000/ws") as socket:
        while time.monotonic() < deadline:
            state = read_state()
            signature = (
                state.get("status"),
                state.get("current_step"),
                state.get("last_upload_status"),
                state.get("last_upload_error"),
                state.get("upload_success_count"),
                state.get("upload_failure_count"),
            )
            if signature != previous:
                print(json.dumps(state, ensure_ascii=False), flush=True)
                previous = signature
            if state.get("status") not in {"RUNNING", "QUEUED"}:
                return
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=2)
                payload = json.loads(raw)
                data = payload.get("data") or {}
                if data.get("account_id") == ACCOUNT_ID and data.get("message"):
                    print(json.dumps({
                        "event": payload.get("event"),
                        "message": data["message"],
                    }, ensure_ascii=False), flush=True)
            except asyncio.TimeoutError:
                await socket.send("ping")


asyncio.run(main())
