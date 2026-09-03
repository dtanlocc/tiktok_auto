import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, Eye, Maximize2, MonitorPlay, Pause, Radio, X } from 'lucide-react';
import { Account } from '../types';

interface LiveScreensProps { accounts: Account[]; }
interface FrameData { username: string; jpeg_b64: string; updatedAt: number; }
interface LiveFrameCardProps {
  accountId: string;
  frame: FrameData;
  account?: Account;
  onZoom: (accountId: string) => void;
}
interface SmoothFrameImageProps {
  jpegBase64: string;
  alt: string;
  className: string;
}

const WS_URL = 'ws://127.0.0.1:9000/ws/screens';
const API_URL = 'http://127.0.0.1:9000/api/v1/tasks';

function identity(account: Account | undefined, fallbackUsername: string): string {
  return account?.email || fallbackUsername || 'Tài khoản chưa xác định';
}

const SmoothFrameImage = memo<SmoothFrameImageProps>(({ jpegBase64, alt, className }) => {
  const nextSource = `data:image/jpeg;base64,${jpegBase64}`;
  const [displayedSource, setDisplayedSource] = useState(nextSource);

  useEffect(() => {
    if (nextSource === displayedSource) return;
    let cancelled = false;
    let committed = false;
    let animationFrame: number | null = null;
    const preloaded = new Image();
    preloaded.decoding = 'async';

    const commitDecodedFrame = () => {
      if (cancelled || committed) return;
      committed = true;
      animationFrame = window.requestAnimationFrame(() => {
        if (!cancelled) setDisplayedSource(nextSource);
      });
    };

    // Keep the old decoded frame visible until the next JPEG is fully ready.
    // This avoids the black/blank flash caused by replacing <img src> directly.
    preloaded.src = nextSource;
    preloaded.decode().then(commitDecodedFrame).catch(() => {
      if (preloaded.complete && preloaded.naturalWidth > 0) commitDecodedFrame();
      else preloaded.onload = commitDecodedFrame;
    });

    return () => {
      cancelled = true;
      preloaded.onload = null;
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
    };
  }, [displayedSource, nextSource]);

  return <img src={displayedSource} alt={alt} className={className} draggable={false} decoding="async" />;
});
SmoothFrameImage.displayName = 'SmoothFrameImage';

const LiveFrameCard = memo<LiveFrameCardProps>(({ accountId, frame, account, onZoom }) => (
  <article className="group overflow-hidden rounded-2xl border border-line-soft bg-surface transition-colors hover:border-brand/45">
    <div className="flex items-start justify-between gap-3 border-b border-line-soft bg-surface-2 px-3 py-2.5">
      <div className="min-w-0">
        <p className="truncate text-xs font-bold text-fg">{identity(account, frame.username)}</p>
        <p className="mt-0.5 truncate text-[10px] text-fg-muted">@{account?.username || frame.username}</p>
      </div>
      <span className="badge shrink-0 border border-emerald-500/25 bg-emerald-500/10 text-emerald-300 normal-case tracking-normal">
        <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse-soft" /> Trực tiếp
      </span>
    </div>
    <button type="button" onClick={() => onZoom(accountId)} className="relative block aspect-video w-full cursor-zoom-in overflow-hidden bg-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-inset" aria-label={`Phóng to luồng trực tiếp của ${identity(account, frame.username)}`}>
      <SmoothFrameImage jpegBase64={frame.jpeg_b64} alt={`Luồng trực tiếp của ${identity(account, frame.username)}`} className="h-full w-full object-contain" />
      <span className="absolute bottom-2 right-2 grid size-10 place-items-center rounded-lg bg-black/75 text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <Maximize2 className="h-4 w-4" aria-hidden="true" />
      </span>
    </button>
    <div className="border-t border-line-soft px-3 py-2.5">
      <div className="flex items-start gap-2">
        <Activity className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand" aria-hidden="true" />
        <p className="line-clamp-2 text-xs leading-5 text-fg-muted">{account?.current_step || 'Đang nhận hình ảnh từ phiên trình duyệt'}</p>
      </div>
    </div>
  </article>
));
LiveFrameCard.displayName = 'LiveFrameCard';

