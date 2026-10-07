"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import type { Role } from "@/lib/types";

import { cx } from "../ui";
import { TOUR_SEEN_KEY, type TourApi, type TourStep } from "./types";

const TourContext = createContext<TourApi | null>(null);

export function useTour(): TourApi {
  const api = useContext(TourContext);
  if (!api) throw new Error("useTour needs a TourProvider");
  return api;
}

interface Box { top: number; left: number; width: number; height: number }

function seen(): boolean {
  try {
    return localStorage.getItem(TOUR_SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

function remember(): void {
  try {
    localStorage.setItem(TOUR_SEEN_KEY, "1");
  } catch {
    /* storage can be blocked: the tour then simply offers itself again */
  }
}

/** Where the step's `data-tour` target is on screen, or null when there is none (a centred card). */
function locate(target?: string): Box | null {
  if (!target) return null;
  const el = document.querySelector<HTMLElement>(`[data-tour="${target}"]`);
  if (!el) return null;
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  const r = el.getBoundingClientRect();
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

export function TourProvider({ steps, role, children }: { steps: TourStep[]; role: Role; children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const mine = useMemo(() => steps.filter((s) => !s.roles || s.roles.includes(role)), [steps, role]);
  const [index, setIndex] = useState<number | null>(null);
  const [box, setBox] = useState<Box | null>(null);

  const stop = useCallback(() => {
    remember();
    setIndex(null);
  }, []);

  const start = useCallback(
    (fromStepId?: string) => {
      const at = fromStepId ? mine.findIndex((s) => s.id === fromStepId) : 0;
      setIndex(at < 0 ? 0 : at);
    },
    [mine],
  );

  const step = index === null ? null : (mine[index] ?? null);

  // Walk to the step's page, then find its target once the page has rendered.
  useEffect(() => {
    if (!step) return;
    if (step.route && step.route !== pathname) {
      router.push(step.route);
      return;
    }
    const timer = window.setTimeout(() => setBox(locate(step.target)), 250);
    const follow = () => setBox(locate(step.target));
    window.addEventListener("resize", follow);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("resize", follow);
    };
  }, [step, pathname, router]);

  useEffect(() => {
    if (index === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") stop();
      if (e.key === "ArrowRight") setIndex((i) => (i === null ? i : Math.min(i + 1, mine.length - 1)));
      if (e.key === "ArrowLeft") setIndex((i) => (i === null ? i : Math.max(i - 1, 0)));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, mine.length, stop]);

  const api = useMemo<TourApi>(() => ({ active: index !== null, start, stop }), [index, start, stop]);
  const last = index !== null && index >= mine.length - 1;

  return (
    <TourContext.Provider value={api}>
      {children}
      {step && (
        <div className="fixed inset-0 z-[100]" role="dialog" aria-modal="true" aria-label="Guided tour">
          {box ? (
            <div
              aria-hidden="true"
              className="pointer-events-none fixed rounded-xl ring-2 ring-accent transition-all duration-300"
              style={{
                top: box.top - 6,
                left: box.left - 6,
                width: box.width + 12,
                height: box.height + 12,
                boxShadow: "0 0 0 9999px rgba(0,0,0,0.55)",
              }}
            />
          ) : (
            <div aria-hidden="true" className="fixed inset-0 bg-black/55" />
          )}
          <div
            className={cx(
              "fixed w-[min(22rem,calc(100vw-2rem))] rounded-2xl border border-line bg-card p-4 shadow-xl",
              !box && "top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2",
            )}
            style={
              box
                ? {
                    top: Math.min(Math.max(box.top, 16), window.innerHeight - 240),
                    left: Math.min(box.left + box.width + 16, window.innerWidth - 368),
                  }
                : undefined
            }
          >
            <div className="text-xs font-medium text-fg-4">
              {step.chapter} · {(index ?? 0) + 1} of {mine.length}
            </div>
            <h2 className="mt-1 text-base font-semibold text-fg">{step.title}</h2>
            <p className="mt-1.5 text-sm text-fg-3">{step.body}</p>
            <div className="mt-4 flex items-center justify-between gap-2">
              <button type="button" onClick={stop} className="text-xs text-fg-3 hover:text-fg">
                Skip tour
              </button>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={index === 0}
                  onClick={() => setIndex((i) => Math.max((i ?? 0) - 1, 0))}
                  className="rounded-lg border border-line px-3 py-1.5 text-xs text-fg-2 disabled:opacity-40"
                >
                  Back
                </button>
                <button
                  type="button"
                  onClick={() => (last ? stop() : setIndex((i) => (i ?? 0) + 1))}
                  className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white"
                >
                  {last ? "Done" : "Next"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      <FirstVisit onStart={start} />
    </TourContext.Provider>
  );
}

/** Offers the tour once, on a first visit; closing it in any way remembers that. */
function FirstVisit({ onStart }: { onStart: () => void }) {
  useEffect(() => {
    if (!seen()) {
      const timer = window.setTimeout(onStart, 800);
      return () => window.clearTimeout(timer);
    }
  }, [onStart]);
  return null;
}

export function TourButton({ className }: { className?: string }) {
  const tour = useTour();
  return (
    <button
      type="button"
      onClick={() => (tour.active ? tour.stop() : tour.start())}
      className={cx(
        "rounded-lg border border-line px-2.5 py-1 text-xs font-medium text-fg-2 hover:bg-white/6 hover:text-fg",
        className,
      )}
    >
      {tour.active ? "End tour" : "Take the tour"}
    </button>
  );
}
