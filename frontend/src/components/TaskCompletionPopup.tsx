import { useEffect } from 'react';
import { AlertTriangle, CheckCircle2, CircleX, X } from 'lucide-react';

export type TaskCompletionTone = 'success' | 'warning' | 'error';

export interface TaskCompletionNotice {
  id: string;
  tone: TaskCompletionTone;
  title: string;
  message: string;
  stats: Array<{ label: string; value: number }>;
}

interface TaskCompletionPopupProps {
  notices: TaskCompletionNotice[];
  onDismiss: (id: string) => void;
}

const toneStyles: Record<TaskCompletionTone, {
  icon: typeof CheckCircle2;
  iconClass: string;
  borderClass: string;
  badgeClass: string;
  progressClass: string;
  label: string;
}> = {
  success: {
    icon: CheckCircle2,
    iconClass: 'text-emerald-300',
    borderClass: 'border-emerald-400/35',
    badgeClass: 'bg-emerald-400/10 text-emerald-200 border-emerald-400/25',
    progressClass: 'bg-emerald-300',
    label: 'Hoàn tất',
  },
  warning: {
    icon: AlertTriangle,
    iconClass: 'text-amber-300',
    borderClass: 'border-amber-400/35',
    badgeClass: 'bg-amber-400/10 text-amber-200 border-amber-400/25',
    progressClass: 'bg-amber-300',
    label: 'Hoàn tất, cần xem lại',
  },
  error: {
    icon: CircleX,
    iconClass: 'text-rose-300',
    borderClass: 'border-rose-400/35',
    badgeClass: 'bg-rose-400/10 text-rose-200 border-rose-400/25',
    progressClass: 'bg-rose-300',
    label: 'Hoàn tất với lỗi',
  },
};

function CompletionNotice({
  notice,
  onDismiss,
}: {
  notice: TaskCompletionNotice;
  onDismiss: (id: string) => void;
}) {
  useEffect(() => {
    const timer = window.setTimeout(() => onDismiss(notice.id), 8_000);
    return () => window.clearTimeout(timer);
  }, [notice.id, onDismiss]);

  const style = toneStyles[notice.tone];
  const StatusIcon = style.icon;

  return (
    <section
      role="status"
      aria-atomic="true"
      className={`pointer-events-auto overflow-hidden rounded-2xl border ${style.borderClass} bg-elevated/95 shadow-2xl shadow-black/40 backdrop-blur-xl`}
    >
      <div className="flex items-start gap-3 p-4">
        <div className="mt-0.5 grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white/[0.05]">
          <StatusIcon aria-hidden="true" className={`h-5 w-5 ${style.iconClass}`} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-bold text-fg">{notice.title}</h2>
            <span className={`rounded-md border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${style.badgeClass}`}>
              {style.label}
            </span>
          </div>
          <p className="mt-1 text-xs leading-5 text-fg-muted">{notice.message}</p>

          <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {notice.stats.map((stat) => (
              <div key={stat.label} className="rounded-lg border border-line-soft bg-black/15 px-2.5 py-2">
                <dt className="text-[10px] font-medium text-fg-subtle">{stat.label}</dt>
                <dd className="mt-0.5 text-sm font-bold tabular-nums text-fg">{stat.value}</dd>
              </div>
            ))}
          </dl>
        </div>

        <button
          type="button"
          onClick={() => onDismiss(notice.id)}
          aria-label={`Đóng thông báo: ${notice.title}`}
          className="grid h-11 w-11 shrink-0 cursor-pointer place-items-center rounded-xl text-fg-muted transition-colors duration-150 hover:bg-white/[0.06] hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>
      <div aria-hidden="true" className={`h-1 w-full origin-left animate-[task-notice-life_8s_linear_forwards] opacity-40 motion-reduce:hidden ${style.progressClass}`} />
    </section>
  );
}

export function TaskCompletionPopup({ notices, onDismiss }: TaskCompletionPopupProps) {
  return (
    <aside
      aria-label="Thông báo hoàn tất tác vụ"
      aria-live="polite"
      aria-relevant="additions"
      className="pointer-events-none fixed right-4 top-4 z-[90] flex w-[calc(100%-2rem)] max-w-md flex-col gap-3"
    >
      {notices.map((notice) => (
        <CompletionNotice key={notice.id} notice={notice} onDismiss={onDismiss} />
      ))}
    </aside>
  );
}
