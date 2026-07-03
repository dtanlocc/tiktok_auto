import logging
from fastapi import WebSocket
from typing import List

logger = logging.getLogger("WebSocketManager")

class WebSocketConnectionManager:
    def __init__(self):
        # Danh sách lưu trữ các kết nối WebSocket đang hoạt động
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"[+] Kết nối WebSocket mới thiết lập. Tổng kết nối: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"[-] Một kết nối WebSocket đã đóng. Tổng kết nối: {len(self.active_connections)}")

    async def broadcast(self, message: dict) -> None:
        """Phát tin nhắn (trạng thái, logs) tới toàn bộ các Client đang mở Dashboard"""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                # Tự động dọn dẹp các kết nối bị đứt ngầm (stale connections)
                logger.warning(f"Lỗi gửi WebSocket, đang dọn dẹp kết nối lỗi: {str(e)}")
                self.disconnect(connection)

# Singleton Instance dùng chung toàn hệ thống
ws_manager = WebSocketConnectionManager()