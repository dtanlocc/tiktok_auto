import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { check } from '@tauri-apps/plugin-updater';
import { relaunch } from '@tauri-apps/plugin-process';
import { isTauriRuntime } from '../services/secureTransport';

type SecurityStatus = {
  configured: boolean;
  activated: boolean;
  backendInstalled: boolean;
  backendRunning: boolean;
  captchaConfigured: boolean;
  deviceId: string;
  leaseExpiresAt?: number | null;
  message: string;
};

type Phase = 'loading' | 'activation' | 'provider' | 'ready' | 'error';

const FRIENDS_BUILD = import.meta.env.VITE_TKAUTO_FRIEND_BUILD === '1';

const checkDesktopUpdate = async (): Promise<void> => {
  if (FRIENDS_BUILD) return;
  try {
    const update = await check();
    if (!update) return;
    const accepted = window.confirm(
      `Có bản TikTok Auto ${update.version}. Cài bản cập nhật đã ký ngay bây giờ?`,
    );
    if (!accepted) return;
    await update.downloadAndInstall();
    await relaunch();
  } catch (error) {
    console.info('Desktop updater chưa được cấu hình cho kênh build này.', error);
  }
};

export function SecureBootstrap({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>(isTauriRuntime() ? 'loading' : 'ready');
  const [status, setStatus] = useState<SecurityStatus | null>(null);
  const [licenseKey, setLicenseKey] = useState('');
  const [captchaKey, setCaptchaKey] = useState('');
  const [error, setError] = useState('');

  const startLicensedRuntime = useCallback(async () => {
    try {
      await invoke<SecurityStatus>('renew_license');
    } catch {
      // A still-valid offline lease remains usable; status below decides.
    }
    const current = await invoke<SecurityStatus>('security_status');
    setStatus(current);
    if (!current.configured) throw new Error(current.message);
    if (!current.activated) {
      setPhase('activation');
      return;
    }
    if (!current.captchaConfigured) {
      setPhase('provider');
      return;
    }
    await invoke('check_backend_update');
    await invoke('launch_backend');
    setPhase('ready');
  }, []);

  useEffect(() => {
    if (!isTauriRuntime()) return;
    checkDesktopUpdate().then(startLicensedRuntime).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : String(reason));
      setPhase('error');
    });
  }, [startLicensedRuntime]);

  useEffect(() => {
    if (!isTauriRuntime() || phase !== 'ready') return;
    const timer = window.setInterval(() => {
      void invoke<SecurityStatus>('renew_license')
        .catch(() => invoke<SecurityStatus>('security_status'))
        .then((current) => {
          setStatus(current);
          if (!current.activated) {
            setError('License đã hết hạn hoặc bị thu hồi; backend đã được dừng.');
            setPhase('activation');
          }
        });
    }, 15 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  const activate = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setPhase('loading');
    try {
      await invoke('activate_license', { licenseKey: licenseKey.trim() });
      setLicenseKey('');
      await startLicensedRuntime();
    } catch (reason) {
      setLicenseKey('');
      setError(reason instanceof Error ? reason.message : String(reason));
      setPhase('activation');
    }
  };

  const configureCaptcha = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setPhase('loading');
    try {
      await invoke('set_captcha_api_key', { apiKey: captchaKey.trim() });
      setCaptchaKey('');
      await startLicensedRuntime();
    } catch (reason) {
      setCaptchaKey('');
      setError(reason instanceof Error ? reason.message : String(reason));
      setPhase('provider');
    }
  };

  if (phase === 'ready') return children;

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 grid place-items-center p-6">
      <section className="w-full max-w-lg rounded-2xl border border-slate-700 bg-slate-900 p-8 shadow-2xl">
        <p className="text-xs uppercase tracking-[0.24em] text-cyan-400">
          {FRIENDS_BUILD ? 'TikTok Auto Friends' : 'TikTok Auto Security'}
        </p>
        <h1 className="mt-3 text-2xl font-semibold">
          {FRIENDS_BUILD ? 'Bản chia sẻ không cần license' : 'Kích hoạt ứng dụng'}
        </h1>
        {phase === 'loading' && (
          <p className="mt-4 text-slate-300">
            {FRIENDS_BUILD ? 'Đang xác minh bộ chương trình…' : 'Đang xác minh license và binary ký số…'}
          </p>
        )}
        {phase === 'activation' && (
          <form className="mt-6 space-y-4" onSubmit={activate}>
            <p className="text-sm text-slate-400">
              Key chỉ được gửi một lần để kích hoạt thiết bị. Các lần sau dùng lease ngắn hạn và khóa thiết bị trong Windows.
            </p>
            <input
              autoComplete="off"
              className="w-full rounded-lg border border-slate-600 bg-slate-950 px-4 py-3 outline-none focus:border-cyan-400"
              onChange={(event) => setLicenseKey(event.target.value)}
              placeholder="TKAUTO-XXXX-XXXX-…"
              type="password"
              value={licenseKey}
            />
            <button
              className="w-full rounded-lg bg-cyan-500 px-4 py-3 font-semibold text-slate-950 hover:bg-cyan-400"
              type="submit"
            >
              Kích hoạt và tải bản chính thức
            </button>
          </form>
        )}
        {phase === 'provider' && (
          <form className="mt-6 space-y-4" onSubmit={configureCaptcha}>
            <p className="text-sm text-slate-400">
              {FRIENDS_BUILD
                ? 'Nhập API key OmoCaptcha của bạn. Key được lưu trong Windows Credential Manager và chỉ chuyển cho backend qua secure bootstrap.'
                : 'Nhập API key CAPTCHA thuộc tài khoản của khách hàng. Key được lưu trong Windows Credential Manager và chỉ chuyển cho backend qua secure bootstrap; bản phát hành không chứa khóa CAPTCHA dùng chung của nhà cung cấp phần mềm.'}
            </p>
            <input
              autoComplete="off"
              className="w-full rounded-lg border border-slate-600 bg-slate-950 px-4 py-3 outline-none focus:border-cyan-400"
              onChange={(event) => setCaptchaKey(event.target.value)}
              placeholder={FRIENDS_BUILD ? 'API key OmoCaptcha của bạn' : 'API key CAPTCHA của khách hàng'}
              type="password"
              value={captchaKey}
            />
            <button
              className="w-full rounded-lg bg-cyan-500 px-4 py-3 font-semibold text-slate-950 hover:bg-cyan-400"
              type="submit"
            >
              Lưu an toàn và khởi chạy
            </button>
          </form>
        )}
        {phase === 'error' && (
          <button
            className="mt-5 rounded-lg bg-cyan-500 px-4 py-2 font-semibold text-slate-950"
            onClick={() => void startLicensedRuntime().catch((reason: unknown) => setError(String(reason)))}
            type="button"
          >
            Thử lại
          </button>
        )}
        {status && !FRIENDS_BUILD && (
          <p className="mt-5 break-all text-xs text-slate-500">Thiết bị: {status.deviceId}</p>
        )}
        {error && <p className="mt-4 rounded-lg bg-red-950/60 p-3 text-sm text-red-200">{error}</p>}
      </section>
    </main>
  );
}
