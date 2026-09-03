import React, { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, BarChart3, Eye, Heart, LoaderCircle, Search, UserPlus, Users, Video, X } from 'lucide-react';
import { Account, TikTokVideoMetric } from '../types';

interface Props {
  account: Account | null;
  onClose: () => void;
  onSync: (accountIds: string[]) => Promise<void>;
}

interface AnalyticsResponse {
  sync_status: string;
  sync_source: string;
  sync_error: string;
  metrics_updated_at: string;
  collected_video_count: number;
  profile_video_count: number | null;
  profile: { follower_count: number | null; following_count: number | null; likes_count: number | null };
  totals: { views: number | null; likes: number | null; comments: number | null; shares: number | null };
  videos: TikTokVideoMetric[];
}

const numbers = new Intl.NumberFormat('vi-VN');
const metric = (value: number | null | undefined) => value === null || value === undefined ? '—' : numbers.format(value);

export const AccountAnalyticsModal: React.FC<Props> = ({ account, onClose, onSync }) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<AnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);

  const load = useCallback(async () => {
    if (!account) return;
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`http://127.0.0.1:9000/api/v1/accounts/${encodeURIComponent(account.id)}/analytics`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || 'Không tải được chi tiết hiệu suất.');
      setData(body);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Không tải được chi tiết hiệu suất.');
    } finally {
      setLoading(false);
    }
  }, [account]);

  useEffect(() => {
    if (!account) return;
    setQuery('');
    void load();
    window.setTimeout(() => dialogRef.current?.focus(), 0);
  }, [account, load]);

  useEffect(() => {
    if (!account || data?.sync_status !== 'SYNCING') return;
    const timer = window.setInterval(() => { void load(); }, 4000);
    return () => window.clearInterval(timer);
  }, [account, data?.sync_status, load]);

  useEffect(() => {
    if (!account) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [account, onClose]);

  const videos = useMemo(() => {
    const needle = deferredQuery.trim().toLowerCase();
    if (!needle) return data?.videos || [];
    return (data?.videos || []).filter((video) => `${video.title} ${video.video_id}`.toLowerCase().includes(needle));
  }, [data?.videos, deferredQuery]);

  if (!account) return null;
  const totals = data?.totals;
  const engagement = totals?.views
    ? (((totals.likes || 0) + (totals.comments || 0) + (totals.shares || 0)) / totals.views * 100)
    : null;
  const cards = [
    { label: 'Video công khai', value: metric(data?.profile_video_count), icon: Video },
    { label: 'Follower', value: metric(data?.profile?.follower_count), icon: Users },
    { label: 'Following', value: metric(data?.profile?.following_count), icon: UserPlus },
    { label: 'Lượt thích profile', value: metric(data?.profile?.likes_count), icon: Heart },
    { label: 'Tổng view chi tiết', value: metric(totals?.views), icon: Eye },
    { label: 'Engagement chi tiết', value: engagement === null ? '—' : `${engagement.toFixed(2)}%`, icon: BarChart3 },
  ];

  const syncNow = async () => {
    setSyncing(true);
    setError('');
    try {
      await onSync([account.id]);
      setData((current) => current ? { ...current, sync_status: 'SYNCING', sync_error: '' } : current);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Không thể bắt đầu đồng bộ.');
    } finally {
      setSyncing(false);
    }
  };

  return <div className="fixed inset-0 z-[110] grid place-items-center bg-slate-950/80 p-3 backdrop-blur-sm" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="analytics-title" tabIndex={-1} className="card flex max-h-[92dvh] w-full max-w-6xl flex-col overflow-hidden shadow-2xl">
      <header className="flex items-start justify-between gap-4 border-b border-line-soft p-4 sm:p-5">
          <div className="min-w-0"><p className="text-xs font-bold uppercase tracking-wider text-brand">Hiệu suất từng account</p><h2 id="analytics-title" className="mt-1 truncate text-lg font-bold text-fg">{account.email}</h2><p className="mt-1 text-sm text-fg-muted">@{account.username || '—'} {account.display_name ? `· ${account.display_name}` : ''} · {account.batch_tag}</p></div>
        <div className="flex shrink-0 gap-2"><button type="button" onClick={syncNow} disabled={syncing || account.is_sold} className="btn btn-primary min-h-11">{syncing ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <BarChart3 className="h-4 w-4" />} {account.is_sold ? 'Đã bán · khóa đồng bộ' : syncing ? 'Đang đồng bộ...' : 'Đồng bộ nhanh'}</button><button type="button" onClick={onClose} aria-label="Đóng chi tiết hiệu suất" className="grid h-11 w-11 place-items-center rounded-xl text-fg-muted hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"><X className="h-5 w-5" /></button></div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
        {error && <div role="alert" className="mb-4 flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div>}
        {loading ? <div className="grid min-h-64 place-items-center text-fg-muted"><LoaderCircle className="h-7 w-7 animate-spin" aria-label="Đang tải hiệu suất" /></div> : <>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-fg-muted">Trạng thái: <strong className={data?.sync_status === 'SUCCESS' ? 'text-emerald-300' : data?.sync_status === 'FAILED' ? 'text-rose-300' : 'text-amber-300'}>{data?.sync_status || account.analytics_sync_status}</strong>{data?.sync_source === 'TIKTOK_PUBLIC_PROFILE' ? ' · profile công khai' : data?.sync_source === 'TIKTOK_PUBLIC_WEB' ? ' · profile + video công khai' : data?.sync_source === 'TIKTOK_STUDIO_BROWSER' ? ' · TikTok Studio' : ''}{data?.metrics_updated_at ? ` · ${new Date(data.metrics_updated_at).toLocaleString('vi-VN')}` : ''}</p>{data?.sync_error && <p className="max-w-xl text-sm text-amber-200">{data.sync_error}</p>}</div>
          <section className="mb-4 rounded-xl border border-line-soft bg-surface-2 p-3" aria-label="Thông tin profile công khai"><div className="flex items-start gap-3"><img src={account.avatar_url || '/app-mark.svg'} alt="" className="h-12 w-12 rounded-full border border-line object-cover" /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><strong className="text-fg">{account.display_name || account.username || 'Profile TikTok'}</strong>{account.verified && <span className="badge border-sky-400/30 bg-sky-400/10 text-sky-200">Đã xác minh</span>}{account.private_account && <span className="badge border-amber-400/30 bg-amber-400/10 text-amber-200">Riêng tư</span>}</div><p className="mt-1 whitespace-pre-wrap break-words text-xs text-fg-muted">{account.bio || 'Chưa có bio công khai.'}</p>{account.website_url && <a href={account.website_url} target="_blank" rel="noreferrer" className="mt-1 block truncate text-xs text-brand hover:underline">{account.website_url}</a>}</div></div></section>
          <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6" aria-label="Tổng hợp hiệu suất">{cards.map(({ label, value, icon: Icon }) => <article key={label} className="rounded-xl border border-line-soft bg-surface-2 p-3"><Icon className="h-4 w-4 text-brand" aria-hidden="true" /><p className="mt-3 text-[11px] font-semibold text-fg-muted">{label}</p><p className="mt-1 text-lg font-bold tabular-nums text-fg">{value}</p></article>)}</section>

          <section className="mt-5 overflow-hidden rounded-xl border border-line-soft">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line-soft p-3"><div><h3 className="font-bold text-fg">Chi tiết video</h3><p className="mt-0.5 text-xs text-fg-muted">{videos.length} video đã lưu · đồng bộ nhanh chỉ cập nhật tổng quan profile; dữ liệu chi tiết cũ được giữ nguyên</p></div><label className="relative"><span className="sr-only">Tìm video</span><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" /><input value={query} onChange={(event) => setQuery(event.target.value)} className="field min-h-11 w-64 max-w-full pl-9" placeholder="Tìm caption hoặc video ID..." /></label></div>
            {!videos.length ? <div className="p-10 text-center text-sm text-fg-muted">Chưa có dữ liệu chi tiết video. Tổng quan profile phía trên vẫn được đồng bộ nhanh.</div> : <div className="max-h-[46dvh] overflow-auto"><table className="w-full min-w-[860px] text-left text-xs"><thead className="sticky top-0 bg-surface-2 text-[11px] uppercase text-fg-subtle"><tr><th className="px-3 py-2">Video</th><th className="px-3 py-2 text-right">View</th><th className="px-3 py-2 text-right">Like</th><th className="px-3 py-2 text-right">Comment</th><th className="px-3 py-2 text-right">Share</th><th className="px-3 py-2">Đăng lúc</th></tr></thead><tbody className="divide-y divide-line-soft">{videos.map((video) => <tr key={video.video_id} style={{ contentVisibility: 'auto', containIntrinsicSize: '52px' }}><td className="max-w-md px-3 py-2"><p className="truncate font-semibold text-fg" title={video.title}>{video.title || 'Không có caption'}</p><p className="mt-0.5 font-mono text-[10px] text-fg-subtle">{video.video_id}</p></td><td className="px-3 py-2 text-right font-semibold tabular-nums text-fg">{metric(video.view_count)}</td><td className="px-3 py-2 text-right tabular-nums text-fg-muted">{metric(video.like_count)}</td><td className="px-3 py-2 text-right tabular-nums text-fg-muted">{metric(video.comment_count)}</td><td className="px-3 py-2 text-right tabular-nums text-fg-muted">{metric(video.share_count)}</td><td className="px-3 py-2 text-fg-muted">{video.create_time ? new Date(video.create_time * 1000).toLocaleDateString('vi-VN') : '—'}</td></tr>)}</tbody></table></div>}
          </section>
        </>}
      </div>
    </div>
  </div>;
};
