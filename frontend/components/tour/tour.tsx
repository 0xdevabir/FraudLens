"use client";

/**
 * Guided product tour: a spotlight that glides between `[data-tour]` targets, an animated
 * cursor that "shows" the user what to do, a tooltip card with chapter progress, and a
 * first-visit welcome sheet. No dependencies: geometry runs in one rAF loop that writes
 * styles straight to the DOM, so tracking scroll/resize never re-renders React.
 */

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";

import { cx } from "@/components/ui";
import type { Role } from "@/lib/types";

import { TOUR_LANG_KEY, TOUR_SEEN_KEY, type TourApi, type TourLang, type TourStep } from "./types";

/* ------------------------------------------------------------------------------------------ */
/* Context                                                                                     */
/* ------------------------------------------------------------------------------------------ */

const NOOP_API: TourApi = { active: false, start: () => {}, stop: () => {} };
const TourContext = createContext<TourApi>(NOOP_API);

export function useTour(): TourApi {
  return useContext(TourContext);
}

function markSeen() {
  try {
    window.localStorage.setItem(TOUR_SEEN_KEY, new Date().toISOString());
  } catch {
    /* storage blocked: the welcome sheet may show again, which is harmless */
  }
}

function hasSeen(): boolean {
  try {
    return window.localStorage.getItem(TOUR_SEEN_KEY) !== null;
  } catch {
    return true; // can't remember a dismissal, so don't nag
  }
}

const SECONDS_PER_STEP = 9;

const BN_DIGITS = "০১২৩৪৫৬৭৮৯";
const bnNum = (n: number) => String(n).replace(/\d/g, (d) => BN_DIGITS[Number(d)]);

/** Everything the tour itself says, in plain English and everyday Bangla. */
const UI = {
  en: {
    num: (n: number) => String(n),
    next: "Next",
    back: "Back",
    skip: "Skip",
    finish: "Finish",
    play: "Play the tour by itself",
    pause: "Pause the tour",
    live: "Live",
    paused: "Paused",
    welcomeTitle: "Welcome to FraudLens",
    welcomeBody:
      "FraudLens stops scam payments on mobile money before the money is gone. Sit back and watch: a guide cursor walks through every screen and explains it in plain words.",
    watch: "Watch the live tour",
    manual: "Go step by step",
    later: "Maybe later",
    steps: (n: number) => `${n} stops`,
    minutes: (m: number) => `about ${m} min`,
    readyTitle: "You\u2019re ready",
    readyBody: (n: number) => `That\u2019s all ${n} stops. Replay it anytime with the compass button, Take the tour.`,
    restart: "Watch again",
    done: "Done",
    complete: "Complete",
  },
  bn: {
    num: bnNum,
    next: "পরের ধাপ",
    back: "আগের",
    skip: "বাদ দিন",
    finish: "শেষ",
    play: "ট্যুর নিজে থেকে চলুক",
    pause: "ট্যুর থামান",
    live: "লাইভ",
    paused: "থামানো",
    welcomeTitle: "FraudLens-এ স্বাগতম",
    welcomeBody:
      "মোবাইল মানিতে প্রতারণার পেমেন্ট টাকা চলে যাওয়ার আগেই FraudLens থামিয়ে দেয়। আরাম করে বসুন আর দেখুন: একটা কার্সর নিজে নিজে প্রতিটা স্ক্রিন ঘুরে সহজ ভাষায় বুঝিয়ে দেবে।",
    watch: "লাইভ ট্যুর দেখুন",
    manual: "ধাপে ধাপে দেখুন",
    later: "পরে দেখব",
    steps: (n: number) => `${bnNum(n)}টি ধাপ`,
    minutes: (m: number) => `প্রায় ${bnNum(m)} মিনিট`,
    readyTitle: "আপনি এখন প্রস্তুত",
    readyBody: (n: number) => `${bnNum(n)}টি ধাপই দেখা হলো। কম্পাস বোতাম “Take the tour” চেপে যেকোনো সময় আবার দেখতে পারবেন।`,
    restart: "আবার দেখুন",
    done: "ঠিক আছে",
    complete: "সম্পন্ন",
  },
} as const;

const CHAPTER_BN: Record<string, string> = {
  Welcome: "স্বাগতম",
  Navigation: "মেনু",
  Overview: "সারসংক্ষেপ",
  Operations: "দৈনন্দিন কাজ",
  Investigation: "তদন্ত",
  Governance: "নিয়ম ও তদারকি",
  "Customer demo": "গ্রাহকের দিক",
  Finish: "শেষ",
};

const chapterName = (chapter: string, lang: TourLang) => (lang === "bn" ? (CHAPTER_BN[chapter] ?? chapter) : chapter);
const titleOf = (s: TourStep, lang: TourLang) => (lang === "bn" && s.titleBn ? s.titleBn : s.title);
const bodyOf = (s: TourStep, lang: TourLang) => (lang === "bn" && s.bodyBn ? s.bodyBn : s.body);

function readLang(): TourLang {
  try {
    return window.localStorage.getItem(TOUR_LANG_KEY) === "bn" ? "bn" : "en";
  } catch {
    return "en";
  }
}

/** Reading time for a step in auto mode: typing plus a calm read, never rushed. */
function dwellFor(text: string, lang: TourLang): number {
  return Math.min(Math.max(2400 + text.length * (lang === "bn" ? 46 : 38), 5200), 11000);
}

/** A pausable one-shot timer: restarts when `key` changes, keeps its remaining time across pauses. */
function useCountdown(running: boolean, ms: number, key: number, onDone: () => void) {
  const done = useRef(onDone);
  const state = useRef({ key: -1, left: ms });
  useEffect(() => {
    done.current = onDone;
  });
  useEffect(() => {
    const s = state.current;
    if (s.key !== key) {
      s.key = key;
      s.left = ms;
    }
    if (!running) return;
    const t0 = performance.now();
    const id = window.setTimeout(() => done.current(), s.left);
    return () => {
      window.clearTimeout(id);
      s.left = Math.max(0, s.left - (performance.now() - t0));
    };
  }, [running, ms, key]);
}

