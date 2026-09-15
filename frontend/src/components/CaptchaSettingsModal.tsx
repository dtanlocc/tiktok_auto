import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { invoke } from '@tauri-apps/api/core';
import { relaunch } from '@tauri-apps/plugin-process';
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  EyeOff,
  Globe2,
  KeyRound,
  LoaderCircle,
  ShieldCheck,
  X,
} from 'lucide-react';
import { isTauriRuntime } from '../services/secureTransport';

type SecurityStatus = {
  captchaConfigured: boolean;
};

type DispatcherStatus = {
  active_count?: number;
  queued_count?: number;
};

type NordVpnExtensionStatus = {
  enabled: boolean;
  package_available: boolean;
  authenticated_state_available: boolean;
  message?: string;
};

const TASKS_API = 'http://127.0.0.1:9000/api/v1/tasks';

async function requireIdleBrowserRuntime(): Promise<void> {
  const [dispatcherResponse, debugResponse, blankResponse] = await Promise.all([
    fetch(`${TASKS_API}/status`),
    fetch(`${TASKS_API}/debug-login/active`),
    fetch(`${TASKS_API}/debug-blank/active`),
  ]);
  if (!dispatcherResponse.ok || !debugResponse.ok || !blankResponse.ok) {
    throw new Error('Không kiểm tra được trạng thái browser. Hãy thử lại sau.');
  }

  const dispatcher = await dispatcherResponse.json() as DispatcherStatus;
  const debug = await debugResponse.json() as { active_ids?: string[] };
  const blank = await blankResponse.json() as { active?: boolean };
  const active = Number(dispatcher.active_count || 0);
  const queued = Number(dispatcher.queued_count || 0);
  if (active > 0 || queued > 0 || (debug.active_ids?.length || 0) > 0 || blank.active) {
    throw new Error(
      'Đang có task hoặc browser mở. Hãy chờ hoàn tất/đóng browser trước khi đổi API key.',
    );
  }
}

interface CaptchaSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function CaptchaSettingsModal({ isOpen, onClose }: CaptchaSettingsModalProps) {
  const [apiKey, setApiKey] = useState('');
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [nordVpnStatus, setNordVpnStatus] = useState<NordVpnExtensionStatus | null>(null);
  const [nordVpnSaving, setNordVpnSaving] = useState(false);
  const [nordVpnError, setNordVpnError] = useState('');
  const [nordVpnMessage, setNordVpnMessage] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const savingRef = useRef(false);
  const nordVpnSavingRef = useRef(false);
  const desktopRuntime = isTauriRuntime();

  useEffect(() => {
    if (!isOpen) return;
    setApiKey('');
    setShowKey(false);
    setError('');
    setConfigured(null);
    setSaving(false);
    setNordVpnStatus(null);
    setNordVpnSaving(false);
    setNordVpnError('');
    setNordVpnMessage('');
    savingRef.current = false;
    nordVpnSavingRef.current = false;
    let disposed = false;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !savingRef.current && !nordVpnSavingRef.current) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);

    void fetch(`${TASKS_API}/nordvpn-extension`)
      .then(async (response) => {
        const data = await response.json() as NordVpnExtensionStatus & { detail?: string };
        if (!response.ok) throw new Error(data.detail || 'Không đọc được cài đặt NordVPN.');
        if (!disposed) setNordVpnStatus(data);
      })
      .catch((reason: unknown) => {
        if (!disposed) {
          setNordVpnError(reason instanceof Error ? reason.message : String(reason));
        }
      });

    if (desktopRuntime) {
      void invoke<SecurityStatus>('security_status')
        .then((status) => {
          setConfigured(status.captchaConfigured);
          window.requestAnimationFrame(() => inputRef.current?.focus());
        })
        .catch((reason: unknown) => {
          setError(reason instanceof Error ? reason.message : String(reason));
          setConfigured(false);
        });
    } else {
      setConfigured(false);
    }

    return () => {
      disposed = true;
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [desktopRuntime, isOpen, onClose]);

  if (!isOpen) return null;

  const toggleNordVpn = async () => {
    if (!nordVpnStatus || nordVpnSaving) return;
    const enabled = !nordVpnStatus.enabled;
    nordVpnSavingRef.current = true;
    setNordVpnSaving(true);
    setNordVpnError('');
    setNordVpnMessage('');
    try {
      const response = await fetch(`${TASKS_API}/nordvpn-extension`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      const data = await response.json() as NordVpnExtensionStatus & { detail?: string };
      if (!response.ok) throw new Error(data.detail || 'Không đổi được cài đặt NordVPN.');
      setNordVpnStatus(data);
      setNordVpnMessage(
        data.message
          || (data.enabled ? 'Đã bật NordVPN cho browser mở sau.' : 'Đã tắt NordVPN cho browser mở sau.'),
      );
    } catch (reason) {
      setNordVpnError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      nordVpnSavingRef.current = false;
      setNordVpnSaving(false);
    }
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const value = apiKey.trim();
    setError('');
    if (!desktopRuntime) {
      setError('Chế độ web/dev dùng OMOCAPTCHA_KEY trong backend/.env và cần khởi động lại backend.');
      return;
    }
    const hasControlCharacter = [...value].some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint < 32 || codePoint === 127;
    });
    if (value.length < 8 || value.length > 512 || hasControlCharacter) {
      setError('API key phải dài 8–512 ký tự và không chứa ký tự điều khiển.');
      return;
    }

