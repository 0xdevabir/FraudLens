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
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[1.75rem] leading-tight font-bold tracking-tight text-fg">{title}</h1>
        {sub && <p className="mt-1.5 max-w-3xl text-[0.9375rem] text-fg-3">{sub}</p>}
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
    <section className={cx("overflow-hidden rounded-2xl border border-line bg-card", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-[0.9375rem] font-semibold tracking-tight text-fg">{title}</h2>
            {hint && <p className="mt-0.5 text-xs text-fg-3">{hint}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

const STAT_TONE = {
  plain: "text-fg",
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
} as const;

export function Stat({
  label, value, sub, tone = "plain",
}: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: keyof typeof STAT_TONE;
}) {
  return (
    <div className="rounded-2xl border border-line bg-card px-4 py-3.5">
      <div className="text-xs font-medium text-fg-3">{label}</div>
      <div className={cx("mt-1 text-[1.7rem] leading-tight font-semibold tabular-nums tracking-tight", STAT_TONE[tone])}>{value}</div>
      {sub && <div className="mt-1 text-xs text-fg-3">{sub}</div>}
    </div>
  );
}

const TONES = {
  slate: "bg-white/8 text-fg-2",
  green: "bg-good/15 text-good",
  amber: "bg-warn/15 text-warn",
  orange: "bg-alert/15 text-alert",
  red: "bg-bad/15 text-bad",
  blue: "bg-info/15 text-info",
  violet: "bg-rule/15 text-rule",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "slate", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cx("inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium", TONES[tone])}
    >
      {children}
    </span>
  );
}

const TIER_TONE: Record<Tier, Tone> = { allow: "green", warn: "amber", step_up: "orange", hold: "red" };

export function TierBadge({ tier }: { tier: Tier | null | undefined }) {
  if (!tier) return <span className="text-fg-4">–</span>;
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
  if (!status) return <span className="text-fg-4">–</span>;
  return <Badge tone={STATUS_TONE[status] ?? "slate"}>{words(status)}</Badge>;
}

const BUTTON = {
  primary: "bg-accent font-semibold text-accent-ink hover:brightness-110",
  danger: "bg-bad font-semibold text-accent-ink hover:brightness-110",
  good: "bg-good font-semibold text-accent-ink hover:brightness-110",
  ghost: "bg-white/8 font-medium text-fg hover:bg-white/14",
} as const;

export function Button({
  variant = "ghost", small, className, ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof BUTTON; small?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-full whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100",
        small ? "px-3 py-1 text-xs" : "px-4 py-2 text-sm",
        BUTTON[variant],
        className,
      )}
    />
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="animate-pulse px-4 py-8 text-center text-sm text-fg-4" role="status">{label}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-4 py-8 text-center text-sm text-fg-3">{children}</div>;
}

export function ErrorNote({ error, retry }: { error: Error | string; retry?: () => void }) {
  const message = typeof error === "string" ? error : error.message;
  const denied = error instanceof ApiError && error.status === 403;
  return (
    <div role="alert" className="flex items-start justify-between gap-3 rounded-xl border border-bad/25 bg-bad/10 px-3 py-2 text-sm text-bad">
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

/** A segmented control: the thumb slides to whichever segment is selected. */
export function Tabs<T extends string>({
  tabs, value, onChange,
}: {
  tabs: { id: T; label: ReactNode }[]; value: T; onChange: (id: T) => void;
}) {
  const list = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ left: number; top: number; width: number; height: number } | null>(null);
  useEffect(() => {
    const element = list.current;
    if (!element) return;
    // The observer reports once as soon as it starts, and again whenever the segments reflow.
    const observer = new ResizeObserver(() => {
      const on = element.querySelector<HTMLElement>('[aria-selected="true"]');
      if (on) setThumb({ left: on.offsetLeft, top: on.offsetTop, width: on.offsetWidth, height: on.offsetHeight });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [value, tabs.length]);

  return (
    <div ref={list} role="tablist" className="relative mb-5 inline-flex max-w-full flex-wrap gap-1 rounded-[0.875rem] bg-white/6 p-1">
      {thumb && (
        <span
          aria-hidden="true"
          className="absolute rounded-[0.625rem] bg-white/14 shadow-sm shadow-black/30 transition-all duration-400 ease-ios"
          style={thumb}
        />
      )}
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          type="button"
          aria-selected={tab.id === value}
          onClick={() => onChange(tab.id)}
          className={cx(
            "relative rounded-[0.625rem] px-3.5 py-1.5 text-sm font-medium",
            tab.id === value ? "text-fg" : "text-fg-3 hover:text-fg",
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
        "rounded-full px-3 py-1 text-xs font-medium",
        on ? "bg-accent text-accent-ink" : "bg-white/8 text-fg-2 hover:bg-white/14",
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
          <tr className="border-b border-line text-xs font-medium text-fg-3">
            {head.map((label, i) => <th key={i} className="whitespace-nowrap px-4 py-2.5 font-medium">{label}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, right, className, title }: { children?: ReactNode; right?: boolean; className?: string; title?: string }) {
  return <td title={title} className={cx("px-4 py-2.5 align-middle", right && "text-right tabular-nums", className)}>{children}</td>;
}

/** Label and value pairs, for profiles. */
export function Facts({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
      {rows.map(([label, value], i) => (
        <div key={i} className="contents">
          <dt className="text-fg-3">{label}</dt>
          <dd className="min-w-0 text-fg">{value}</dd>
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
      className="sheet"
    >
      <div className="px-5 pt-5 text-lg font-semibold tracking-tight text-fg">{title}</div>
      <div className="p-5 pt-3">{children}</div>
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
        {intro && <div className="text-sm text-fg-2">{intro}</div>}
        <label htmlFor={id} className="block text-xs font-medium text-fg-2">{label}</label>
        <textarea
          id={id}
          autoFocus
          rows={4}
          maxLength={2000}
          value={text}
          onChange={(event) => setText(event.target.value)}
          className={`${inputClass} w-full`}
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
  "rounded-xl border border-transparent bg-white/8 px-3 py-2 text-sm text-fg placeholder:text-fg-4 hover:bg-white/10 focus:border-accent/60 focus:bg-white/10 focus:ring-4 focus:ring-accent/15 focus:outline-none";
