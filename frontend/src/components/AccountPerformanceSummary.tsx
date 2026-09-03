import React, { useMemo } from 'react';
import { AlertCircle, CheckCircle2, Eye, Heart, MessageCircle, RefreshCw, Share2, Users, Video } from 'lucide-react';
import { Account } from '../types';

interface Props { accounts: Account[] }

const compact = new Intl.NumberFormat('vi-VN', { notation: 'compact', maximumFractionDigits: 1 });
const format = (value: number | null) => value === null ? '—' : compact.format(value);

export const AccountPerformanceSummary: React.FC<Props> = ({ accounts }) => {
  const metrics = useMemo(() => {
    const operational = accounts.filter((account) => !account.is_sold);
    const sumKnown = (
      field: 'video_count' | 'total_views' | 'follower_count' | 'likes_count' | 'total_comments' | 'total_shares',
    ) => {
      const known = operational.filter((account) => account[field] !== null && account[field] !== undefined);
      return {
        value: known.length ? known.reduce((sum, account) => sum + Number(account[field] || 0), 0) : null,
        coverage: known.length,
      };
    };
    return {
      operationalCount: operational.length,
      archivedCount: accounts.length - operational.length,
      canPost: operational.filter((account) => (account.upload_success_count || 0) > 0).length,
      retry: operational.filter((account) => account.last_upload_status === 'FAILED').length,
      verifiedPosts: operational.reduce((sum, account) => sum + (account.upload_success_count || 0), 0),
      failedAttempts: operational.reduce((sum, account) => sum + (account.upload_failure_count || 0), 0),
      synced: operational.filter((account) => ['SUCCESS', 'PARTIAL'].includes(account.analytics_sync_status)).length,
      videos: sumKnown('video_count'),
      views: sumKnown('total_views'),
      followers: sumKnown('follower_count'),
      videoLikes: sumKnown('likes_count'),
      comments: sumKnown('total_comments'),
      shares: sumKnown('total_shares'),
    };
  }, [accounts]);

  const coverage = (known: number) => known
    ? `Đã đồng bộ ${known}/${metrics.operationalCount} account`
    : 'Chưa có dữ liệu đồng bộ';
  const cards = [
    { label: 'Đăng được video', value: `${metrics.canPost}/${metrics.operationalCount}`, detail: 'Đã xác minh ≥ 1 bài', icon: CheckCircle2, tone: 'text-emerald-300 bg-emerald-500/10' },
    { label: 'Cần chạy lại', value: compact.format(metrics.retry), detail: `${metrics.failedAttempts} lượt đăng lỗi`, icon: AlertCircle, tone: 'text-rose-300 bg-rose-500/10' },
    { label: 'Bài đã xác minh', value: compact.format(metrics.verifiedPosts), detail: 'Có mặt trong Studio Posts', icon: Video, tone: 'text-brand bg-brand/10' },
    { label: 'Video TikTok', value: format(metrics.videos.value), detail: coverage(metrics.videos.coverage), icon: RefreshCw, tone: 'text-violet-300 bg-violet-500/10' },
    { label: 'Tổng view', value: format(metrics.views.value), detail: coverage(metrics.views.coverage), icon: Eye, tone: 'text-sky-300 bg-sky-500/10' },
    { label: 'Follower', value: format(metrics.followers.value), detail: coverage(metrics.followers.coverage), icon: Users, tone: 'text-amber-300 bg-amber-500/10' },
    { label: 'Lượt thích profile', value: format(metrics.videoLikes.value), detail: coverage(metrics.videoLikes.coverage), icon: Heart, tone: 'text-pink-300 bg-pink-500/10' },
    { label: 'Bình luận', value: format(metrics.comments.value), detail: coverage(metrics.comments.coverage), icon: MessageCircle, tone: 'text-cyan-300 bg-cyan-500/10' },
    { label: 'Chia sẻ', value: format(metrics.shares.value), detail: coverage(metrics.shares.coverage), icon: Share2, tone: 'text-indigo-300 bg-indigo-500/10' },
  ];

  return <section className="card overflow-hidden" aria-labelledby="account-performance-title">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line-soft px-4 py-3">
      <div>
        <h2 id="account-performance-title" className="text-sm font-bold text-fg">Tổng hợp khả năng đăng & hiệu suất</h2>
        <p className="mt-1 text-xs text-fg-muted">Chỉ tính account đang vận hành. Dấu “—” nghĩa là chưa có dữ liệu thật để tổng hợp.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <span className="badge border-brand/25 bg-brand/10 text-brand">{metrics.operationalCount} đang vận hành</span>
        {metrics.archivedCount > 0 && <span className="badge border-slate-500/25 bg-slate-500/10 text-slate-300">{metrics.archivedCount} ĐÃ BÁN · loại trừ</span>}
        <span className="badge border-line bg-white/5 text-fg-muted">{metrics.synced}/{metrics.operationalCount} đã đồng bộ</span>
      </div>
    </div>
    <div className="grid grid-cols-2 divide-x divide-y divide-line-soft sm:grid-cols-3 xl:grid-cols-9 xl:divide-y-0">
      {cards.map(({ label, value, detail, icon: Icon, tone }) => <article key={label} className="min-w-0 p-3.5">
        <div className={`mb-3 grid h-8 w-8 place-items-center rounded-lg ${tone}`}><Icon className="h-4 w-4" aria-hidden="true" /></div>
        <p className="text-[11px] font-semibold text-fg-muted">{label}</p>
        <p className="mt-1 text-xl font-bold tabular-nums text-fg">{value}</p>
        <p className="mt-1 truncate text-[10px] text-fg-subtle" title={detail}>{detail}</p>
      </article>)}
    </div>
  </section>;
};