function LangSwitch({ lang, onLang, className }: { lang: TourLang; onLang: (l: TourLang) => void; className?: string }) {
  return (
    <div role="group" aria-label="Language / ভাষা" className={cx("flex rounded-full bg-white/8 p-0.5 text-xs font-medium", className)}>
      {(["en", "bn"] as const).map((l) => (
        <button
          key={l}
          type="button"
          lang={l}
          aria-pressed={lang === l}
          onClick={() => onLang(l)}
          className={cx(
            "rounded-full px-2.5 py-0.5 transition-colors duration-300",
            lang === l ? "bg-accent text-accent-ink" : "text-fg-3 hover:text-fg",
          )}
        >
          {l === "en" ? "EN" : "বাংলা"}
        </button>
      ))}
    </div>
  );
}

interface RunState {
  /** Index into the role-filtered steps, or null when on the finish card / inactive. */
  index: number | null;
  finished: boolean;
  /** Plays by itself: the cursor tours each stop and moves on after a read. */
  auto: boolean;
  /** Bumps on every jump so the overlay can tell a fresh run from a stale one. */
  nonce: number;
}

export function TourProvider({ steps, role, children }: { steps: TourStep[]; role: Role; children: ReactNode }) {
  const visible = useMemo(() => steps.filter((s) => !s.roles || s.roles.includes(role)), [steps, role]);
  const [run, setRun] = useState<RunState>({ index: null, finished: false, auto: true, nonce: 0 });
  const [welcome, setWelcome] = useState(false);
  const [lang, setLangState] = useState<TourLang>(readLang);
  const setLang = useCallback((l: TourLang) => {
    setLangState(l);
    try {
      window.localStorage.setItem(TOUR_LANG_KEY, l);
    } catch {
      /* the choice just won't outlive this page */
    }
  }, []);

  const active = run.index !== null || run.finished;

  const goto = useCallback((index: number) => {
    setRun((r) => ({ ...r, index, finished: false, nonce: r.nonce + 1 }));
  }, []);

  const start = useCallback(
    (fromStepId?: string, mode: "auto" | "manual" = "auto") => {
      if (visible.length === 0) return;
      const at = fromStepId ? visible.findIndex((s) => s.id === fromStepId) : 0;
      setWelcome(false);
      markSeen();
      setRun((r) => ({ index: Math.max(0, at), finished: false, auto: mode === "auto", nonce: r.nonce + 1 }));
    },
    [visible],
  );

  const toggleAuto = useCallback(() => setRun((r) => ({ ...r, auto: !r.auto })), []);

  const stop = useCallback(() => {
    markSeen();
    setRun((r) => ({ ...r, index: null, finished: false, nonce: r.nonce + 1 }));
  }, []);

  const finish = useCallback(() => {
    markSeen();
    setRun((r) => ({ ...r, index: null, finished: true, nonce: r.nonce + 1 }));
  }, []);

  // First visit: offer the tour once the console has painted.
  useEffect(() => {
    if (visible.length === 0) return;
    const id = window.setTimeout(() => {
      if (!hasSeen()) setWelcome(true);
    }, 800);
    return () => window.clearTimeout(id);
  }, [visible.length]);

  const api = useMemo<TourApi>(() => ({ active, start, stop }), [active, start, stop]);

  return (
    <TourContext.Provider value={api}>
      {children}
      {welcome && !active && (
        <WelcomeSheet
          count={visible.length}
          lang={lang}
          onLang={setLang}
          onStart={(mode) => start(undefined, mode)}
          onLater={() => {
            markSeen();
            setWelcome(false);
          }}
        />
      )}
      {active &&
        createPortal(
          <TourOverlay
            steps={visible}
            run={run}
            onGoto={goto}
            onFinish={finish}
            onStop={stop}
            onRestart={() => start(undefined, "auto")}
            onToggleAuto={toggleAuto}
            lang={lang}
            onLang={setLang}
          />,
          document.body,
        )}
    </TourContext.Provider>
  );
}

/* ------------------------------------------------------------------------------------------ */
/* Sidebar button                                                                              */
/* ------------------------------------------------------------------------------------------ */

/** `compact` renders icon-only (e.g. the mobile top bar); the label stays for screen readers. */
export function TourButton({ className, compact }: { className?: string; compact?: boolean }) {
  const { start, active } = useTour();
  return (
    <button
      type="button"
      onClick={() => start()}
      aria-pressed={active}
      data-tour={compact ? "tour-button" : undefined}
      className={cx(
        "group flex items-center gap-2.5 rounded-xl bg-accent/10 text-left text-[0.9375rem] font-medium text-accent hover:bg-accent/18 active:scale-[0.98]",
        compact ? "p-2" : "w-full px-3 py-2",
        className,
      )}
    >
      <CompassIcon className="size-[1.125rem] shrink-0 transition-transform duration-500 ease-ios group-hover:rotate-45" />
      <span className={compact ? "sr-only" : undefined}>Take the tour</span>
    </button>
  );
}

function CompassIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <circle cx="12" cy="12" r="9.25" />
      <path d="m15.6 8.4-2.1 5.1-5.1 2.1 2.1-5.1z" fill="currentColor" fillOpacity={0.25} />
    </svg>
  );
}

function PlayIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className={className} aria-hidden="true">
      <path d="M4.5 2.9v10.2c0 .6.7 1 1.2.7l8-5.1c.5-.3.5-1.1 0-1.4l-8-5.1c-.5-.3-1.2.1-1.2.7Z" />
    </svg>
  );
}

function PauseIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className={className} aria-hidden="true">
      <rect x="3.5" y="2.5" width="3.2" height="11" rx="1" />
      <rect x="9.3" y="2.5" width="3.2" height="11" rx="1" />
    </svg>
  );
}

function SparkleIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M12 2.5c.5 4.6 2.4 6.9 7 7.5-4.6.6-6.5 2.9-7 7.5-.5-4.6-2.4-6.9-7-7.5 4.6-.6 6.5-2.9 7-7.5Z" />
      <path d="M19 15.5c.2 1.8 1 2.6 2.75 2.75-1.8.2-2.55 1-2.75 2.75-.2-1.8-1-2.55-2.75-2.75 1.8-.2 2.55-1 2.75-2.75Z" opacity={0.6} />
    </svg>
  );
}