    savingRef.current = true;
    setSaving(true);
    try {
      await requireIdleBrowserRuntime();
      await invoke('set_captcha_api_key', { apiKey: value });
      setApiKey('');
      setConfigured(true);
      await relaunch();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      savingRef.current = false;
      setSaving(false);
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-black/75 p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !saving && !nordVpnSaving) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="captcha-settings-title"
        className="max-h-[calc(100vh-2rem)] w-full max-w-lg overflow-y-auto rounded-2xl border border-line bg-elevated shadow-2xl shadow-black/50"
      >
        <header className="flex items-start justify-between gap-4 border-b border-line-soft px-5 py-4">
          <div className="flex min-w-0 items-start gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-brand/25 bg-brand/10 text-brand">
              <KeyRound className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h2 id="captcha-settings-title" className="text-base font-bold text-fg">
                Cài đặt extension
              </h2>
              <p className="mt-1 text-xs leading-5 text-fg-muted">
                Chọn extension được gắn vào những browser mở sau.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={saving || nordVpnSaving}
            aria-label="Đóng cài đặt extension"
            className="grid h-11 w-11 shrink-0 cursor-pointer place-items-center rounded-xl text-fg-muted transition-colors hover:bg-white/5 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </header>

        <div className="space-y-4 p-5">
          <section className="rounded-xl border border-line bg-surface/60 p-4" aria-labelledby="nordvpn-extension-title">
            <div className="flex items-start justify-between gap-4">
              <div className="flex min-w-0 items-start gap-3">
                <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-sky-400/25 bg-sky-400/[0.08] text-sky-300">
                  <Globe2 className="h-4.5 w-4.5" aria-hidden="true" />
                </div>
                <div>
                  <h3 id="nordvpn-extension-title" className="text-sm font-bold text-fg">
                    NordVPN
                  </h3>
                  <p className="mt-1 text-xs leading-5 text-fg-muted">
                    {nordVpnStatus === null
                      ? 'Đang đọc trạng thái extension…'
                      : nordVpnStatus.enabled
                        ? 'Sẽ gắn vào mọi browser mở sau.'
                        : 'Không gắn vào browser mới; dữ liệu đăng nhập vẫn được giữ.'}
                  </p>
                </div>
              </div>

              <button
                type="button"
                role="switch"
                aria-checked={Boolean(nordVpnStatus?.enabled)}
                aria-label="Gắn extension NordVPN vào browser mới"
                onClick={toggleNordVpn}
                disabled={
                  nordVpnSaving
                  || nordVpnStatus === null
                  || (!nordVpnStatus.package_available && !nordVpnStatus.enabled)
                }
                className={`relative h-11 w-[68px] shrink-0 cursor-pointer rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 disabled:cursor-not-allowed disabled:opacity-45 ${
                  nordVpnStatus?.enabled
                    ? 'border-brand/50 bg-brand/25'
                    : 'border-line bg-black/20'
                }`}
              >
                <span
                  className={`absolute top-1/2 grid h-8 w-8 -translate-y-1/2 place-items-center rounded-full shadow-md transition-[left,background-color] motion-reduce:transition-none ${
                    nordVpnStatus?.enabled
                      ? 'left-[32px] bg-brand text-slate-950'
                      : 'left-1 bg-fg-subtle text-fg-muted'
                  }`}
                >
                  {nordVpnSaving
                    ? <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
                    : <span className="text-[9px] font-black">{nordVpnStatus?.enabled ? 'ON' : 'OFF'}</span>}
                </span>
              </button>
            </div>

            {nordVpnStatus && (
              <div className="mt-3 flex flex-wrap gap-2 text-[11px] font-semibold">
                <span className={`rounded-full border px-2.5 py-1 ${
                  nordVpnStatus.package_available
                    ? 'border-emerald-400/25 bg-emerald-400/[0.08] text-emerald-200'
                    : 'border-rose-400/25 bg-rose-400/[0.08] text-rose-200'
                }`}>
                  {nordVpnStatus.package_available ? 'XPI sẵn sàng' : 'Thiếu XPI'}
                </span>
                <span className={`rounded-full border px-2.5 py-1 ${
                  nordVpnStatus.authenticated_state_available
                    ? 'border-emerald-400/25 bg-emerald-400/[0.08] text-emerald-200'
                    : 'border-amber-400/25 bg-amber-400/[0.08] text-amber-100'
                }`}>
                  {nordVpnStatus.authenticated_state_available ? 'Đã lưu đăng nhập' : 'Chưa có phiên đăng nhập'}
                </span>
              </div>
            )}

