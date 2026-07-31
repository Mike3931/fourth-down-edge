import type { HTMLAttributes, TdHTMLAttributes, ReactNode } from 'react';
import clsx from 'clsx';
import type { DataFreshness, RecommendationStatus } from '@fde/shared-types';

export const cn = clsx;

/* ----------------------------------------------------------------------------
 * Layout primitives — institutional research-terminal styling.
 * Graphite background, slate panels, thin borders, restrained accents.
 * ------------------------------------------------------------------------- */

export function Card({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'rounded-md border border-edge bg-panel shadow-[0_1px_2px_rgba(0,0,0,0.35)]',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  hint,
  right,
}: {
  title: ReactNode;
  hint?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-edge px-4 py-2.5">
      <div>
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-ink-muted">{title}</h2>
        {hint ? <p className="mt-0.5 text-xs text-ink-faint">{hint}</p> : null}
      </div>
      {right ? <div className="shrink-0">{right}</div> : null}
    </div>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone = 'default',
  mono = true,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: 'default' | 'positive' | 'warning' | 'danger' | 'accent' | 'model';
  mono?: boolean;
}) {
  const toneCls = {
    default: 'text-ink',
    positive: 'text-ok',
    warning: 'text-warn',
    danger: 'text-bad',
    accent: 'text-accent',
    model: 'text-model',
  }[tone];
  return (
    <div className="min-w-0 rounded-md border border-edge bg-panel px-3 py-2.5">
      <div className="truncate text-[11px] font-medium uppercase tracking-wider text-ink-faint">{label}</div>
      <div className={cn('mt-1 text-lg leading-tight', toneCls, mono && 'font-mono tabular-nums')}>{value}</div>
      {sub ? <div className="mt-0.5 truncate text-[11px] text-ink-faint">{sub}</div> : null}
    </div>
  );
}

/* ----------------------------------------------------------------------------
 * Badges
 * ------------------------------------------------------------------------- */

const REC_STYLES: Record<RecommendationStatus, string> = {
  BET: 'bg-ok/15 text-ok border-ok/40',
  WATCH: 'bg-warn/15 text-warn border-warn/40',
  PASS: 'bg-ink-faint/10 text-ink-muted border-edge',
  'DATA INCOMPLETE': 'bg-bad/15 text-bad border-bad/40',
};

/** Symbols so status is never conveyed by color alone. */
const REC_SYMBOL: Record<RecommendationStatus, string> = {
  BET: '●',
  WATCH: '◐',
  PASS: '○',
  'DATA INCOMPLETE': '✕',
};

export function RecBadge({ status }: { status: RecommendationStatus }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded border px-1.5 py-0.5 font-mono text-[11px] font-semibold tracking-wide',
        REC_STYLES[status],
      )}
    >
      <span aria-hidden="true">{REC_SYMBOL[status]}</span>
      {status}
    </span>
  );
}

const FRESH_STYLES: Record<DataFreshness, string> = {
  CURRENT: 'bg-ok/15 text-ok border-ok/40',
  AGING: 'bg-warn/15 text-warn border-warn/40',
  STALE: 'bg-warn/25 text-warn border-warn/60',
  MISSING: 'bg-bad/15 text-bad border-bad/40',
  CONFLICTING: 'bg-bad/15 text-bad border-bad/40',
};

const FRESH_SYMBOL: Record<DataFreshness, string> = {
  CURRENT: '✓',
  AGING: '~',
  STALE: '!',
  MISSING: '✕',
  CONFLICTING: '≠',
};

export function FreshBadge({ status }: { status: DataFreshness }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 font-mono text-[11px] font-semibold',
        FRESH_STYLES[status],
      )}
    >
      <span aria-hidden="true">{FRESH_SYMBOL[status]}</span>
      {status}
    </span>
  );
}

export function Pill({
  children,
  tone = 'neutral',
  title,
}: {
  children: ReactNode;
  tone?: 'neutral' | 'ok' | 'warn' | 'bad' | 'accent' | 'model';
  title?: string;
}) {
  const cls = {
    neutral: 'border-edge text-ink-muted',
    ok: 'border-ok/40 text-ok',
    warn: 'border-warn/40 text-warn',
    bad: 'border-bad/40 text-bad',
    accent: 'border-accent/40 text-accent',
    model: 'border-model/40 text-model',
  }[tone];
  return (
    <span title={title} className={cn('inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px]', cls)}>
      {children}
    </span>
  );
}

