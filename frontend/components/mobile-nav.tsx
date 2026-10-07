"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useRef } from "react";

import { words } from "@/lib/format";
import { current, isActive, NAV, type NavItem, slug, tabsFor } from "@/lib/nav";
import type { Me } from "@/lib/types";

import { TourButton } from "./tour/tour";
import { cx } from "./ui";

export function Glyph({ d, className }: { d: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d={d} />
    </svg>
  );
}

const MORE = "M5 12h.01M12 12h.01M19 12h.01";

/** The phone's title bar: the page's name in the middle, as an iOS navigation bar has it. */
export function TopBar({ pathname }: { pathname: string }) {
  const page = current(pathname);
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-base/75 pt-[env(safe-area-inset-top)] backdrop-blur-2xl backdrop-saturate-150 lg:hidden">
      <div className="relative flex h-11 items-center px-4">
        <span className="flex items-center gap-1.5 text-[0.8125rem] font-bold tracking-tight text-fg">
          <span aria-hidden="true" className="size-2 rounded-full bg-accent" />
          FraudLens
        </span>
        <span className="pointer-events-none absolute inset-x-24 truncate text-center text-[1.0625rem] font-semibold text-fg">{page?.short ?? ""}</span>
        <TourButton compact className="ml-auto" />
      </div>
    </header>
  );
}

/** The bottom tab bar: the role's four most-used pages and More. Stays under the tour's overlay (z-100). */
export function TabBar({ me, pathname, moreOpen, onMore }: { me: Me; pathname: string; moreOpen: boolean; onMore: () => void }) {
  const tabs = tabsFor(me.role);
  const onTab = tabs.some((tab) => isActive(pathname, tab.href));
  const item = "flex min-h-[3.125rem] flex-1 flex-col items-center justify-center gap-0.5 text-[0.625rem] font-medium tracking-wide active:scale-95";
  return (
    <nav
      aria-label="Tabs"
      data-tour="bottom-nav"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-base/80 pb-[env(safe-area-inset-bottom)] backdrop-blur-2xl backdrop-saturate-150 lg:hidden"
    >
      <div className="mx-auto flex max-w-xl px-1">
        {tabs.map((tab) => {
          const on = isActive(pathname, tab.href);
          return (
            <Link
              key={tab.href}
              href={tab.href}
              data-tour={`tab-${tab.href === "/" ? "home" : slug(tab.href)}`}
              aria-current={on ? "page" : undefined}
              className={cx(item, on ? "text-accent" : "text-fg-3")}
            >
              <Glyph d={tab.icon} className={cx("size-6", on && "[&_path]:fill-accent/20")} />
              {tab.short}
            </Link>
          );
        })}
        <button
          type="button"
          data-tour="tab-more"
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          onClick={onMore}
          className={cx(item, !onTab || moreOpen ? "text-accent" : "text-fg-3")}
        >
          <Glyph d={MORE} className="size-6 [stroke-width:3.2]" />
          More
        </button>
      </div>
    </nav>
  );
}

function Row({ item, on, onPick }: { item: NavItem; on: boolean; onPick: () => void }) {
  return (
    <li>
      <Link
        href={item.href}
        onClick={onPick}
        aria-current={on ? "page" : undefined}
        className={cx("flex min-h-11 items-center gap-3 px-3 py-2 text-[0.9375rem] active:bg-white/8", on ? "text-accent" : "text-fg")}
      >
        <span className={cx("grid size-7 shrink-0 place-items-center rounded-lg", on ? "bg-accent text-accent-ink" : "bg-accent/15 text-accent")}>
          <Glyph d={item.icon} className="size-[1.125rem]" />
        </span>
        <span className="min-w-0 flex-1 truncate">{item.label}</span>
        <Glyph d="M9.5 6l6 6-6 6" className="size-4 text-fg-4" />
      </Link>
    </li>
  );
}

/** Every page, the account and sign-out, as an iOS sheet of inset grouped lists. */
export function MoreSheet({
  me, pathname, serving, onSignOut, onClose,
}: {
  me: Me; pathname: string; serving: ReactNode; onSignOut: () => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);
  const initials = me.display_name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
  return (
    <dialog
      ref={ref}
      aria-label="More"
      onClose={onClose}
      onClick={(event) => event.target === ref.current && onClose()}
      className="sheet lg:hidden"
    >
      <div className="flex max-h-[85dvh] flex-col">
        <div aria-hidden="true" className="mx-auto mt-2 h-1.5 w-9 shrink-0 rounded-full bg-white/25" />
        <div className="flex items-center justify-between px-5 pt-3 pb-2">
          <h2 className="text-[1.375rem] font-bold tracking-tight text-fg">More</h2>
          <button type="button" onClick={onClose} className="min-h-11 px-1 text-[1.0625rem] font-semibold text-accent">Done</button>
        </div>
        <div className="overflow-y-auto overscroll-contain px-4 pb-5">
          <div className="flex items-center gap-3 rounded-xl bg-white/5 px-3 py-3">
            <span aria-hidden="true" className="grid size-11 shrink-0 place-items-center rounded-full bg-accent/20 text-[0.9375rem] font-semibold text-accent">{initials}</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-semibold text-fg">{me.display_name}</span>
              <span className="text-[0.8125rem] text-fg-3">{words(me.role)}</span>
            </span>
          </div>
          <div className="mt-3 [&>div]:mx-0">{serving}</div>
          {NAV.map((group) => {
            const items = group.items.filter((entry) => entry.roles.includes(me.role));
            if (!items.length) return null;
            return (
              <section key={group.heading} className="mt-4">
                <h3 className="px-3 pb-1.5 text-[0.8125rem] text-fg-3 uppercase">{group.heading}</h3>
                <ul className="divide-y divide-line overflow-hidden rounded-xl bg-white/5 [&>li]:pl-0">
                  {items.map((item) => <Row key={item.href} item={item} on={isActive(pathname, item.href)} onPick={onClose} />)}
                </ul>
              </section>
            );
          })}
          <button
            type="button"
            onClick={onSignOut}
            className="mt-5 min-h-11 w-full rounded-xl bg-white/5 text-[0.9375rem] font-medium text-bad active:bg-white/8"
          >
            Sign out
          </button>
        </div>
      </div>
    </dialog>
  );
}
