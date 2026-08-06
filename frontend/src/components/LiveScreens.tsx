// File: frontend/src/components/LiveScreens.tsx
import React, { useEffect, useRef, useState } from 'react';
import { MonitorPlay, Maximize2, X } from 'lucide-react';

interface FrameData {
  username: string;
  jpeg_b64: string;
  updatedAt: number;
}

// Mo WebSocket RIENG cho luong hinh anh (tan suat cao) de KHONG lam re-render
// toan bo App moi khi co frame moi. Chi component nay cap nhat khi co BROWSER_FRAME.
const WS_URL = 'ws://127.0.0.1:9000/ws';

export const LiveScreens: React.FC = () => {
  const [frames, setFrames] = useState<Record<string, FrameData>>({});
  const [zoomId, setZoomId] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // HEARTBEAT: báo backend "đang xem" khi tab này mở -> backend mới chụp & gửi
  // frame. Đóng tab (unmount) -> ngừng ping -> backend tự ngừng chụp (tiết kiệm
  // CPU cho browser đang chạy đa luồng).
  useEffect(() => {
    const ping = () => {
      fetch('http://127.0.0.1:9000/api/v1/tasks/screen-view-ping', { method: 'POST' }).catch(() => {});
    };
    ping(); // ping ngay khi mở tab
    const t = setInterval(ping, 3000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let closed = false;

    const connect = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.event === 'BROWSER_FRAME') {
            const { account_id, username, jpeg_b64 } = msg.data;
            setFrames((prev) => ({
              ...prev,
              [account_id]: { username, jpeg_b64, updatedAt: Date.now() },
            }));
          } else if (msg.event === 'BROWSER_FRAME_END') {
            const { account_id } = msg.data;
            setFrames((prev) => {
              const next = { ...prev };
              delete next[account_id];
              return next;
            });
            setZoomId((z) => (z === account_id ? null : z));
          }
        } catch {
          /* bo qua goi tin khong phai JSON */
        }
      };

      // Tu ket noi lai neu bi rot (backend restart...) - tru khi component da unmount.
      ws.onclose = () => {
        if (!closed) setTimeout(connect, 2000);
      };
    };

    connect();

    return () => {
      closed = true;
      wsRef.current?.close();
    };
  }, []);

  const ids = Object.keys(frames);
  const zoomed = zoomId ? frames[zoomId] : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="bg-[#0e1424] p-4 rounded-2xl border border-slate-800 flex items-center justify-between">
        <h3 className="font-bold text-sm text-slate-200 flex items-center gap-2">
          <MonitorPlay className="w-4 h-4 text-teal-400" /> Màn Hình Trực Tiếp Đa Luồng
        </h3>
        <span className="text-[11px] text-slate-400 font-semibold">
          Đang chạy: <span className="text-teal-400 font-bold">{ids.length}</span> trình duyệt
        </span>
      </div>

      {ids.length === 0 ? (
        <div className="bg-[#0e1424] border border-slate-800 rounded-2xl py-16 text-center">
          <MonitorPlay className="w-10 h-10 text-slate-700 mx-auto mb-3" />
          <p className="text-sm text-slate-500 font-semibold">Chưa có trình duyệt nào đang chạy.</p>
          <p className="text-xs text-slate-600 mt-1">
            Khi bạn chạy Đăng nhập / Đổi Profile / Tương tác video, màn hình từng luồng sẽ hiện ở đây.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {ids.map((id) => {
            const f = frames[id];
            return (
              <div
                key={id}
                className="bg-[#0e1424] border border-slate-800 rounded-xl overflow-hidden group relative cursor-zoom-in hover:border-teal-500/50 transition-colors"
                onClick={() => setZoomId(id)}
              >
                <div className="flex items-center justify-between px-3 py-2 border-b border-slate-800 bg-[#141b2e]">
                  <span className="text-[11px] font-bold text-slate-200 truncate">{f.username}</span>
                  <span className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-teal-400 animate-pulse" />
                    <Maximize2 className="w-3 h-3 text-slate-500 group-hover:text-teal-400" />
                  </span>
                </div>
                <img
                  src={`data:image/jpeg;base64,${f.jpeg_b64}`}
                  alt={f.username}
                  className="w-full block bg-black"
                  draggable={false}
                />
              </div>
            );
          })}
        </div>
      )}

      {/* PHONG TO 1 MAN HINH */}
      {zoomed && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-6"
          onClick={() => setZoomId(null)}
        >
          <div className="relative max-w-5xl w-full" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-bold text-slate-100">{zoomed.username}</span>
              <button
                onClick={() => setZoomId(null)}
                className="p-1.5 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-300"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <img
              src={`data:image/jpeg;base64,${zoomed.jpeg_b64}`}
              alt={zoomed.username}
              className="w-full rounded-xl border border-slate-700 bg-black"
              draggable={false}
            />
          </div>
        </div>
      )}
    </div>
  );
};