export const LiveScreens: React.FC<LiveScreensProps> = ({ accounts }) => {
  const [frames, setFrames] = useState<Record<string, FrameData>>({});
  const [zoomId, setZoomId] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const reconnectTimerRef = useRef<number | null>(null);
  const frameFlushRef = useRef<number | null>(null);
  const pendingFramesRef = useRef<Map<string, FrameData | null>>(new Map());

  useEffect(() => {
    const ping = () => { fetch(`${API_URL}/screen-view-ping`, { method: 'POST' }).catch(() => {}); };
    ping();
    const timer = window.setInterval(ping, 3000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    const scheduleFrameFlush = () => {
      if (frameFlushRef.current !== null) return;
      frameFlushRef.current = window.requestAnimationFrame(() => {
        frameFlushRef.current = null;
        const updates = pendingFramesRef.current;
        pendingFramesRef.current = new Map();
        if (updates.size === 0) return;
        setFrames((current) => {
          const next = { ...current };
          updates.forEach((frame, accountId) => { if (frame === null) delete next[accountId]; else next[accountId] = frame; });
          return next;
        });
      });
    };
    const connect = () => {
      socket = new WebSocket(WS_URL);
      socket.onopen = () => setWsConnected(true);
      socket.onmessage = (event: MessageEvent) => {
        try {
          const message = JSON.parse(event.data);
          if (message.event === 'BROWSER_FRAME') {
            const { account_id, username, jpeg_b64 } = message.data;
            pendingFramesRef.current.set(account_id, { username, jpeg_b64, updatedAt: Date.now() });
            scheduleFrameFlush();
          } else if (message.event === 'BROWSER_FRAME_END') {
            const { account_id } = message.data;
            pendingFramesRef.current.set(account_id, null);
            scheduleFrameFlush();
            setZoomId((current) => (current === account_id ? null : current));
          }
        } catch { /* A later valid frame restores the stream. */ }
      };
      socket.onclose = () => { setWsConnected(false); if (!disposed) reconnectTimerRef.current = window.setTimeout(connect, 2000); };
    };
    connect();
    return () => {
      disposed = true;
      socket?.close();
      if (reconnectTimerRef.current !== null) window.clearTimeout(reconnectTimerRef.current);
      if (frameFlushRef.current !== null) window.cancelAnimationFrame(frameFlushRef.current);
    };
  }, []);

  useEffect(() => {
    if (!zoomId) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setZoomId(null); };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [zoomId]);

  const accountMap = useMemo(() => new Map(accounts.map((account) => [account.id, account])), [accounts]);
  const ids = useMemo(() => Object.keys(frames), [frames]);
  const activeAccounts = useMemo(() => accounts
    .filter((account) => account.status === 'RUNNING' || account.status === 'QUEUED' || account.is_paused)
    .sort((left, right) => Number(right.status === 'RUNNING') - Number(left.status === 'RUNNING')), [accounts]);
  const runningCount = activeAccounts.filter((account) => account.status === 'RUNNING' && !account.is_paused).length;
  const queuedCount = activeAccounts.filter((account) => account.status === 'QUEUED').length;
  const zoomed = zoomId ? frames[zoomId] : null;
  const zoomedAccount = zoomId ? accountMap.get(zoomId) : undefined;

  return (
    <section className="flex flex-col gap-4" aria-labelledby="live-screens-title">
      <header className="card flex flex-col gap-3 p-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="mb-1 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.16em] text-brand"><Radio className="h-4 w-4" aria-hidden="true" /> Giám sát tác vụ</div>
          <h2 id="live-screens-title" className="text-xl font-bold tracking-tight text-fg">Màn hình trực tiếp & tiến trình</h2>
          <p className="mt-1 text-sm text-fg-muted">Luồng hình ảnh chỉ để xem; tác vụ nền vẫn tiếp tục khi bạn chuyển tab hoặc phóng to.</p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center sm:min-w-[360px]">
          <div className="rounded-xl border border-line-soft bg-surface-2 px-3 py-2"><p className="text-lg font-bold tabular-nums text-fg">{ids.length}</p><p className="text-[10px] uppercase tracking-wide text-fg-subtle">Màn hình</p></div>
          <div className="rounded-xl border border-line-soft bg-surface-2 px-3 py-2"><p className="text-lg font-bold tabular-nums text-emerald-300">{runningCount}</p><p className="text-[10px] uppercase tracking-wide text-fg-subtle">Đang chạy</p></div>
          <div className="rounded-xl border border-line-soft bg-surface-2 px-3 py-2"><p className="text-lg font-bold tabular-nums text-sky-300">{queuedCount}</p><p className="text-[10px] uppercase tracking-wide text-fg-subtle">Đang chờ</p></div>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="flex items-center gap-2 text-sm font-bold text-fg"><MonitorPlay className="h-4 w-4 text-brand" aria-hidden="true" /> Luồng hình ảnh</h3>
            <span className="text-xs font-semibold text-fg-muted" role="status" aria-live="polite"><span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${wsConnected ? 'bg-emerald-400' : 'bg-amber-400'}`} />{wsConnected ? 'Đã kết nối' : 'Đang kết nối lại'}</span>
          </div>
          {ids.length === 0 ? (
            <div className="card grid min-h-72 place-items-center px-6 text-center"><div><Eye className="mx-auto h-10 w-10 text-fg-subtle" aria-hidden="true" /><p className="mt-3 text-sm font-semibold text-fg">Chưa có phiên đang phát hình</p><p className="mt-1 text-xs leading-5 text-fg-muted">Khi trình duyệt nền bắt đầu tác vụ, ảnh trực tiếp sẽ xuất hiện tại đây.</p></div></div>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-2">
              {ids.map((id) => <LiveFrameCard key={id} accountId={id} frame={frames[id]} account={accountMap.get(id)} onZoom={setZoomId} />)}
            </div>
          )}
        </div>

        <aside className="card min-h-0 overflow-hidden" aria-labelledby="task-progress-title">
          <div className="border-b border-line-soft p-4"><h3 id="task-progress-title" className="flex items-center gap-2 text-sm font-bold text-fg"><Activity className="h-4 w-4 text-brand" aria-hidden="true" /> Tiến trình tác vụ</h3><p className="mt-1 text-xs text-fg-muted">Chỉ hiển thị tài khoản đang chạy, tạm dừng hoặc chờ.</p></div>
          {activeAccounts.length === 0 ? (
            <div className="px-5 py-12 text-center"><p className="text-sm font-semibold text-fg">Không có tác vụ đang hoạt động</p><p className="mt-1 text-xs text-fg-muted">Tiến trình mới sẽ tự xuất hiện ở đây.</p></div>
          ) : (
            <ol className="max-h-[680px] space-y-1 overflow-y-auto overscroll-contain p-2">
              {activeAccounts.map((account) => (
                <li key={account.id} className="rounded-xl border border-transparent px-3 py-3 hover:border-line-soft hover:bg-white/[0.02]">
                  <div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate text-xs font-bold text-fg">{account.email || 'Chưa có Hotmail'}</p><p className="mt-0.5 truncate text-[10px] text-fg-subtle">@{account.username}</p></div><span className={`badge shrink-0 border normal-case tracking-normal ${account.is_paused ? 'border-amber-500/25 bg-amber-500/10 text-amber-300' : account.status === 'RUNNING' ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-300' : 'border-sky-500/25 bg-sky-500/10 text-sky-300'}`}>{account.is_paused ? <><Pause className="h-3 w-3" aria-hidden="true" /> Tạm dừng</> : account.status === 'RUNNING' ? 'Đang chạy' : 'Đang chờ'}</span></div>
                  <div className="mt-2 flex items-start gap-2 rounded-lg bg-surface-2 px-2.5 py-2"><span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${account.is_paused ? 'bg-amber-400' : account.status === 'RUNNING' ? 'bg-emerald-400 animate-pulse-soft' : 'bg-sky-400'}`} aria-hidden="true" /><p className="text-xs leading-5 text-fg-muted">{account.current_step || 'Đang chuẩn bị tác vụ'}</p></div>
                </li>
              ))}
            </ol>
          )}
        </aside>
      </div>

      {zoomed && zoomId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-3 sm:p-6" role="dialog" aria-modal="true" aria-label={`Xem luồng trực tiếp của ${identity(zoomedAccount, zoomed.username)}`} onClick={() => setZoomId(null)}>
          <div className="w-full max-w-7xl" onClick={(event) => event.stopPropagation()}>
            <div className="mb-2 flex items-center justify-between gap-3"><div className="min-w-0"><p className="truncate text-sm font-bold text-fg">{identity(zoomedAccount, zoomed.username)}</p><p className="mt-0.5 truncate text-xs text-fg-muted">{zoomedAccount?.current_step || 'Chế độ chỉ xem · tác vụ vẫn tiếp tục chạy'}</p></div><button type="button" onClick={() => setZoomId(null)} className="grid size-11 shrink-0 place-items-center rounded-lg bg-slate-800 text-fg hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand" aria-label="Đóng màn hình phóng to"><X className="h-4 w-4" aria-hidden="true" /></button></div>
            <div className="flex max-h-[82vh] min-h-[320px] items-center justify-center overflow-hidden rounded-xl border border-line bg-black"><SmoothFrameImage jpegBase64={zoomed.jpeg_b64} alt={`Luồng trực tiếp phóng to của ${identity(zoomedAccount, zoomed.username)}`} className="max-h-[82vh] max-w-full object-contain select-none" /></div>
          </div>
        </div>
      )}
    </section>
  );
};