/* ------------------------------------------------------------------------------------------ */
/* Welcome sheet                                                                               */
/* ------------------------------------------------------------------------------------------ */

function WelcomeSheet({
  count,
  lang,
  onLang,
  onStart,
  onLater,
}: {
  count: number;
  lang: TourLang;
  onLang: (l: TourLang) => void;
  onStart: (mode: "auto" | "manual") => void;
  onLater: () => void;
}) {
  const startRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const minutes = Math.max(1, Math.round((count * SECONDS_PER_STEP) / 60));
  const t = UI[lang];

  useEffect(() => {
    startRef.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onLater();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onLater]);

  return createPortal(
    <div className="tour-root fixed inset-0 z-[100] flex items-end justify-center p-3 sm:items-center">
      <TourStyles />
      <div aria-hidden="true" className="tour-fade absolute inset-0 bg-black/55 backdrop-blur-sm" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="tour-pop relative w-full max-w-md overflow-hidden rounded-3xl border border-white/10 bg-card/75 p-6 shadow-[0_30px_80px_rgb(0_0_0/0.55)] backdrop-blur-2xl backdrop-saturate-150 sm:p-7"
      >
        <div aria-hidden="true" className="pointer-events-none absolute -top-24 left-1/2 size-56 -translate-x-1/2 rounded-full bg-accent/25 blur-3xl" />
        <LangSwitch lang={lang} onLang={onLang} className="absolute top-4 right-4 z-10" />
        <div className="relative" lang={lang}>
          <div className="mx-auto mb-4 grid size-12 place-items-center rounded-2xl bg-accent/15 text-accent ring-1 ring-accent/25">
            <SparkleIcon className="tour-twinkle size-6" />
          </div>
          <h2 id={titleId} className="text-center text-[1.375rem] font-bold tracking-tight text-fg">
            {t.welcomeTitle}
          </h2>
          <p className="mx-auto mt-2 max-w-sm text-center text-[0.9375rem] leading-relaxed text-fg-3">{t.welcomeBody}</p>
          <div className="mt-4 flex items-center justify-center gap-2 text-xs text-fg-4">
            <span className="rounded-full bg-white/6 px-2.5 py-1 tabular-nums">{t.steps(count)}</span>
            <span className="rounded-full bg-white/6 px-2.5 py-1">{t.minutes(minutes)}</span>
          </div>
          <div className="mt-6 flex flex-col gap-2">
            <button
              ref={startRef}
              type="button"
              onClick={() => onStart("auto")}
              className="flex items-center justify-center gap-2 rounded-xl bg-accent px-5 py-2.5 text-[0.9375rem] font-semibold text-accent-ink shadow-[0_8px_24px_rgb(168_188_161/0.25)] hover:brightness-110"
            >
              <PlayIcon className="size-4" />
              {t.watch}
            </button>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => onStart("manual")}
                className="flex-1 rounded-xl bg-white/6 px-4 py-2.5 text-[0.9375rem] font-medium text-fg-2 hover:bg-white/10 hover:text-fg"
              >
                {t.manual}
              </button>
              <button type="button" onClick={onLater} className="flex-1 rounded-xl px-4 py-2.5 text-[0.9375rem] text-fg-3 hover:bg-white/6 hover:text-fg">
                {t.later}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ------------------------------------------------------------------------------------------ */
/* Helpers                                                                                     */
/* ------------------------------------------------------------------------------------------ */

const reducedQuery = "(prefers-reduced-motion: reduce)";
function subscribeReduced(cb: () => void) {
  const mq = window.matchMedia(reducedQuery);
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}
function useReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReduced,
    () => window.matchMedia(reducedQuery).matches,
    () => false,
  );
}

const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

/** Poll once per frame until `probe` returns something, or give up after `timeout` ms. */
function waitFor<T>(probe: () => T | null, timeout: number, cancelled: () => boolean): Promise<T | null> {
  return new Promise((resolve) => {
    const t0 = performance.now();
    const tick = () => {
      if (cancelled()) return resolve(null);
      const hit = probe();
      if (hit) return resolve(hit);
      if (performance.now() - t0 > timeout) return resolve(null);
      requestAnimationFrame(tick);
    };
    tick();
  });
}

function isShown(el: Element): boolean {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.right > 0 && r.left < window.innerWidth;
}

function findTarget(name: string): HTMLElement | null {
  const all = Array.from(document.querySelectorAll<HTMLElement>(`[data-tour="${CSS.escape(name)}"]`));
  return all.find(isShown) ?? all[0] ?? null;
}

/** A visible anchor inside the page itself, usually its header: used when a step's own target never shows up. */
function pageAnchor(): HTMLElement | null {
  return Array.from(document.querySelectorAll<HTMLElement>("main [data-tour]")).find(isShown) ?? null;
}

/** Tallest spotlight, as a share of the viewport: a long table is lit from its top instead of filling the screen. */
const maxHole = () => (window.innerWidth < 640 ? 0.34 : 0.46);
const isTall = (el: Element) => el.getBoundingClientRect().height > window.innerHeight * maxHole();

function inViewport(el: Element): boolean {
  const r = el.getBoundingClientRect();
  return r.top >= 8 && r.bottom <= window.innerHeight - 8;
}

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

type Side = "top" | "bottom" | "left" | "right";
interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

const PAD = 8; // spotlight breathing room around the target
const GAP = 16; // distance between spotlight and card
const EDGE = 12; // minimum distance from the viewport edge
const MOVE_MS = 650;

