import type { ReactNode } from "react";

/** Building blocks for pages that are mostly read, laid out like a written document. */

export function DocSection({
  id, number, title, sub, aside, intro, children, tour,
}: {
  id?: string; number?: number; title: ReactNode; sub?: ReactNode; aside?: ReactNode; intro?: ReactNode; children?: ReactNode; tour?: string;
}) {
  return (
    <section id={id} data-tour={tour} className="scroll-mt-20">
      <header className="mb-3 flex flex-wrap items-end justify-between gap-2 border-b border-line pb-2">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold tracking-tight text-fg">
            {number !== undefined && <span className="mr-2 tabular-nums text-fg-4">{number}.</span>}
            {title}
          </h2>
          {sub && <p className="mt-0.5 text-sm text-fg-3">{sub}</p>}
        </div>
        {aside && <div className="flex flex-wrap items-center gap-2">{aside}</div>}
      </header>
      {intro && <div className="mb-4 max-w-3xl space-y-2 text-[0.9375rem] leading-7 text-fg-2">{intro}</div>}
      {children}
    </section>
  );
}

export function H3({ children }: { children: ReactNode }) {
  return <h3 className="mt-5 mb-1.5 text-sm font-semibold text-fg first:mt-0">{children}</h3>;
}

export function P({ children }: { children: ReactNode }) {
  return <p className="max-w-3xl text-[0.9375rem] leading-7 text-fg-2">{children}</p>;
}

export function Bullets({ items }: { items: ReactNode[] }) {
  return (
    <ul className="max-w-3xl list-disc space-y-1 pl-5 text-[0.9375rem] leading-7 text-fg-2 marker:text-fg-4">
      {items.map((item, i) => <li key={i}>{item}</li>)}
    </ul>
  );
}

/** The few sentences a reader should leave with, above the detail. */
export function KeyPoints({ title = "In short", points }: { title?: string; points: ReactNode[] }) {
  return (
    <aside className="rounded-2xl border border-accent/25 bg-accent/8 px-5 py-4">
      <h2 className="text-xs font-semibold tracking-wide text-accent uppercase">{title}</h2>
      <ul className="mt-2 max-w-3xl list-disc space-y-1.5 pl-5 text-[0.9375rem] leading-7 text-fg-2 marker:text-accent">
        {points.map((point, i) => <li key={i}>{point}</li>)}
      </ul>
    </aside>
  );
}

/** A footnote under a table inside a flush `Card`. */
export function Note({ children }: { children: ReactNode }) {
  return <p className="border-t border-line px-4 py-3 text-[0.8125rem] leading-6 text-fg-3">{children}</p>;
}

/** Detail most readers can skip, folded away until asked for. */
export function Details({ label = "Technical detail", children }: { label?: ReactNode; children: ReactNode }) {
  return (
    <details className="group mt-1.5 text-xs text-fg-3">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-fg-4 select-none hover:text-fg-3 [&::-webkit-details-marker]:hidden">
        <span aria-hidden="true" className="inline-block transition-transform group-open:rotate-90">›</span>
        {label}
      </summary>
      <div className="mt-1.5 pl-3">{children}</div>
    </details>
  );
}

/** A text shown to customers, in both languages, quoted exactly. */
export function Quote({ en, bn, label }: { en?: string; bn?: string; label?: ReactNode }) {
  return (
    <figure className="rounded-xl border-l-2 border-accent/60 bg-white/4 px-4 py-3">
      {label && <figcaption className="mb-2 text-xs font-medium text-fg-3">{label}</figcaption>}
      <div className="grid gap-3 md:grid-cols-2">
        {bn && (
          <div>
            <div className="text-[0.6875rem] font-medium tracking-wide text-fg-4 uppercase">বাংলা</div>
            <p lang="bn" className="mt-0.5 text-[0.9375rem] leading-7 text-fg">{bn}</p>
          </div>
        )}
        {en && (
          <div>
            <div className="text-[0.6875rem] font-medium tracking-wide text-fg-4 uppercase">English</div>
            <p className="mt-0.5 text-[0.9375rem] leading-7 text-fg-2">{en}</p>
          </div>
        )}
      </div>
    </figure>
  );
}

/** A numbered list of links to the sections below. */
export function Contents({ items }: { items: { id: string; label: ReactNode }[] }) {
  return (
    <nav aria-label="On this page" className="rounded-2xl border border-line bg-card px-5 py-4">
      <h2 className="text-xs font-semibold tracking-wide text-fg-3 uppercase">On this page</h2>
      <ol className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {items.map((item, i) => (
          <li key={item.id}>
            <a href={`#${item.id}`} className="text-fg-2 hover:text-accent">
              <span className="mr-1.5 tabular-nums text-fg-4">{i + 1}.</span>
              {item.label}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
