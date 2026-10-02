"use client";

import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { ApiError, type Loaded } from "@/lib/api";
import { TIER_LABEL, words } from "@/lib/format";
import type { Tier } from "@/lib/types";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function PageHeader({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {sub && <p className="mt-1 max-w-3xl text-sm text-slate-500">{sub}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({
  title, hint, actions, children, className, flush,
}: {
  title?: ReactNode; hint?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; flush?: boolean;
}) {
  return (
    <section className={cx("rounded-lg border border-slate-200 bg-white shadow-xs", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-100 px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
            {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

const STAT_TONE = {
  plain: "text-slate-900",
  good: "text-emerald-700",
  warn: "text-amber-700",
  bad: "text-red-700",
} as const;

export function Stat({
  label, value, sub, tone = "plain",
}: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: keyof typeof STAT_TONE;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-xs">
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className={cx("mt-1 text-2xl font-semibold tabular-nums tracking-tight", STAT_TONE[tone])}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

const TONES = {
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
  green: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  orange: "bg-orange-50 text-orange-800 ring-orange-200",
  red: "bg-red-50 text-red-800 ring-red-200",
  blue: "bg-sky-50 text-sky-800 ring-sky-200",
  violet: "bg-violet-50 text-violet-800 ring-violet-200",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "slate", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cx("inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset", TONES[tone])}
    >
      {children}
    </span>
  );
}

const TIER_TONE: Record<Tier, Tone> = { allow: "green", warn: "amber", step_up: "orange", hold: "red" };

export function TierBadge({ tier }: { tier: Tier | null | undefined }) {
  if (!tier) return <span className="text-slate-400">–</span>;
  return <Badge tone={TIER_TONE[tier] ?? "slate"}>{TIER_LABEL[tier] ?? tier}</Badge>;
}

const STATUS_TONE: Record<string, Tone> = {
  completed: "slate", pending_customer: "amber", held: "red", cancelled: "green", blocked: "green", rejected: "blue",
  open: "amber", in_review: "blue", escalated: "violet", closed: "slate",
  pending: "amber", approved: "red", confirmed_fraud: "red", legitimate: "green", inconclusive: "slate",
  stable: "green", watch: "amber", shifted: "red", frozen: "blue", active: "slate",
  fired: "red", not_fired: "slate", not_applicable: "slate",
};

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-slate-400">–</span>;
  return <Badge tone={STATUS_TONE[status] ?? "slate"}>{words(status)}</Badge>;
}

const BUTTON = {
  primary: "bg-slate-900 text-white hover:bg-slate-700 disabled:bg-slate-300",
  danger: "bg-red-600 text-white hover:bg-red-500 disabled:bg-red-200",
  good: "bg-emerald-600 text-white hover:bg-emerald-500 disabled:bg-emerald-200",
  ghost: "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:text-slate-300",
} as const;

export function Button({
  variant = "ghost", small, className, ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof BUTTON; small?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500 disabled:cursor-not-allowed",
        small ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm",
        BUTTON[variant],
        className,
      )}
    />
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="px-4 py-8 text-center text-sm text-slate-400" role="status">{label}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-4 py-8 text-center text-sm text-slate-500">{children}</div>;
}

export function ErrorNote({ error, retry }: { error: Error | string; retry?: () => void }) {
  const message = typeof error === "string" ? error : error.message;
  const denied = error instanceof ApiError && error.status === 403;
  return (
    <div role="alert" className="flex items-start justify-between gap-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      <span>{denied ? `Your role is not allowed to do this. ${message}` : message}</span>
      {retry && <Button small onClick={retry}>Retry</Button>}
    </div>
  );
}

/** Loading, failed, or loaded: one place decides which of the three to draw. */
export function Async<T>({ state, children }: { state: Loaded<T>; children: (data: T) => ReactNode }) {
  if (state.data !== undefined) {
    return (
      <>
        {state.error && <div className="mb-3"><ErrorNote error={state.error} retry={state.reload} /></div>}
        {children(state.data)}
      </>
    );
  }
  if (state.error) return <ErrorNote error={state.error} retry={state.reload} />;
  return <Loading />;
}

export function Tabs<T extends string>({
  tabs, value, onChange,
}: {
  tabs: { id: T; label: ReactNode }[]; value: T; onChange: (id: T) => void;
}) {
  return (
    <div role="tablist" className="mb-4 flex flex-wrap gap-1 border-b border-slate-200">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          type="button"
          aria-selected={tab.id === value}
          onClick={() => onChange(tab.id)}
          className={cx(
            "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
            tab.id === value ? "border-slate-900 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/** A filter chip that toggles. */
export function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cx(
        "rounded-full border px-2.5 py-1 text-xs font-medium",
        on ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50",
      )}
    >
      {children}
    </button>
  );
}

export function Table({ head, children, className }: { head: ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <div className={cx("overflow-x-auto", className)}>
      <table className="w-full min-w-max text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs font-medium text-slate-500">
            {head.map((label, i) => <th key={i} className="whitespace-nowrap px-3 py-2 font-medium">{label}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, right, className, title }: { children?: ReactNode; right?: boolean; className?: string; title?: string }) {
  return <td title={title} className={cx("px-3 py-2 align-middle", right && "text-right tabular-nums", className)}>{children}</td>;
}

/** Label and value pairs, for profiles. */
export function Facts({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
      {rows.map(([label, value], i) => (
        <div key={i} className="contents">
          <dt className="text-slate-500">{label}</dt>
          <dd className="min-w-0 text-slate-900">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(event) => event.target === ref.current && onClose()}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-lg border border-slate-200 bg-white p-0 shadow-xl backdrop:bg-slate-900/40"
    >
      <div className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-900">{title}</div>
      <div className="p-4">{children}</div>
    </dialog>
  );
}

/**
 * Every decision a person takes here needs a written reason, which lands in the audit log.
 * The server enforces the length; the dialog only saves a round trip.
 */
export function ReasonDialog({
  title, label = "Reason", intro, confirm, variant = "primary", minLength = 10, onSubmit, onClose,
}: {
  title: string; label?: string; intro?: ReactNode; confirm: string; variant?: "primary" | "danger" | "good";
  minLength?: number; onSubmit: (reason: string) => Promise<unknown>; onClose: () => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const short = text.trim().length < minLength;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (short || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(text.trim());
      onClose();
    } catch (problem) {
      setError(problem as Error);
      setBusy(false);
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        {intro && <div className="text-sm text-slate-600">{intro}</div>}
        <label htmlFor={id} className="block text-xs font-medium text-slate-600">{label}</label>
        <textarea
          id={id}
          autoFocus
          rows={4}
          maxLength={2000}
          value={text}
          onChange={(event) => setText(event.target.value)}
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-sky-500 focus:outline-none"
          placeholder={`At least ${minLength} characters. This is recorded in the audit log.`}
        />
        {error && <ErrorNote error={error} />}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant={variant} disabled={short || busy}>{busy ? "Working…" : confirm}</Button>
        </div>
      </form>
    </Modal>
  );
}

export const inputClass =
  "rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 focus:border-sky-500 focus:outline-none";