            <p className="mt-3 text-[11px] leading-4 text-fg-subtle">
              Thay đổi chỉ áp dụng cho browser mở sau; browser đang chạy không bị ngắt.
            </p>
            {nordVpnMessage && (
              <p aria-live="polite" className="mt-3 rounded-lg border border-emerald-400/25 bg-emerald-400/[0.08] px-3 py-2 text-xs text-emerald-200">
                {nordVpnMessage}
              </p>
            )}
            {nordVpnError && (
              <p role="alert" className="mt-3 rounded-lg border border-rose-400/25 bg-rose-400/[0.08] px-3 py-2 text-xs text-rose-200">
                {nordVpnError}
              </p>
            )}
          </section>

          <div className="flex items-center gap-2 pt-1">
            <KeyRound className="h-4 w-4 text-brand" aria-hidden="true" />
            <h3 className="text-sm font-bold text-fg">OmoCaptcha</h3>
          </div>

          <div className={`flex items-center gap-3 rounded-xl border px-4 py-3 ${
            configured
              ? 'border-emerald-400/25 bg-emerald-400/[0.08]'
              : 'border-amber-400/25 bg-amber-400/[0.08]'
          }`}>
            {configured ? (
              <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-300" aria-hidden="true" />
            ) : (
              <AlertTriangle className="h-5 w-5 shrink-0 text-amber-300" aria-hidden="true" />
            )}
            <div>
              <p className="text-sm font-semibold text-fg">
                {configured === null
                  ? 'Đang kiểm tra cấu hình…'
                  : configured
                    ? 'Đã có API key trong Windows Credential Manager'
                    : 'Chưa cấu hình API key'}
              </p>
              <p className="mt-0.5 text-[11px] leading-4 text-fg-muted">
                Ứng dụng chỉ hiển thị trạng thái; API key hiện tại không bao giờ được đọc trả về giao diện.
              </p>
            </div>
          </div>

          {desktopRuntime ? (
            <form className="space-y-4" onSubmit={save}>
              <label className="block">
                <span className="mb-2 block text-xs font-semibold text-fg">API key mới</span>
                <span className="relative block">
                  <input
                    ref={inputRef}
                    type={showKey ? 'text' : 'password'}
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                    autoComplete="new-password"
                    spellCheck={false}
                    disabled={saving}
                    placeholder="Dán API key OmoCaptcha"
                    className="h-12 w-full rounded-xl border border-line bg-surface px-4 pr-12 text-sm text-fg outline-none transition-colors placeholder:text-fg-subtle focus:border-brand focus:ring-2 focus:ring-brand/20 disabled:opacity-50"
                  />
                  <button
                    type="button"
                    onClick={() => setShowKey((current) => !current)}
                    disabled={saving}
                    aria-label={showKey ? 'Ẩn API key' : 'Hiện API key'}
                    className="absolute inset-y-0 right-0 grid w-12 cursor-pointer place-items-center text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/60"
                  >
                    {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </span>
              </label>

              <div className="flex gap-3 rounded-xl border border-sky-400/20 bg-sky-400/[0.06] px-4 py-3 text-xs leading-5 text-fg-muted">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-sky-300" aria-hidden="true" />
                Key được Windows bảo vệ và được nạp vào storage của extension khi tạo profile mới.
                Ứng dụng sẽ khởi động lại; hãy đóng hoặc chờ hoàn tất mọi task trước khi lưu.
              </div>

              {error && (
                <p role="alert" className="rounded-xl border border-rose-400/25 bg-rose-400/[0.08] px-4 py-3 text-xs leading-5 text-rose-200">
                  {error}
                </p>
              )}

              <div className="flex justify-end gap-2 border-t border-line-soft pt-4">
                <button type="button" onClick={onClose} disabled={saving} className="btn btn-ghost">
                  Hủy
                </button>
                <button
                  type="submit"
                  disabled={saving || configured === null || apiKey.trim().length < 8}
                  className="btn bg-brand text-slate-950 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saving ? (
                    <><LoaderCircle className="h-4 w-4 animate-spin" /> Đang lưu…</>
                  ) : (
                    <><ShieldCheck className="h-4 w-4" /> Lưu và khởi động lại</>
                  )}
                </button>
              </div>
            </form>
          ) : (
            <div className="rounded-xl border border-amber-400/25 bg-amber-400/[0.08] px-4 py-3 text-xs leading-5 text-amber-100">
              Chế độ web/dev không có Windows Credential Manager. Thêm
              <code className="mx-1 rounded bg-black/25 px-1.5 py-0.5">OMOCAPTCHA_KEY=...</code>
              vào <code className="rounded bg-black/25 px-1.5 py-0.5">backend/.env</code>, rồi khởi động lại backend.
            </div>
          )}
        </div>
      </section>
    </div>,
    document.body,
  );
}