function placeCard(t: Box, w: number, h: number, vw: number, vh: number, pref: TourStep["placement"]) {
  const room: Record<Side, number> = {
    top: t.top - GAP - EDGE,
    bottom: vh - (t.top + t.height) - GAP - EDGE,
    left: t.left - GAP - EDGE,
    right: vw - (t.left + t.width) - GAP - EDGE,
  };
  const fits = (s: Side) => room[s] >= (s === "top" || s === "bottom" ? h : w);
  const opposite: Record<Side, Side> = { top: "bottom", bottom: "top", left: "right", right: "left" };

  let side: Side | null = null;
  if (pref && pref !== "auto") side = fits(pref) ? pref : fits(opposite[pref]) ? opposite[pref] : null;
  if (!side) {
    const order: Side[] = ["bottom", "top", "right", "left"];
    side = order.find(fits) ?? null;
  }

  const clampX = (x: number) => Math.min(Math.max(x, EDGE), vw - w - EDGE);
  const clampY = (y: number) => Math.min(Math.max(y, EDGE), vh - h - EDGE);
  const cx = t.left + t.width / 2;
  const cy = t.top + t.height / 2;

  if (!side) {
    // Target fills the screen: float the card over its lower part, no arrow.
    return { left: clampX(cx - w / 2), top: clampY(vh - h - EDGE * 2), side: null, arrow: 0 };
  }
  let left: number;
  let top: number;
  if (side === "bottom" || side === "top") {
    left = clampX(cx - w / 2);
    top = side === "bottom" ? t.top + t.height + GAP : t.top - GAP - h;
    top = clampY(top);
    return { left, top, side, arrow: Math.min(Math.max(cx - left, 22), w - 22) };
  }
  top = clampY(cy - h / 2);
  left = side === "right" ? t.left + t.width + GAP : t.left - GAP - w;
  left = clampX(left);
  return { left, top, side, arrow: Math.min(Math.max(cy - top, 22), h - 22) };
}

/* ------------------------------------------------------------------------------------------ */
/* Overlay                                                                                     */
/* ------------------------------------------------------------------------------------------ */

interface Ready {
  nonce: number;
  el: HTMLElement | null;
}

