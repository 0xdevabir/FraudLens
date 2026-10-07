"use client";

import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { ApiError, type Loaded } from "@/lib/api";
import { TIER_LABEL, words } from "@/lib/format";
import type { NamedCategory, Tier } from "@/lib/types";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function PageHeader({ title, sub, actions, tour }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; tour?: string }) {
  return (
    <div data-tour={tour} className="mb-5 flex flex-wrap items-end justify-between gap-3 lg:mb-6">
      <div className="min-w-0">
        {/* An iOS large title on a phone. */}
        <h1 className="text-[2rem] leading-tight font-bold tracking-tight text-fg lg:text-[1.75rem]">{title}</h1>
        {sub && <p className="mt-1.5 max-w-3xl text-[0.9375rem] text-fg-3">{sub}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({
  title, hint, actions, children, className, flush, tour,
}: {
  title?: ReactNode; hint?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; flush?: boolean; tour?: string;
}) {
  return (
    <section data-tour={tour} className={cx("overflow-hidden rounded-2xl border border-line bg-card", className)}>
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
  paid: "green", unrecoverable: "orange", declined: "slate",
};

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-fg-4">–</span>;
  return <Badge tone={STATUS_TONE[status] ?? "slate"}>{words(status)}</Badge>;
}

/** The fraud categories an alert, a report or a message was put in. The tooltip says on what evidence. */
export function CategoryBadges({ categories }: { categories: NamedCategory[] | undefined }) {
  if (!categories?.length) return <span className="text-fg-4">none named</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {categories.map((category) => (
        <Badge
          key={category.id}
          tone="violet"
          title={[category.name.bn, category.basis?.length ? `from ${category.basis.map(words).join(", ")}` : ""].filter(Boolean).join(" · ")}
        >
          {category.number}. {category.name.en}
        </Badge>
      ))}
    </span>
  );
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
        // A finger needs more room than a pointer: 36 and 44 points tall on a touch screen.
        small ? "px-3 py-1 text-xs pointer-coarse:min-h-9 pointer-coarse:px-3.5 pointer-coarse:text-[0.8125rem]" : "px-4 py-2 text-sm pointer-coarse:min-h-11",
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
        "rounded-full px-3 py-1 text-xs font-medium pointer-coarse:min-h-8 pointer-coarse:px-3.5 pointer-coarse:text-[0.8125rem]",
        on ? "bg-accent text-accent-ink" : "bg-white/8 text-fg-2 hover:bg-white/14",
      )}
    >
      {children}
    </button>
  );
}

/** Give each cell of a row its column's heading, so a phone can show it beside the value. */
function labelled(rows: ReactNode, head: ReactNode[]): ReactNode {
  return Children.map(rows, (row) => {
    if (!isValidElement(row) || row.type !== "tr") return row;
    const cells = (row as ReactElement<{ children?: ReactNode }>).props.children;
    return cloneElement(row as ReactElement<{ children?: ReactNode }>, {
      // toArray drops the `false` of a cell left out with `cond && <Td/>`, as the heading row does.
      children: Children.toArray(cells).map((cell, i) =>
        isValidElement(cell) && cell.type === Td ? cloneElement(cell as ReactElement<TdProps>, { label: head[i], first: i === 0 }) : cell,
      ),
    });
  });
}

/**
 * A table on a laptop. On a phone (`stack`, the default) each row becomes an iOS list group:
 * its first cell as the title, every other cell as "heading … value". Pass `stack={false}`
 * for a grid of numbers that only reads as a grid; it then scrolls sideways instead.
 */
export function Table({ head, children, className, stack = true }: { head: ReactNode[]; children: ReactNode; className?: string; stack?: boolean }) {
  const s = stack;
  return (
    <div className={cx("overflow-x-auto overscroll-x-contain", className)}>
      <table className={cx("w-full text-left text-sm", s ? "md:min-w-max max-md:block" : "min-w-max")}>
        <thead className={cx(s && "max-md:hidden")}>
          <tr className="border-b border-line text-xs font-medium text-fg-3">
            {head.map((label, i) => <th key={i} className="whitespace-nowrap px-4 py-2.5 font-medium">{label}</th>)}
          </tr>
        </thead>
        <tbody className={cx("divide-y divide-line", s && "max-md:block [&>tr]:max-md:block [&>tr]:max-md:px-4 [&>tr]:max-md:py-3 [&>tr]:active:max-md:bg-wash")}>
          {s ? labelled(children, head) : children}
        </tbody>
      </table>
    </div>
  );
}

interface TdProps {
  children?: ReactNode;
  right?: boolean;
  className?: string;
  title?: string;
  /** Set by a stacking `Table`: the column heading, and whether this is the row's first cell. */
  label?: ReactNode;
  first?: boolean;
}

export function Td({ children, right, className, title, label, first }: TdProps) {
  const stacked = label !== undefined;
  return (
    <td
      title={title}
      className={cx(
        "px-4 py-2.5 align-middle",
        right && "text-right tabular-nums",
        className,
        stacked && "max-md:whitespace-normal max-md:px-0 max-md:text-left",
        stacked && (first
          ? "max-md:block max-md:pb-1 max-md:text-[0.9375rem] max-md:font-semibold"
          : label
            ? "max-md:grid max-md:grid-cols-[minmax(0,7rem)_minmax(0,1fr)] max-md:items-baseline max-md:gap-3 max-md:py-1"
            : "max-md:block max-md:py-1.5 max-md:empty:hidden"),
      )}
    >
      {stacked && !first && label ? (
        <>
          <span aria-hidden="true" className="text-[0.8125rem] font-normal text-fg-3 md:hidden">{label}</span>
          <div className="min-w-0 md:contents [&_.w-48]:max-md:w-auto">{children}</div>
        </>
      ) : (
        children
      )}
    </td>
  );
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
      <div aria-hidden="true" className="mx-auto mt-2 h-1.5 w-9 rounded-full bg-white/25 sm:hidden" />
      <div className="px-5 pt-5 text-lg font-semibold tracking-tight text-fg max-sm:pt-3">{title}</div>
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
  // 16px on a phone, or iOS zooms the page into the field.
  "rounded-xl border border-transparent bg-white/8 px-3 py-2 text-base sm:text-sm text-fg placeholder:text-fg-4 hover:bg-white/10 focus:border-accent/60 focus:bg-white/10 focus:ring-4 focus:ring-accent/15 focus:outline-none";
