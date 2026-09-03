import React, { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Files, Film, FolderOpen, ListTree, LoaderCircle, RefreshCw, Send, Trash2, Upload, Users, X } from 'lucide-react';
import { Account } from '../types';
import { AccountFolderPickerModal } from './AccountFolderPickerModal';

interface Props { accounts: Account[]; selectedAccountIds: string[]; onSelectedAccountIdsChange: (ids: string[]) => void; concurrency: number }
interface Video { id: string; name: string; path: string; size_bytes: number }
interface Batch { id: string; status: string; created_at: string; account_count: number; videos_per_account?: number; total: number; submitted: number; completed: number; processed?: number; failed?: number }
const API = 'http://127.0.0.1:9000/api/v1/tasks';

const bytes = (value: number) => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GB` : value >= 1024 ** 2 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${Math.ceil(value / 1024)} KB`;
const batchLabel = (value: string) => ({ PENDING: 'Đang chờ', RUNNING: 'Đang chạy', DONE: 'Hoàn tất', DONE_WITH_ERRORS: 'Xong, có lỗi', CANCELLED: 'Đã dừng' }[value] || value);

export const VideoManager: React.FC<Props> = ({ accounts, selectedAccountIds, onSelectedAccountIdsChange, concurrency }) => {
  const [videos, setVideos] = useState<Video[]>([]);
  const [manualPaths, setManualPaths] = useState('');
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const [selectedFolder, setSelectedFolder] = useState<{ country: string; batch: string } | null>(null);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [videosPerAccount, setVideosPerAccount] = useState(1);

  const selectedSet = useMemo(() => new Set(selectedAccountIds), [selectedAccountIds]);
  const selectedAccounts = useMemo(
    () => accounts.filter((account) => selectedSet.has(account.id) && account.email && !account.is_sold),
    [accounts, selectedSet],
  );
  const archivedSelectedCount = useMemo(
    () => accounts.filter((account) => selectedSet.has(account.id) && account.is_sold).length,
    [accounts, selectedSet],
  );
  const hasEnoughDistinctVideos = videos.length >= videosPerAccount;
  const distribution = useMemo(() => {
    if (!selectedAccounts.length || !videos.length || !hasEnoughDistinctVideos) return [];
    return selectedAccounts.flatMap((account, accountIndex) => {
      const start = (accountIndex * videosPerAccount) % videos.length;
      return Array.from({ length: videosPerAccount }, (_, slot) => ({
        video: videos[(start + slot) % videos.length],
        account,
        accountSlot: slot + 1,
      }));
    });
  }, [selectedAccounts, videos, videosPerAccount, hasEnoughDistinctVideos]);
  const loads = useMemo(() => {
    const result = new Map<string, number>();
    distribution.forEach(({ account }) => result.set(account.email!, (result.get(account.email!) || 0) + 1));
    return [...result.entries()];
  }, [distribution]);

  const mergeVideos = (incoming: Video[]) => setVideos((current) => {
    const merged = new Map(current.map((video) => [video.path.toLowerCase(), video]));
    incoming.forEach((video) => merged.set(video.path.toLowerCase(), video));
    return [...merged.values()].sort((a, b) => a.name.localeCompare(b.name));
  });
  const loadBatches = async () => {
    try { const response = await fetch(`${API}/video-batches`); if (response.ok) setBatches((await response.json()).batches || []); } catch { /* retry next poll */ }
  };
  useEffect(() => { loadBatches(); const timer = window.setInterval(loadBatches, 4000); return () => clearInterval(timer); }, []);

  const pick = async (kind: 'files' | 'folder') => {
    setBusy(true); setError(null);
    try {
      const response = await fetch(`${API}/video-library/pick-${kind}`, { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Không mở được bộ chọn video.');
      mergeVideos(data.videos || []);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Không mở được bộ chọn video.'); }
    finally { setBusy(false); }
  };
  const scan = async () => {
    const paths = manualPaths.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    if (!paths.length) return setError('Dán ít nhất một đường dẫn file hoặc thư mục.');
    setBusy(true); setError(null);
    try {
      const response = await fetch(`${API}/video-library/scan`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paths }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Không quét được video.');
      mergeVideos(data.videos || []); setMessage(`Đã nạp ${data.count || 0} video hợp lệ.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Không quét được video.'); }
    finally { setBusy(false); }
  };
  const start = async () => {
    if (!videos.length) return setError('Kho video đang trống.');
    if (!selectedAccounts.length) return setError('Chọn ít nhất một Hotmail nhận video.');
    if (!hasEnoughDistinctVideos) return setError(`Cần ít nhất ${videosPerAccount} video khác nhau để không trùng trong một Hotmail.`);
    setBusy(true); setError(null); setMessage(null);
    try {
      const response = await fetch(`${API}/video-batches`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ account_ids: selectedAccounts.map((account) => account.email), video_paths: videos.map((video) => video.path), videos_per_account: videosPerAccount, proxy_concurrency: concurrency }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Không tạo được đợt đăng video.');
      setMessage(data.message); setVideos([]); setManualPaths(''); await loadBatches();
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Không tạo được đợt đăng video.'); }
    finally { setBusy(false); }
  };
  const cancel = async (id: string) => {
    const response = await fetch(`${API}/video-batches/${id}`, { method: 'DELETE' }); const data = await response.json();
    if (response.ok) setMessage(data.message);
    else setError(data.detail || 'Không thể dừng đợt này.');
    await loadBatches();
  };

  return <section className="flex flex-col gap-4" aria-labelledby="video-manager-title">
    <header className="card flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div><div className="mb-1 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.16em] text-brand"><Film className="h-4 w-4" /> Kho nội dung</div><h2 id="video-manager-title" className="text-xl font-bold text-fg">Quản lý & tự chia video</h2><p className="mt-1 text-sm text-fg-muted">Nạp toàn bộ video, xem trước cách chia rồi chạy lần lượt theo từng Hotmail.</p></div>
      <div className="flex flex-wrap gap-2"><span className="badge border-line bg-white/5 text-fg"><Film className="h-3.5 w-3.5" /> {videos.length} video</span><span className="badge border-line bg-white/5 text-fg"><Users className="h-3.5 w-3.5" /> {selectedAccounts.length} Hotmail có thể chạy</span>{archivedSelectedCount > 0 && <span className="badge border-slate-500/25 bg-slate-500/10 text-slate-300">{archivedSelectedCount} ĐÃ BÁN bị loại</span>}</div>
    </header>
    {error && <p role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200">{error}</p>}
    {message && <p role="status" className="flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-200"><CheckCircle2 className="h-4 w-4" />{message}</p>}

    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <section className="card overflow-hidden">
        <div className="border-b border-line-soft p-4"><h3 className="font-bold text-fg">1. Nạp kho video</h3><p className="mt-1 text-xs text-fg-muted">MP4, MOV, WEBM, M4V; thư mục được quét cả thư mục con.</p>
          <div className="mt-3 flex flex-wrap gap-2"><button disabled={busy} onClick={() => pick('files')} className="btn btn-primary"><Files className="h-4 w-4" /> Chọn nhiều video</button><button disabled={busy} onClick={() => pick('folder')} className="btn btn-ghost"><FolderOpen className="h-4 w-4" /> Chọn thư mục</button>{videos.length > 0 && <button onClick={() => setVideos([])} className="btn btn-danger"><Trash2 className="h-4 w-4" /> Xóa kho</button>}</div>
          <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto]"><textarea value={manualPaths} onChange={(event) => setManualPaths(event.target.value)} rows={2} className="field resize-y font-mono text-xs" placeholder={'D:\\videos\\batch-01\nD:\\videos\\clip.mp4'} /><button disabled={busy} onClick={scan} className="btn btn-ghost sm:self-stretch"><Upload className="h-4 w-4" /> Nạp đường dẫn</button></div>
        </div>
        <div className="max-h-[390px] overflow-y-auto p-2">{!videos.length ? <div className="grid min-h-48 place-items-center text-center text-sm text-fg-muted"><div><Film className="mx-auto mb-2 h-8 w-8 text-fg-subtle" />Chưa có video trong kho.</div></div> : <ul className="space-y-1">{videos.slice(0, 200).map((video, index) => <li key={video.id} className="flex items-center gap-3 rounded-xl px-3 py-2 hover:bg-white/[0.03]"><span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand/10 text-xs font-bold text-brand">{index + 1}</span><span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold text-fg">{video.name}</span><span className="block truncate font-mono text-[10px] text-fg-subtle">{video.path}</span></span><span className="text-xs text-fg-muted">{bytes(video.size_bytes)}</span><button aria-label={`Xóa ${video.name}`} onClick={() => setVideos((items) => items.filter((item) => item.id !== video.id))} className="grid h-9 w-9 place-items-center rounded-lg text-fg-muted hover:bg-rose-500/10 hover:text-rose-300"><X className="h-4 w-4" /></button></li>)}</ul>}{videos.length > 200 && <p className="p-3 text-center text-xs text-fg-muted">Ẩn {videos.length - 200} dòng để giao diện mượt; tất cả vẫn được chạy.</p>}</div>
      </section>

      <section className="card flex min-h-[520px] flex-col overflow-hidden">
        <div className="border-b border-line-soft p-4"><h3 className="font-bold text-fg">2. Chọn thư mục account</h3><p className="mt-1 text-xs text-fg-muted">Mở cây thư mục giống Quản lý tài khoản và chọn đúng Lô cần chạy.</p></div>
        <div className="grid flex-1 place-items-center p-5">
          <div className="w-full max-w-lg rounded-2xl border border-line-soft bg-surface-2/60 p-5 text-center">
            <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-brand/10 text-brand"><ListTree className="h-7 w-7" aria-hidden="true" /></span>
            {selectedFolder ? <><p className="mt-4 text-xs font-bold uppercase tracking-wider text-brand">Thư mục đang dùng</p><h4 className="mt-1 text-lg font-bold text-fg"><span className="mr-2 badge border-brand/30 bg-brand/10 text-brand">{selectedFolder.country}</span>{selectedFolder.batch}</h4><p className="mt-2 text-sm text-fg-muted"><strong className="text-fg">{selectedAccounts.length}</strong> Hotmail · mỗi Hotmail nhận <strong className="text-brand">{videosPerAccount}</strong> video không trùng.</p></> : <><h4 className="mt-4 text-base font-bold text-fg">Chưa chọn thư mục account</h4><p className="mt-2 text-sm leading-6 text-fg-muted">Không cần tick từng account. Chọn một Lô và hệ thống sẽ lấy toàn bộ Hotmail trong đó.</p></>}
            <button type="button" onClick={() => setFolderPickerOpen(true)} className="btn btn-primary mt-5 min-h-12 w-full"><FolderOpen className="h-4 w-4" aria-hidden="true" /> {selectedFolder ? 'Đổi thư mục account' : 'Mở cây thư mục account'}</button>
            {selectedFolder && <button type="button" onClick={() => { setSelectedFolder(null); onSelectedAccountIdsChange([]); }} className="btn btn-ghost mt-2 min-h-11 w-full"><X className="h-4 w-4" aria-hidden="true" /> Bỏ thư mục đã chọn</button>}
          </div>
        </div>
      </section>
    </div>

    <section className="card overflow-hidden"><div className="border-b border-line-soft p-4"><div className="grid gap-4 md:grid-cols-[1fr_220px] md:items-end"><div><h3 className="font-bold text-fg">3. Xem trước phân phối</h3><p className="mt-1 text-xs leading-5 text-fg-muted">Trong một Hotmail không trùng video; video được phép dùng lại ở Hotmail khác. Mỗi Hotmail đăng tuần tự trong cùng một browser.</p></div><div><label htmlFor="videos-per-account" className="mb-1.5 block text-xs font-bold text-fg">Số video mỗi Hotmail</label><input id="videos-per-account" type="number" min={1} step={1} value={videosPerAccount} onChange={(event) => setVideosPerAccount(Math.max(1, Math.floor(Number(event.target.value) || 1)))} aria-describedby="videos-per-account-help" aria-invalid={videos.length > 0 && !hasEnoughDistinctVideos} className="field min-h-11 w-full tabular-nums" /><p id="videos-per-account-help" className={`mt-1.5 text-[11px] ${videos.length > 0 && !hasEnoughDistinctVideos ? 'text-rose-300' : 'text-fg-subtle'}`}>{videos.length > 0 && !hasEnoughDistinctVideos ? `Kho cần ít nhất ${videosPerAccount} video khác nhau.` : `${distribution.length} lượt đăng dự kiến.`}</p></div></div>{loads.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{loads.map(([email, count]) => <span key={email} className="badge border-line bg-white/5 normal-case tracking-normal text-fg-muted">{email}: <strong className="text-brand">{count}</strong></span>)}</div>}</div>
      {!distribution.length ? <div className="p-8 text-center text-sm text-fg-muted">{videos.length > 0 && !hasEnoughDistinctVideos ? `Cần thêm ${videosPerAccount - videos.length} video khác nhau để chia đúng yêu cầu.` : 'Nạp video và chọn Hotmail để xem bảng chia.'}</div> : <div className="max-h-80 overflow-auto"><table className="w-full min-w-[720px] text-left text-sm"><thead className="sticky top-0 bg-surface-2 text-[11px] uppercase text-fg-subtle"><tr><th className="px-4 py-3">#</th><th className="px-4 py-3">Video / caption</th><th className="px-4 py-3">Hotmail nhận</th></tr></thead><tbody className="divide-y divide-line-soft">{distribution.slice(0, 100).map(({ video, account, accountSlot }, index) => <tr key={`${account.id}:${video.id}`}><td className="px-4 py-3 text-fg-subtle">{index + 1}</td><td className="max-w-md px-4 py-3"><p className="truncate font-medium text-fg">{video.name.replace(/\.[^.]+$/, '')}</p><p className="truncate text-xs text-fg-subtle">{video.name}</p></td><td className="px-4 py-3 font-semibold text-fg"><p>{account.email}</p><p className="mt-0.5 text-[10px] font-normal text-fg-subtle">Video {accountSlot}/{videosPerAccount} · cùng phiên browser</p></td></tr>)}</tbody></table>{distribution.length > 100 && <p className="p-3 text-center text-xs text-fg-muted">Còn {distribution.length - 100} phân công; tất cả sẽ được chạy.</p>}</div>}
      <div className="border-t border-line-soft bg-surface-2/60 p-4"><button disabled={busy || !distribution.length || !hasEnoughDistinctVideos} onClick={start} className="btn btn-primary min-h-12 w-full">{busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}{busy ? 'Đang xử lý...' : `Xếp hàng ${distribution.length} lượt đăng cho ${selectedAccounts.length} Hotmail`}</button></div>
    </section>

    <section className="card overflow-hidden"><div className="flex items-center justify-between border-b border-line-soft p-4"><div><h3 className="font-bold text-fg">Các đợt đang chạy</h3><p className="mt-1 text-xs text-fg-muted">Tự cập nhật mỗi 4 giây.</p></div><button onClick={loadBatches} className="btn btn-sm btn-ghost"><RefreshCw className="h-3.5 w-3.5" /> Làm mới</button></div>
      {!batches.length ? <div className="p-8 text-center text-sm text-fg-muted">Chưa có đợt đăng hàng loạt.</div> : <div className="divide-y divide-line-soft">{batches.map((batch) => { const processed = batch.processed ?? batch.completed; return <div key={batch.id} className="flex flex-wrap items-center gap-4 p-4"><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className={`badge normal-case ${batch.status === 'DONE' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300' : batch.status === 'RUNNING' ? 'border-brand/30 bg-brand/10 text-brand' : 'border-line bg-white/5 text-fg-muted'}`}>{batchLabel(batch.status)}</span><span className="text-xs text-fg-subtle">{new Date(batch.created_at).toLocaleString('vi-VN')}</span></div><p className="mt-2 text-sm font-semibold text-fg">{processed}/{batch.total} đã xử lý · <span className="text-emerald-300">{batch.completed} thành công</span>{Boolean(batch.failed) && <> · <span className="text-rose-300">{batch.failed} lỗi</span></>} · {batch.account_count} Hotmail · {batch.videos_per_account || 1} video/Hotmail</p><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/5"><div className="h-full rounded-full bg-brand" style={{ width: `${batch.total ? Math.round(processed / batch.total * 100) : 0}%` }} /></div></div>{['PENDING', 'RUNNING'].includes(batch.status) && <button onClick={() => cancel(batch.id)} className="btn btn-sm btn-danger"><X className="h-3.5 w-3.5" /> Dừng cấp video</button>}</div>; })}</div>}
    </section>

    <AccountFolderPickerModal
      isOpen={folderPickerOpen}
      accounts={accounts}
      initialCountry={selectedFolder?.country || null}
      initialBatch={selectedFolder?.batch || null}
      initialAccountIds={selectedAccountIds}
      onClose={() => setFolderPickerOpen(false)}
      onConfirm={(country, batch, accountIds) => {
        setSelectedFolder({ country, batch });
        onSelectedAccountIdsChange(accountIds);
        setFolderPickerOpen(false);
        setError(null);
        setMessage(`Đã chọn Lô ${country} / ${batch} với ${accountIds.length} Hotmail.`);
      }}
    />
  </section>;
};