function TourOverlay({
  steps,
  run,
  onGoto,
  onFinish,
  onStop,
  onRestart,
  onToggleAuto,
  lang,
  onLang,
}: {
  steps: TourStep[];
  run: RunState;
  onGoto: (i: number) => void;
  onFinish: () => void;
  onStop: () => void;
  onRestart: () => void;
  onToggleAuto: () => void;
  lang: TourLang;
  onLang: (l: TourLang) => void;
}) {
  const router = useRouter();
  const reduced = useReducedMotion();
  const titleId = useId();

  const { index, finished, nonce, auto } = run;
  const t = UI[lang];
  const step = index !== null ? steps[index] : null;
  const total = steps.length;

  const [ready, setReady] = useState<Ready | null>(null);
  const [arrived, setArrived] = useState(-1); // nonce at which the cursor landed
  const isReady = finished || ready?.nonce === nonce;
  const target = isReady && !finished ? (ready?.el ?? null) : null;
  const action = step?.action;
  const showCursor = !!target && action !== "none" && !reduced;

  const rootRef = useRef<HTMLDivElement>(null);
  const holeRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const arrowRef = useRef<HTMLSpanElement>(null);
  const cursorRef = useRef<HTMLDivElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);

  /** Everything the frame loop reads, refreshed after each render. */
  const live = useRef({
    target: null as HTMLElement | null,
    /** data-tour name of the target, so a remounted element can be found again. */
    name: null as string | null,
    placement: "auto" as TourStep["placement"],
    action: undefined as TourStep["action"],
    reduced: false,
    nonce: -1,
    movingUntil: 0,
    cursor: { x: -1, y: -1, fromX: 0, fromY: 0, ctrlX: 0, ctrlY: 0, t0: 0, dur: 0, animating: false, bend: 1, arrivedAt: 0 },
  });

  useEffect(() => {
    const l = live.current;
    l.reduced = reduced;
    l.placement = step?.placement ?? "auto";
    l.action = step?.action;
    if (l.target !== target || l.nonce !== nonce) {
      l.movingUntil = performance.now() + (reduced ? 0 : MOVE_MS);
      l.nonce = nonce;
      // Start a fresh cursor glide toward the new target.
      const c = l.cursor;
      if (target) {
        if (c.x < 0) {
          c.x = window.innerWidth * 0.62;
          c.y = window.innerHeight * 0.82;
        }
        c.fromX = c.x;
        c.fromY = c.y;
        c.t0 = performance.now();
        c.bend = -c.bend;
        c.animating = true;
        c.dur = 0;
      }
    }
    l.target = target;
  });

  // Resolve the step: navigate, wait for the target, scroll it into view.
  useEffect(() => {
    if (!step) return;
    let cancelled = false;
    const isCancelled = () => cancelled;
    (async () => {
      const path = step.route?.split(/[?#]/)[0];
      let navigated = false;
      if (step.route && path && window.location.pathname !== path) {
        router.push(step.route);
        navigated = true;
        await waitFor(() => (window.location.pathname === path ? true : null), 8000, isCancelled);
        if (cancelled) return;
        await sleep(live.current.reduced ? 60 : 480); // let <main> finish its rise-in
      }
      const name = window.innerWidth < 1024 && step.mobileTarget ? step.mobileTarget : step.target;
      const probe = () => {
        const hit = name ? findTarget(name) : null;
        return hit && isShown(hit) ? hit : null;
      };
      const show = async (el: HTMLElement | null) => {
        if (el && !inViewport(el)) {
          const behavior = live.current.reduced ? "auto" : "smooth";
          if (isTall(el)) {
            // Long tables: bring their top under the chapter rail, so the lit part is the start of the table.
            window.scrollTo({ top: window.scrollY + el.getBoundingClientRect().top - 72, behavior });
          } else {
            el.scrollIntoView({ block: "center", inline: "nearest", behavior });
          }
          await sleep(live.current.reduced ? 30 : 480);
        }
        if (cancelled) return;
        live.current.name = el?.dataset.tour ?? null;
        setReady({ nonce, el });
      };
      if (!name) return show(null);

      let el = await waitFor(probe, navigated ? 1200 : 1500, isCancelled);
      if (cancelled) return;
      if (el || !step.route) return show(el); // sidebar targets hidden on a phone: centred card
      // Data-backed target still loading: light the page header now, then glide over once it lands.
      const interim = pageAnchor();
      await show(interim);
      el = await waitFor(probe, 8000, isCancelled);
      if (!cancelled && el) await show(el);
    })();
    return () => {
      cancelled = true;
    };
  }, [step, nonce, router]);

  // One frame loop positions the spotlight, card, arrow and cursor.
  useEffect(() => {
    let raf = 0;
    let lastHole = "";
    let lastCard = "";
    const frame = () => {
      raf = requestAnimationFrame(frame);
      const l = live.current;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const now = performance.now();
      const moving = now < l.movingUntil;

      // Spotlight
      let box: Box | null = null;
      if (l.target && !l.target.isConnected && l.name) {
        // React swapped the element (data refresh, re-render): follow its replacement.
        const again = findTarget(l.name);
        if (again) l.target = again;
      }
      if (l.target && l.target.isConnected) {
        const r = l.target.getBoundingClientRect();
        const left = Math.max(r.left - PAD, 4);
        const top = Math.max(r.top - PAD, 4);
        const right = Math.min(r.right + PAD, vw - 4);
        const bottom = Math.min(r.bottom + PAD, vh - 4, top + vh * maxHole());
        box = { left, top, width: Math.max(right - left, 0), height: Math.max(bottom - top, 0) };
      }
      const hole = holeRef.current;
      if (hole) {
        const b = box ?? { left: vw / 2, top: vh / 2, width: 0, height: 0 };
        const key = `${b.left}|${b.top}|${b.width}|${b.height}|${moving}`;
        if (key !== lastHole) {
          lastHole = key;
          hole.style.transition = moving
            ? "transform .65s var(--ease-ios), width .65s var(--ease-ios), height .65s var(--ease-ios), opacity .3s ease"
            : "opacity .3s ease";
          hole.style.transform = `translate(${b.left}px, ${b.top}px)`;
          hole.style.width = `${b.width}px`;
          hole.style.height = `${b.height}px`;
          hole.dataset.empty = box ? "false" : "true";
        }
      }

      // Card
      const card = cardRef.current;
      const arrow = arrowRef.current;
      if (card) {
        const w = card.offsetWidth;
        const h = card.offsetHeight;
        const p = box
          ? placeCard(box, w, h, vw, vh, l.placement)
          : { left: (vw - w) / 2, top: Math.max((vh - h) / 2, EDGE), side: null, arrow: 0 };
        const key = `${p.left}|${p.top}|${p.side}|${p.arrow}|${moving}`;
        if (key !== lastCard) {
          lastCard = key;
          card.style.transition = moving
            ? "transform .65s var(--ease-ios), opacity .25s ease, scale .4s var(--ease-ios)"
            : "opacity .25s ease, scale .4s var(--ease-ios)";
          card.style.transform = `translate(${Math.round(p.left)}px, ${Math.round(p.top)}px)`;
          if (arrow) {
            if (!p.side) arrow.style.display = "none";
            else {
              arrow.style.display = "block";
              const s = p.side;
              arrow.style.left = s === "top" || s === "bottom" ? `${p.arrow - 6}px` : s === "right" ? "-6px" : `${w - 6}px`;
              arrow.style.top = s === "left" || s === "right" ? `${p.arrow - 6}px` : s === "bottom" ? "-6px" : `${h - 6}px`;
            }
          }
        }
      }

      // Cursor: glide on a bezier toward the (possibly moving) target centre, then stick to it.
      const cur = cursorRef.current;
      const c = l.cursor;
      if (cur && box) {
        const tx = box.left + box.width / 2;
        const ty = box.top + box.height / 2;
        if (c.animating) {
          if (c.dur === 0) {
            const dist = Math.hypot(tx - c.fromX, ty - c.fromY);
            c.dur = l.reduced ? 1 : Math.min(Math.max(dist * 1.1, 520), 1150);
            const mx = (c.fromX + tx) / 2;
            const my = (c.fromY + ty) / 2;
            const nx = -(ty - c.fromY) / (dist || 1);
            const ny = (tx - c.fromX) / (dist || 1);
            const bend = Math.min(dist * 0.28, 160) * c.bend;
            c.ctrlX = mx + nx * bend;
            c.ctrlY = my + ny * bend;
          }
          const p = Math.min((now - c.t0) / c.dur, 1);
          const e = easeInOut(p);
          const u = 1 - e;
          c.x = u * u * c.fromX + 2 * u * e * c.ctrlX + e * e * tx;
          c.y = u * u * c.fromY + 2 * u * e * c.ctrlY + e * e * ty;
          if (p >= 1) {
            c.animating = false;
            c.arrivedAt = now;
            setArrived(l.nonce);
          }
        } else if (l.action === "hover" && !l.reduced && (box.width > 220 || box.height > 140)) {
          // A big panel: the cursor reads it, sweeping a slow figure-eight across it like a person would.
          const k = (now - c.arrivedAt) / 1000;
          const amp = Math.min(k / 0.9, 1);
          c.x = tx + Math.max(box.width / 2 - 40, 0) * 0.78 * amp * Math.sin(k * 0.85);
          c.y = ty + Math.max(box.height / 2 - 28, 0) * 0.6 * amp * Math.sin(k * 1.7);
        } else {
          c.x = tx;
          c.y = ty;
        }
        cur.style.transform = `translate(${c.x}px, ${c.y}px)`;
      }
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Focus: remember what had focus, move it into the card, give it back on exit.
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    return () => prev?.focus?.({ preventScroll: true });
  }, []);
  useEffect(() => {
    if (isReady) nextRef.current?.focus({ preventScroll: true });
  }, [isReady, nonce]);

  const next = useCallback(() => {
    if (index === null) return;
    if (index >= total - 1) onFinish();
    else onGoto(index + 1);
  }, [index, total, onFinish, onGoto]);
  const back = useCallback(() => {
    if (index !== null && index > 0) onGoto(index - 1);
  }, [index, onGoto]);

  const body = step ? bodyOf(step, lang) : "";
  const dwell = dwellFor(body, lang);
  const landedNow = arrived === nonce;
  const counting = auto && isReady && !finished && !!step && (landedNow || !showCursor);
  useCountdown(counting, dwell, nonce, next);

  // Keyboard: arrows/Enter step, Esc leaves, Tab stays inside the tour.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const inButton = e.target instanceof HTMLButtonElement && !!rootRef.current?.contains(e.target);
      if (e.key === "Escape") {
        e.preventDefault();
        onStop();
      } else if (e.key === "ArrowRight" && !finished) {
        e.preventDefault();
        next();
      } else if (e.key === "ArrowLeft" && !finished) {
        e.preventDefault();
        back();
      } else if (e.key === "Enter" && !inButton && !finished) {
        e.preventDefault();
        next();
      } else if (e.key === " " && !finished) {
        e.preventDefault();
        onToggleAuto();
      } else if (e.key === "Tab") {
        const focusables = Array.from(rootRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled)") ?? []);
        if (focusables.length === 0) return;
        const i = focusables.indexOf(document.activeElement as HTMLElement);
        e.preventDefault();
        const n = e.shiftKey ? (i <= 0 ? focusables.length - 1 : i - 1) : (i + 1) % focusables.length;
        focusables[n].focus();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [next, back, onStop, onToggleAuto, finished]);

  const chapters = useMemo(() => {
    const groups: { chapter: string; items: { step: TourStep; i: number }[] }[] = [];
    steps.forEach((s, i) => {
      const last = groups[groups.length - 1];
      if (last && last.chapter === s.chapter) last.items.push({ step: s, i });
      else groups.push({ chapter: s.chapter, items: [{ step: s, i }] });
    });
    return groups;
  }, [steps]);

  const landed = arrived === nonce;
  const progress = finished ? 100 : index !== null ? ((index + 1) / total) * 100 : 0;

  return (
    <div ref={rootRef} className={cx("tour-root fixed inset-0 z-[100]", reduced && "tour-reduced")}>
      <TourStyles />

      {/* Swallows every click meant for the page underneath. */}
      <div
        aria-hidden="true"
        className="absolute inset-0 cursor-default"
        onPointerDown={(e) => e.preventDefault()}
        onClick={(e) => e.stopPropagation()}
      />

      {/* Spotlight: a rounded window punched through a giant shadow. */}
      <div
        ref={holeRef}
        aria-hidden="true"
        className="tour-hole pointer-events-none fixed top-0 left-0 rounded-2xl"
        style={{ boxShadow: "0 0 0 200vmax rgb(10 10 10 / 0.66)" }}
      >
        <span className={cx("tour-ring absolute inset-0 rounded-2xl", !target && "opacity-0")} />
      </div>

      {/* The guide's cursor. */}
      <div
        ref={cursorRef}
        aria-hidden="true"
        className={cx(
          "pointer-events-none fixed top-0 left-0 transition-opacity duration-300",
          showCursor ? "opacity-100" : "opacity-0",
        )}
      >
        {landed && action === "click" && <span key={`r${nonce}`} className="tour-ripple" />}
        {landed && action === "hover" && <span key={`g${nonce}`} className="tour-glow" />}
        <div key={nonce} className={cx(landed && action === "click" && "tour-press", landed && action === "hover" && "tour-wiggle")}>
          <svg width="26" height="30" viewBox="0 0 26 30" className="drop-shadow-[0_4px_10px_rgb(0_0_0/0.55)]">
            <path
              d="M2.2 1.6 23 15.1c.9.6.6 2-.5 2.1l-8.5 1-4.2 8.3c-.5 1-2 .9-2.3-.2L1.4 2.6c-.2-.8.2-1.3.8-1Z"
              fill="#f5f5f5"
              stroke="#1b1b1b"
              strokeWidth="1.6"
              strokeLinejoin="round"
            />
            <circle cx="20" cy="25" r="3" fill="var(--color-accent)" opacity="0.9" />
          </svg>
        </div>
      </div>

      {/* Chapter rail */}
      <nav
        aria-label="Tour chapters"
        className="tour-fade fixed top-3 left-1/2 flex max-w-[calc(100vw-1.5rem)] -translate-x-1/2 items-center gap-2 overflow-x-auto rounded-full border border-white/10 bg-card/95 px-3 py-1.5 shadow-[0_10px_30px_rgb(0_0_0/0.4)] backdrop-blur-2xl"
      >
        <span className="mr-0.5 hidden max-w-36 truncate text-xs font-medium text-accent sm:block">
          {finished ? t.complete : step ? chapterName(step.chapter, lang) : null}
        </span>
        {chapters.map((g) => {
          const current = !finished && g.items.some((it) => it.i === index);
          return (
            <div key={`${g.chapter}-${g.items[0].i}`} title={chapterName(g.chapter, lang)} className={cx("flex items-center rounded-full px-0.5", current && "bg-white/6")}>
              {g.items.map(({ step: s, i }) => {
                const done = finished || (index !== null && i < index);
                const here = !finished && i === index;
                return (
                  <button
                    key={s.id}
                    type="button"
                    tabIndex={-1}
                    onClick={() => onGoto(i)}
                    aria-label={`Step ${i + 1}: ${s.title}`}
                    title={titleOf(s, lang)}
                    aria-current={here ? "step" : undefined}
                    className="grid h-5 place-items-center px-[3px] active:scale-90"
                  >
                    <span
                      className={cx(
                        "block h-1.5 rounded-full transition-all duration-500 ease-ios",
                        here ? "w-5 bg-accent" : done ? "w-1.5 bg-accent/55" : "w-1.5 bg-white/20 hover:bg-white/40",
                      )}
                    />
                  </button>
                );
              })}
            </div>
          );
        })}
      </nav>

      {/* Card */}
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-step={finished ? "finished" : step?.id}
        data-ready={isReady}
        className={cx(
          "fixed top-0 left-0 w-[min(22.5rem,calc(100vw-1.5rem))] rounded-2xl border border-white/10 bg-card shadow-[0_24px_60px_rgb(0_0_0/0.55)]",
          isReady ? "scale-100 opacity-100" : "pointer-events-none scale-[0.97] opacity-0",
        )}
      >
        <span ref={arrowRef} aria-hidden="true" className="absolute hidden size-3 rotate-45 rounded-[2px] bg-card" />
        {finished ? (
          <FinishCard titleId={titleId} total={total} onRestart={onRestart} onDone={onStop} doneRef={nextRef} reduced={reduced} lang={lang} />
        ) : step ? (
          <div className="relative overflow-hidden rounded-2xl p-4 sm:p-5">
            {auto && (
              <div aria-hidden="true" className="absolute inset-x-0 top-0 h-[3px] bg-white/6">
                <div
                  key={nonce}
                  className="tour-count h-full bg-accent"
                  style={{ animationDuration: `${dwell}ms`, animationPlayState: counting ? "running" : "paused" }}
                />
              </div>
            )}
            <div className="flex items-center gap-2 text-xs">
              <span lang={lang} className="min-w-0 flex-1 truncate font-semibold tracking-wide text-accent uppercase">
                {chapterName(step.chapter, lang)}
              </span>
              <LangSwitch lang={lang} onLang={onLang} />
              <span className="shrink-0 text-fg-4 tabular-nums">
                {t.num(index! + 1)} / {t.num(total)}
              </span>
            </div>
            <div lang={lang}>
              <h2 id={titleId} className="mt-2.5 text-[1.0625rem] leading-snug font-semibold tracking-tight text-fg">
                {titleOf(step, lang)}
              </h2>
              <TypedText key={`${nonce}-${lang}`} text={body} run={isReady} instant={reduced} />
            </div>
            <div className="mt-4 h-1 overflow-hidden rounded-full bg-white/8">
              <div className="h-full rounded-full bg-accent transition-[width] duration-500 ease-ios" style={{ width: `${progress}%` }} />
            </div>
            <div className="mt-4 flex items-center gap-1.5" lang={lang}>
              <button
                type="button"
                onClick={onToggleAuto}
                aria-label={auto ? t.pause : t.play}
                title={`${auto ? t.pause : t.play} (Space)`}
                className={cx(
                  "flex items-center gap-1.5 rounded-full py-1 pr-2.5 pl-2 text-xs font-semibold",
                  auto ? "bg-accent/15 text-accent" : "bg-white/8 text-fg-3 hover:text-fg",
                )}
              >
                {auto ? <PauseIcon className="size-3" /> : <PlayIcon className="size-3" />}
                {auto ? (
                  <span className="flex items-center gap-1">
                    <span className="tour-live size-1.5 rounded-full bg-accent" />
                    {t.live}
                  </span>
                ) : (
                  t.paused
                )}
              </button>
              <button type="button" onClick={onStop} className="mr-auto rounded-lg px-2 py-1.5 text-sm text-fg-4 hover:bg-white/6 hover:text-fg-2">
                {t.skip}
              </button>
              <button
                type="button"
                onClick={back}
                disabled={index === 0}
                className="rounded-xl px-3 py-1.5 text-sm font-medium text-fg-2 hover:bg-white/8 disabled:opacity-35"
              >
                {t.back}
              </button>
              <button
                ref={nextRef}
                type="button"
                onClick={next}
                className="rounded-xl bg-accent px-4 py-1.5 text-sm font-semibold text-accent-ink hover:brightness-110"
              >
                {index === total - 1 ? t.finish : t.next}
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {finished && !reduced && <Confetti />}

      <div aria-live="polite" className="sr-only">
        {finished
          ? "Tour complete. You're ready."
          : step && isReady
            ? `${t.num(index! + 1)} / ${t.num(total)}: ${titleOf(step, lang)}. ${body}`
            : ""}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------ */
/* Card pieces                                                                                 */
/* ------------------------------------------------------------------------------------------ */

/** Splits into user-perceived characters, so Bangla vowel signs never dangle mid-typing. */
function graphemes(text: string): string[] {
  if (typeof Intl !== "undefined" && "Segmenter" in Intl) {
    return Array.from(new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(text), (s) => s.segment);
  }
  return Array.from(text);
}

function TypedText({ text, run, instant }: { text: string; run: boolean; instant: boolean }) {
  const parts = useMemo(() => graphemes(text), [text]);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!run || instant) return;
    const id = window.setInterval(() => {
      setN((v) => {
        if (v >= parts.length) {
          window.clearInterval(id);
          return v;
        }
        return v + 2;
      });
    }, 16);
    return () => window.clearInterval(id);
  }, [run, instant, parts]);
  const shown = instant ? parts.length : Math.min(n, parts.length);
  return (
    <p className="mt-1.5 text-[0.9375rem] leading-relaxed text-fg-3">
      <span className="sr-only">{text}</span>
      <span aria-hidden="true">
        {parts.slice(0, shown).join("")}
        {shown < parts.length && <span className="tour-caret" />}
        <span className="invisible">{parts.slice(shown).join("")}</span>
      </span>
    </p>
  );
}

function FinishCard({
  titleId,
  total,
  onRestart,
  onDone,
  doneRef,
  reduced,
  lang,
}: {
  titleId: string;
  total: number;
  onRestart: () => void;
  onDone: () => void;
  doneRef: React.RefObject<HTMLButtonElement | null>;
  reduced: boolean;
  lang: TourLang;
}) {
  const t = UI[lang];
  return (
    <div className="relative overflow-hidden rounded-2xl p-6 text-center">
      <div aria-hidden="true" className="pointer-events-none absolute -top-20 left-1/2 size-48 -translate-x-1/2 rounded-full bg-accent/25 blur-3xl" />
      <div className={cx("relative mx-auto grid size-14 place-items-center rounded-2xl bg-accent text-accent-ink", !reduced && "tour-pop")}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" className="size-7" aria-hidden="true">
          <path d="m5 12.5 4.5 4.5L19 7.5" className={reduced ? undefined : "tour-check"} />
        </svg>
      </div>
      <h2 id={titleId} className="relative mt-4 text-[1.375rem] font-bold tracking-tight text-fg">
        {t.readyTitle}
      </h2>
      <p lang={lang} className="relative mx-auto mt-2 max-w-xs text-[0.9375rem] leading-relaxed text-fg-3">
        {t.readyBody(total)}
      </p>
      <div className="relative mt-5 flex justify-center gap-2">
        <button type="button" onClick={onRestart} className="rounded-xl px-4 py-2 text-sm font-medium text-fg-2 hover:bg-white/8">
          {t.restart}
        </button>
        <button
          ref={doneRef}
          type="button"
          onClick={onDone}
          className="rounded-xl bg-accent px-5 py-2 text-sm font-semibold text-accent-ink hover:brightness-110"
        >
          {t.done}
        </button>
      </div>
    </div>
  );
}

const CONFETTI_COLORS = ["#a8bca1", "#27c93f", "#ffbd2e", "#96eefb", "#b9a5ff", "#ff9456", "#f5f5f5"];

function Confetti() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const resize = () => {
      canvas.width = window.innerWidth * dpr;
      canvas.height = window.innerHeight * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ox = window.innerWidth / 2;
    const oy = window.innerHeight * 0.42;
    const parts = Array.from({ length: 150 }, () => {
      const a = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.15;
      const v = 6 + Math.random() * 9;
      return {
        x: ox,
        y: oy,
        vx: Math.cos(a) * v,
        vy: Math.sin(a) * v,
        r: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 0.35,
        w: 5 + Math.random() * 5,
        h: 8 + Math.random() * 6,
        c: CONFETTI_COLORS[Math.floor(Math.random() * CONFETTI_COLORS.length)],
      };
    });
    const t0 = performance.now();
    let raf = 0;
    const draw = (now: number) => {
      const t = (now - t0) / 2800;
      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
      if (t >= 1) return;
      ctx.globalAlpha = t > 0.7 ? 1 - (t - 0.7) / 0.3 : 1;
      for (const p of parts) {
        p.vy += 0.28;
        p.vx *= 0.985;
        p.vy *= 0.985;
        p.x += p.vx;
        p.y += p.vy;
        p.r += p.vr;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.r);
        ctx.scale(1, Math.cos(p.r * 2));
        ctx.fillStyle = p.c;
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        ctx.restore();
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);
  return <canvas ref={ref} aria-hidden="true" className="pointer-events-none fixed inset-0 size-full" />;
}

/* ------------------------------------------------------------------------------------------ */
/* Keyframes                                                                                   */
/* ------------------------------------------------------------------------------------------ */

const CSS_TEXT = `
.tour-root{--tour-accent:var(--color-accent,#a8bca1)}
.tour-fade{animation:tour-fade .35s ease both}
.tour-pop{animation:tour-pop .5s var(--ease-ios) both}
.tour-hole[data-empty="true"]{border-radius:999px}
.tour-ring{box-shadow:0 0 0 2px color-mix(in oklab,var(--tour-accent) 85%,transparent);animation:tour-ring 2s ease-out infinite;transition:opacity .3s ease}
.tour-press{transform-origin:2px 2px;animation:tour-press 2.4s var(--ease-ios) infinite}
.tour-wiggle{transform-origin:2px 2px;animation:tour-wiggle 1.6s ease-in-out infinite}
.tour-ripple,.tour-glow{position:absolute;left:2px;top:2px;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:999px;pointer-events:none}
.tour-ripple{border:2px solid var(--tour-accent);animation:tour-ripple 2.4s ease-out infinite}
.tour-glow{background:radial-gradient(circle,color-mix(in oklab,var(--tour-accent) 55%,transparent),transparent 65%);animation:tour-glow 1.6s ease-in-out infinite}
.tour-caret{display:inline-block;width:2px;height:1em;margin-left:1px;vertical-align:-.15em;background:var(--tour-accent);animation:tour-blink .9s steps(1) infinite}
.tour-count{width:0;animation:tour-count linear forwards}
.tour-live{animation:tour-live 1.4s ease-in-out infinite}
@keyframes tour-count{to{width:100%}}
@keyframes tour-live{50%{opacity:.25}}
.tour-twinkle{animation:tour-twinkle 2.6s ease-in-out infinite}
.tour-check{stroke-dasharray:24;stroke-dashoffset:24;animation:tour-draw .5s .25s var(--ease-ios) forwards}
@keyframes tour-fade{from{opacity:0}}
@keyframes tour-pop{from{opacity:0;transform:translateY(14px) scale(.94)}}
@keyframes tour-ring{0%{box-shadow:0 0 0 2px color-mix(in oklab,var(--tour-accent) 90%,transparent),0 0 0 0 color-mix(in oklab,var(--tour-accent) 45%,transparent)}70%,100%{box-shadow:0 0 0 2px color-mix(in oklab,var(--tour-accent) 90%,transparent),0 0 0 12px transparent}}
@keyframes tour-press{0%,8%{transform:scale(1)}14%{transform:scale(.78) translate(1px,1px)}24%{transform:scale(1.04)}32%,100%{transform:scale(1)}}
@keyframes tour-ripple{0%,10%{transform:scale(.2);opacity:0}14%{opacity:1}45%,100%{transform:scale(1.6);opacity:0}}
@keyframes tour-wiggle{0%,100%{transform:translate(0,0) rotate(0)}25%{transform:translate(3px,-2px) rotate(-6deg)}75%{transform:translate(-2px,2px) rotate(4deg)}}
@keyframes tour-glow{0%,100%{transform:scale(.7);opacity:.5}50%{transform:scale(1.15);opacity:1}}
@keyframes tour-blink{50%{opacity:0}}
@keyframes tour-twinkle{0%,100%{transform:scale(1) rotate(0)}50%{transform:scale(1.12) rotate(12deg)}}
@keyframes tour-draw{to{stroke-dashoffset:0}}
.tour-reduced *,.tour-reduced{animation:none!important;transition:none!important}
@media (prefers-reduced-motion:reduce){.tour-root *,.tour-root{animation:none!important;transition:none!important}.tour-check{stroke-dashoffset:0}}
`;

function TourStyles() {
  return <style>{CSS_TEXT}</style>;
}
