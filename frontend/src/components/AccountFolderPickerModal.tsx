import React, { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle, Check, CheckCircle2, ChevronDown, ChevronRight, CircleDashed,
  Folder, FolderOpen, Globe, Mail, Search, Users, X,
} from 'lucide-react';
import { Account } from '../types';
import { getCountryFlagUrl } from '../utils/countries';

interface Props {
  isOpen: boolean;
  accounts: Account[];
  initialCountry: string | null;
  initialBatch: string | null;
  initialAccountIds: string[];
  onClose: () => void;
  onConfirm: (country: string, batch: string, accountIds: string[]) => void;
}

type TreeData = Record<string, Record<string, Account[]>>;

export const AccountFolderPickerModal: React.FC<Props> = ({
  isOpen, accounts, initialCountry, initialBatch, initialAccountIds, onClose, onConfirm,
}) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [country, setCountry] = useState<string | null>(initialCountry);
  const [batch, setBatch] = useState<string | null>(initialBatch);
  const [expanded, setExpanded] = useState<string[]>(initialCountry ? [initialCountry] : []);
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const [chosenIds, setChosenIds] = useState<string[]>([]);

  const tree = useMemo(() => accounts.reduce<TreeData>((result, account) => {
    const accountCountry = account.country || 'US';
    const accountBatch = account.batch_tag || 'DEFAULT';
    result[accountCountry] ||= {};
    result[accountCountry][accountBatch] ||= [];
    result[accountCountry][accountBatch].push(account);
    return result;
  }, {}), [accounts]);
  const countries = useMemo(() => Object.keys(tree).sort(), [tree]);
  const selectedAccounts = useMemo(
    () => country && batch ? (tree[country]?.[batch] || []) : [],
    [batch, country, tree],
  );
  const operationalAccounts = useMemo(
    () => selectedAccounts.filter((account) => !account.is_sold),
    [selectedAccounts],
  );
  const archivedCount = selectedAccounts.length - operationalAccounts.length;
  const visibleSelectedAccounts = useMemo(() => {
    const needle = deferredQuery.trim().toLowerCase();
    if (!needle) return selectedAccounts;
    return selectedAccounts.filter((account) => [account.email, account.username]
      .some((value) => String(value || '').toLowerCase().includes(needle)));
  }, [deferredQuery, selectedAccounts]);
  const chosenSet = useMemo(() => new Set(chosenIds), [chosenIds]);
  const failedAccounts = useMemo(
    () => operationalAccounts.filter((account) => account.last_upload_status === 'FAILED'),
    [operationalAccounts],
  );
  const notSuccessfulAccounts = useMemo(
    () => operationalAccounts.filter((account) => (account.upload_success_count || 0) === 0),
    [operationalAccounts],
  );

  useEffect(() => {
    if (!isOpen) return;
    setCountry(initialCountry);
    setBatch(initialBatch);
    setExpanded(initialCountry ? [initialCountry] : []);
    setQuery('');
    if (initialCountry && initialBatch) {
      const folderIds = (tree[initialCountry]?.[initialBatch] || [])
        .filter((account) => !account.is_sold)
        .map((account) => account.id);
      const preserved = folderIds.filter((id) => initialAccountIds.includes(id));
      setChosenIds(preserved.length ? preserved : folderIds);
    } else {
      setChosenIds([]);
    }
    window.setTimeout(() => dialogRef.current?.focus(), 0);
  }, [initialAccountIds, initialBatch, initialCountry, isOpen, tree]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key !== 'Tab' || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)'));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const selectBatch = (nextCountry: string, nextBatch: string) => {
    setCountry(nextCountry);
    setBatch(nextBatch);
    setChosenIds((tree[nextCountry]?.[nextBatch] || [])
      .filter((account) => !account.is_sold)
      .map((account) => account.id));
    setQuery('');
  };
  const toggleAccount = (account: Account) => {
    if (account.is_sold) return;
    setChosenIds((current) => current.includes(account.id)
      ? current.filter((id) => id !== account.id)
      : [...current, account.id]);
  };
  const setChosenAccounts = (items: Account[]) => setChosenIds(items
    .filter((account) => !account.is_sold)
    .map((account) => account.id));

  return <div className="fixed inset-0 z-[100] grid place-items-center bg-slate-950/80 p-3 backdrop-blur-sm" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="folder-picker-title" tabIndex={-1} className="card flex max-h-[90dvh] w-full max-w-6xl flex-col overflow-hidden shadow-2xl">
      <header className="flex items-start justify-between gap-4 border-b border-line-soft p-4 sm:p-5">
        <div>
          <div className="mb-1 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.14em] text-brand"><Globe className="h-4 w-4" aria-hidden="true" /> Cây thư mục tài khoản</div>
          <h2 id="folder-picker-title" className="text-lg font-bold text-fg">Chọn tài khoản nhận video</h2>
          <p className="mt-1 text-sm text-fg-muted">Chọn Lô bên trái, sau đó tích chính xác các Hotmail cần chạy hoặc cần thử lại.</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Đóng cửa sổ chọn tài khoản" className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-fg-muted hover:bg-white/5 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"><X className="h-5 w-5" /></button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[minmax(280px,.72fr)_minmax(0,1.28fr)]">
        <section className="min-h-0 border-b border-line-soft p-3 md:border-b-0 md:border-r">
          <div className="mb-2 flex items-center justify-between px-2 py-1"><span className="text-[11px] font-bold uppercase tracking-wider text-fg-subtle">Quốc gia / Lô</span><span className="badge border-line bg-white/5 text-fg-muted">{accounts.length} acc</span></div>
          <div className="max-h-[34dvh] space-y-1 overflow-y-auto pr-1 md:max-h-[54dvh]">
            {countries.map((itemCountry) => {
              const batches = Object.keys(tree[itemCountry]).sort();
              const isExpanded = expanded.includes(itemCountry);
              const total = Object.values(tree[itemCountry]).reduce((sum, values) => sum + values.length, 0);
              return <div key={itemCountry} className="space-y-0.5">
                <button type="button" aria-expanded={isExpanded} onClick={() => setExpanded((items) => items.includes(itemCountry) ? items.filter((item) => item !== itemCountry) : [...items, itemCountry])} className="flex min-h-11 w-full items-center justify-between rounded-lg p-2 text-left hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                  <span className="flex min-w-0 items-center gap-2">{isExpanded ? <ChevronDown className="h-3.5 w-3.5 text-fg-subtle" /> : <ChevronRight className="h-3.5 w-3.5 text-fg-subtle" />}<img src={getCountryFlagUrl(itemCountry)} alt="" className="h-3.5 w-[18px] rounded-sm border border-line-soft object-cover" onError={(event) => { event.currentTarget.style.display = 'none'; }} /><span className="truncate text-xs font-bold uppercase tracking-wider text-fg">{itemCountry}</span></span>
                  <span className="badge border-line bg-slate-900 text-fg-muted">{total}</span>
                </button>
                {isExpanded && <div className="ml-3.5 space-y-0.5 border-l border-line-soft/80 pl-6">{batches.map((itemBatch) => {
                  const selected = country === itemCountry && batch === itemBatch;
                  const count = tree[itemCountry][itemBatch].length;
                  return <button type="button" key={itemBatch} aria-pressed={selected} onClick={() => selectBatch(itemCountry, itemBatch)} className={`flex min-h-11 w-full items-center justify-between rounded-lg border p-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${selected ? 'border-brand/30 bg-brand/10 text-brand' : 'border-transparent text-fg-muted hover:bg-white/[0.03] hover:text-fg'}`}>
                    <span className="flex min-w-0 items-center gap-2">{selected ? <FolderOpen className="h-4 w-4 shrink-0 text-brand" /> : <Folder className="h-4 w-4 shrink-0 text-fg-subtle" />}<span className="truncate text-xs font-semibold">{itemBatch}</span></span><span className="badge border-line bg-slate-900 text-fg-muted">{count} acc</span>
                  </button>;
                })}</div>}
              </div>;
            })}
          </div>
        </section>

        <section className="flex min-h-0 flex-col p-3 sm:p-4">
          {!country || !batch ? <div className="grid min-h-64 flex-1 place-items-center text-center"><div><FolderOpen className="mx-auto h-10 w-10 text-fg-subtle" /><p className="mt-3 font-semibold text-fg">Chưa chọn Lô</p><p className="mt-1 text-sm text-fg-muted">Mở một quốc gia rồi chọn thư mục con.</p></div></div> : <>
            <div className="border-b border-line-soft pb-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><p className="flex items-center gap-2 font-bold text-fg"><span className="badge border-brand/30 bg-brand/10 text-brand">{country}</span>{batch}</p><p className="mt-1 text-xs text-fg-muted">{operationalAccounts.length} có thể chạy · {chosenIds.length} đã tích · {failedAccounts.length} lỗi lần cuối{archivedCount > 0 ? ` · ${archivedCount} ĐÃ BÁN bị khóa` : ''}</p></div>
                <div className="relative"><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" /><input value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Tìm trong Lô đã chọn" className="field min-h-11 w-64 max-w-full pl-9" placeholder="Tìm Hotmail, username..." /></div>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Chọn nhanh tài khoản">
                <button type="button" onClick={() => setChosenAccounts(operationalAccounts)} className="btn btn-sm btn-ghost">Tất cả có thể chạy ({operationalAccounts.length})</button>
                <button type="button" onClick={() => setChosenAccounts(notSuccessfulAccounts)} className="btn btn-sm btn-ghost">Chưa đăng được ({notSuccessfulAccounts.length})</button>
                <button type="button" onClick={() => setChosenAccounts(failedAccounts)} className="btn btn-sm border border-rose-500/25 bg-rose-500/10 text-rose-300">Lỗi lần cuối ({failedAccounts.length})</button>
                <button type="button" onClick={() => setChosenIds([])} className="btn btn-sm btn-ghost">Bỏ chọn</button>
              </div>
            </div>
            <ul className="mt-2 max-h-[43dvh] flex-1 space-y-1 overflow-y-auto pr-1">
              {visibleSelectedAccounts.map((account) => {
                const checked = chosenSet.has(account.id);
                const lastStatus = account.last_upload_status || 'NEVER';
                return <li key={account.id} style={{ contentVisibility: 'auto', containIntrinsicSize: '56px' }}>
                  <label className={`flex min-h-14 items-center gap-3 rounded-lg border px-3 py-2 transition-colors ${account.is_sold ? 'cursor-not-allowed border-slate-500/15 bg-slate-500/[0.04] opacity-65' : checked ? 'cursor-pointer border-brand/25 bg-brand/[0.07]' : 'cursor-pointer border-transparent hover:bg-white/[0.03]'}`}>
                    <input type="checkbox" checked={checked} disabled={account.is_sold} onChange={() => toggleAccount(account)} aria-label={account.is_sold ? `${account.email} đã bán, không thể chọn` : `Chọn ${account.email}`} className="h-4 w-4 shrink-0 accent-teal-400 disabled:cursor-not-allowed" />
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand/10 text-brand"><Mail className="h-4 w-4" /></span>
                    <span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold text-fg">{account.email}</span><span className="block truncate text-xs text-fg-muted">@{account.username || '—'} · {account.health_status === 'ALIVE' ? 'Sống' : account.health_status || 'Chưa kiểm tra'}</span></span>
                    {account.is_sold ? <span className="badge shrink-0 border-slate-500/30 bg-slate-500/10 text-slate-300 normal-case">ĐÃ BÁN · lưu trữ</span> : <span className="shrink-0 text-right">
                      <span className={`flex items-center justify-end gap-1 text-xs font-semibold ${lastStatus === 'SUCCESS' ? 'text-emerald-300' : lastStatus === 'FAILED' ? 'text-rose-300' : 'text-fg-subtle'}`}>
                        {lastStatus === 'SUCCESS' ? <CheckCircle2 className="h-3.5 w-3.5" /> : lastStatus === 'FAILED' ? <AlertCircle className="h-3.5 w-3.5" /> : <CircleDashed className="h-3.5 w-3.5" />}
                        {lastStatus === 'SUCCESS' ? 'Đã đăng được' : lastStatus === 'FAILED' ? 'Lỗi lần cuối' : 'Chưa thử'}
                      </span>
                      <span className="mt-0.5 block text-[10px] tabular-nums text-fg-subtle">{account.upload_success_count || 0} thành công · {account.upload_failure_count || 0} lỗi</span>
                    </span>}
                  </label>
                </li>;
              })}
            </ul>
            {!visibleSelectedAccounts.length && <p className="py-8 text-center text-sm text-fg-muted">Không tìm thấy tài khoản phù hợp.</p>}
          </>}
        </section>
      </div>

      <footer className="flex flex-col-reverse items-stretch justify-between gap-3 border-t border-line-soft bg-surface-2/70 p-4 sm:flex-row sm:items-center">
        <p className="flex items-center gap-2 text-sm text-fg-muted"><Users className="h-4 w-4 text-brand" />{selectedAccounts.length ? <><strong className="text-fg">{chosenIds.length}</strong>/{operationalAccounts.length} Hotmail có thể chạy sẽ nhận video{archivedCount > 0 ? ` · bỏ qua ${archivedCount} đã bán` : ''}</> : 'Hãy chọn một Lô'}</p>
        <div className="flex gap-2"><button type="button" onClick={onClose} className="btn btn-ghost min-h-11 flex-1 sm:flex-none">Hủy</button><button type="button" disabled={!country || !batch || !chosenIds.length} onClick={() => onConfirm(country!, batch!, chosenIds)} className="btn btn-primary min-h-11 flex-1 sm:flex-none"><Check className="h-4 w-4" /> Dùng {chosenIds.length} tài khoản</button></div>
      </footer>
    </div>
  </div>;
};