/* ----------------------------------------------------------------------------
 * States: loading / empty / error / demo banner
 * ------------------------------------------------------------------------- */

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-edge bg-panel px-4 py-6 text-sm text-ink-muted" role="status">
      <span className="inline-block size-3 animate-spin rounded-full border-2 border-accent border-t-transparent motion-reduce:animate-none" aria-hidden="true" />
      {label}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-md border border-dashed border-edge bg-panel/50 px-4 py-8 text-center">
      <p className="text-sm font-medium text-ink-muted">{title}</p>
      {hint ? <p className="mt-1 text-xs text-ink-faint">{hint}</p> : null}
    </div>
  );
}

export function ErrorState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="rounded-md border border-bad/40 bg-bad/10 px-4 py-4" role="alert">
      <p className="text-sm font-semibold text-bad">✕ {title}</p>
      {detail ? <p className="mt-1 text-xs text-ink-muted">{detail}</p> : null}
    </div>
  );
}

export function DemoBanner({ label }: { label: string }) {
  return (
    <div
      className="rounded border border-warn/50 bg-warn/10 px-3 py-1.5 text-center font-mono text-[11px] font-semibold tracking-wider text-warn"
      role="note"
      aria-label="Demonstration data notice"
    >
      {label}
    </div>
  );
}

export function StaleBanner({ children }: { children: ReactNode }) {
  return (
    <div className="rounded border border-warn/60 bg-warn/15 px-3 py-2 text-sm text-warn" role="alert">
      ⚠ {children}
    </div>
  );
}

/* ----------------------------------------------------------------------------
 * Table + misc
 * ------------------------------------------------------------------------- */

export function Th({ className, children, ...rest }: HTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      scope="col"
      className={cn(
        'sticky top-0 z-10 whitespace-nowrap border-b border-edge bg-panel-raised px-2.5 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-ink-faint',
        className,
      )}
      {...rest}
    >
      {children}
    </th>
  );
}

export function Td({ className, children, ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={cn('whitespace-nowrap border-b border-edge/60 px-2.5 py-1.5 text-xs text-ink', className)} {...rest}>
      {children}
    </td>
  );
}

export function Mono({ className, children, ...rest }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cn('font-mono tabular-nums', className)} {...rest}>
      {children}
    </span>
  );
}

/** Accessible definition: keyboard-focusable term with tooltip + underline. */
export function Term({ term, def }: { term: string; def: string }) {
  return (
    <span
      tabIndex={0}
      title={def}
      aria-label={`${term}: ${def}`}
      className="cursor-help underline decoration-dotted decoration-ink-faint underline-offset-2 focus:outline focus:outline-2 focus:outline-accent"
    >
      {term}
    </span>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-faint">{children}</h3>;
}

export function Button({
  className,
  variant = 'default',
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'default' | 'primary' | 'danger' | 'ghost' }) {
  const cls = {
    default: 'border-edge bg-panel-raised text-ink hover:border-ink-faint',
    primary: 'border-accent/60 bg-accent/15 text-accent hover:bg-accent/25',
    danger: 'border-bad/60 bg-bad/10 text-bad hover:bg-bad/20',
    ghost: 'border-transparent bg-transparent text-ink-muted hover:text-ink',
  }[variant];
  return (
    <button
      className={cn(
        'rounded border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-40',
        cls,
        className,
      )}
      {...rest}
    />
  );
}

export function ProgressBar({ value, tone = 'accent', label }: { value: number; tone?: 'accent' | 'ok' | 'warn' | 'bad'; label: string }) {
  const clamped = Math.max(0, Math.min(1, value));
  const barCls = { accent: 'bg-accent', ok: 'bg-ok', warn: 'bg-warn', bad: 'bg-bad' }[tone];
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(clamped * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className="h-1.5 w-full overflow-hidden rounded-full bg-edge"
    >
      <div className={cn('h-full rounded-full', barCls)} style={{ width: `${clamped * 100}%` }} />
    </div>
  );
}
