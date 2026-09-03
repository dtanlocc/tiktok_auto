import asyncio
import base64
import json
import urllib.request

import websockets


ACCOUNT_ID = "curiocamarzenet@hotmail.com"
OUTPUT_PATH = r"D:\tiktok_auto\.tmp\thuf37ch_sha0ge_stuck.jpg"


async def main() -> None:
    request = urllib.request.Request(
        "http://127.0.0.1:9000/api/v1/tasks/screen-view-ping",
        method="POST",
        data=b"",
    )
    urllib.request.urlopen(request, timeout=5).read()
    async with websockets.connect("ws://127.0.0.1:9000/ws/screens") as socket:
        deadline = asyncio.get_running_loop().time() + 15
        while asyncio.get_running_loop().time() < deadline:
            payload = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
            if payload.get("event") != "BROWSER_FRAME":
                continue
            data = payload.get("data") or {}
            if data.get("account_id") != ACCOUNT_ID:
                continue
            with open(OUTPUT_PATH, "wb") as output:
                output.write(base64.b64decode(data["jpeg_b64"]))
            print(OUTPUT_PATH)
            return
    raise RuntimeError("No matching browser frame received")


asyncio.run(main())
