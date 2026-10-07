"use client";

import Image from "next/image";
import Link from "next/link";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { ScoreBar } from "@/components/charts";
import { useSession } from "@/components/session";
import { Async, Badge, Button, Card, cx, ErrorNote, Facts, inputClass, PageHeader, StatusBadge, TierBadge } from "@/components/ui";
import { api, ApiError, qs, useApi } from "@/lib/api";
import { maskId, taka, when, words } from "@/lib/format";
import type { AppealRelation, CustomerAppeal, PayResult, Scenario, Text2, Tier } from "@/lib/types";

import { clockSpoken, clockText, CUE, digits, type Lang, RELATIONS, steps, STRIP_TEXT, T, type Words } from "./copy";
// upay's app icon. The name and the mark belong to UCB Fintech Company Limited.
import upayLogo from "./upay-logo.png";

interface Payment {
  sender_id: string;
  receiver_id: string;
  amount: number;
  sender_balance_before: number;
}
/** What the platform has learned about where a wallet is normally used from. */
interface Habits {
  home: string;
  transactions: number;
  places: { district: string; transactions: number }[];
  network_transactions: number;
  network_history_needed: number;
  districts: string[];
}
interface Check { level: "none" | "caution" | "high"; message: Text2 | null }
interface Outcome { status: string; status_reason: string | null }
interface Reported { report_id: number; case_id: number }

// Addresses from the ranges reserved for documentation: they belong to nobody.
const NETWORKS = [
  { ip: "", label: "Not sent by the app" },
  { ip: "203.0.113.24", label: "The usual connection (203.0.113.x)" },
  { ip: "198.51.100.77", label: "A different connection (198.51.100.x)" },
];

const SCENARIO: Record<string, { title: string; text: string }> = {
  allow: { title: "An ordinary payment", text: "Goes straight through; the customer never sees FraudLens." },
  warn: { title: "Looks like a scam", text: "The customer gets a warning naming the scam, and chooses." },
  step_up: { title: "Riskier", text: "Verify again, then wait out a cooling-off period." },
  hold: { title: "Very likely fraud", text: "Paused until a reviewer decides; the customer can appeal." },
  confirmed_fraud: { title: "To a confirmed-fraud wallet", text: "Held by a hard rule, whatever the model says." },
};
const CATEGORIES: [string, string, string][] = [
  ["impersonation", "কেউ কর্মকর্তা সেজে ফোন করেছে", "Someone posed as staff"],
  ["prize_or_lottery", "পুরস্কার বা লটারির কথা বলেছে", "A prize or lottery"],
  ["investment", "বিনিয়োগে লাভের লোভ দেখিয়েছে", "An investment offer"],
  ["account_takeover", "আমার অ্যাকাউন্ট অন্য কেউ ব্যবহার করেছে", "Someone used my account"],
  ["wrong_send", "ভুল নম্বরে পাঠিয়েছি", "I sent to the wrong number"],
  ["fake_payment", "টাকা পাঠানোর ভুয়া প্রমাণ দেখিয়েছে", "A fake payment proof"],
  ["merchant_or_marketplace", "অনলাইনে কিনে পণ্য পাইনি", "An online seller took my money"],
  ["phishing_link_or_app", "সন্দেহজনক লিংক বা অ্যাপ", "A suspicious link or app"],
  ["job_or_loan", "চাকরি বা ঋণের প্রলোভন", "A job or loan offer"],
  ["other", "অন্য কিছু", "Something else"],
];
const WALLET_ID = /^[A-Za-z0-9_-]{1,32}$/;
const LANG_KEY = "fraudlens.phone.lang";
const REASON_MAX = 500;

// The phone is drawn the way upay's app looks: its yellow bar, blue buttons and white pages,
// whatever the console around it looks like. What FraudLens adds is the strip under the bar.
// Every pairing here passes WCAG AA contrast for its text size.
const STRIP: Record<Tier, string> = {
  allow: "border-green-300 bg-green-50 text-green-900",
  warn: "border-amber-300 bg-amber-50 text-amber-950",
  step_up: "border-orange-300 bg-orange-50 text-orange-950",
  hold: "border-red-300 bg-red-50 text-red-900",
};
// The services on upay's home screen. Only send money does anything here.
const SERVICES: [string, string, string][] = [
  ["সেন্ড মানি", "Send money", "M3 11.5 21 3l-6.5 18-3-7.5z"],
  ["মোবাইল রিচার্জ", "Recharge", "M8 2.5h8A1.5 1.5 0 0 1 17.5 4v16a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 20V4A1.5 1.5 0 0 1 8 2.5zM10.5 18.5h3"],
  ["ক্যাশ আউট", "Cash out", "M12 3v12m0 0-4-4m4 4 4-4M4 20h16"],
  ["মেক পেমেন্ট", "Payment", "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 18h2v2h-2zM18 14h2M14 18v2"],
  ["অ্যাড মানি", "Add money", "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 8v8M8 12h8"],
  ["পে বিল", "Pay bill", "M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6"],
  ["রিকোয়েস্ট মানি", "Request", "M12 21V9m0 0-4 4m4-4 4 4M4 4h16"],
  ["ফান্ড ট্রান্সফার", "Transfer", "M3 9.5 12 4l9 5.5M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"],
];

/** The language the demo customer last chose. Storage can be missing or blocked: Bangla then. */
function savedLang(): Lang {
  try {
    return window.localStorage.getItem(LANG_KEY) === "en" ? "en" : "bn";
  } catch {
    return "bn";
  }
}

function Icon({ d, className }: { d: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d={d} />
    </svg>
  );
}

function StatusBar() {
  return (
    <div aria-hidden="true" className="flex items-center justify-between bg-upay-yellow px-6 pt-3 text-[13px] font-semibold text-black">
      <span>9:41</span>
      <svg viewBox="0 0 42 12" fill="currentColor" className="h-3">
        <path d="M0 8h3v4H0zM5 5.5h3V12H5zM10 3h3v9h-3zM15 0h3v12h-3z" />
        <rect x="24.5" y="1" width="15" height="10" rx="2.5" fill="none" stroke="currentColor" />
        <rect x="26.5" y="3" width="11" height="6" rx="1" />
        <path d="M40.5 4.5h1.5v3h-1.5z" />
      </svg>
    </div>
  );
}

function LangSwitch({ lang, onSwitch }: { lang: Lang; onSwitch: () => void }) {
  const other: Lang = lang === "bn" ? "en" : "bn";
  return (
    <button
      type="button"
      onClick={onSwitch}
      aria-label={T.switchLabel[lang]}
      data-testid="phone-lang"
      className="min-h-11 min-w-11 rounded-full px-2 text-xs font-bold text-black underline-offset-2 hover:bg-black/10 focus-visible:outline-2 focus-visible:outline-black"
    >
      <span lang={other}>{T.switchTo[lang]}</span>
    </button>
  );
}

// The buttons stay at the bottom of the screen while a long message scrolls behind them.
const FOOT = "sticky -bottom-4 -mx-4 -mb-4 mt-auto space-y-2 border-t border-neutral-200 bg-white p-4";

/** One screen of the app. Its title takes focus when it appears, so a screen reader
 * announces the new screen and keyboard users start from the top of it. */
function Screen({
  title, tone = "plain", heading, onBack, lang, onLang, children,
}: {
  title: ReactNode; tone?: Tier | "plain"; heading?: ReactNode; onBack?: () => void;
  lang: Lang; onLang: () => void; children: ReactNode;
}) {
  const titleRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => titleRef.current?.focus(), []);
  return (
    <div className="flex h-full flex-col bg-white text-neutral-900">
      <StatusBar />
      <div className="relative flex min-h-12 items-center justify-center bg-upay-yellow px-12 pb-1 text-center text-black">
        {onBack && (
          <button type="button" aria-label={T.back[lang]} onClick={onBack} className="absolute top-0 left-1 grid size-11 place-items-center rounded-full hover:bg-black/10 focus-visible:outline-2 focus-visible:outline-black">
            <Icon d="M15 5l-7 7 7 7" className="size-5" />
          </button>
        )}
        <h2 ref={titleRef} tabIndex={-1} className="text-[15px] font-bold outline-none">{title}</h2>
        <span className="absolute top-0 right-1"><LangSwitch lang={lang} onSwitch={onLang} /></span>
      </div>
      {tone !== "plain" && heading && (
        <div role="status" className={cx("border-b px-4 py-2 text-sm font-semibold", STRIP[tone])}>{heading}</div>
      )}
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4 text-sm">{children}</div>
    </div>
  );
}

function Home({ lang, onLang }: { lang: Lang; onLang: () => void }) {
  return (
    <div className="flex h-full flex-col bg-white text-neutral-900">
      <StatusBar />
      <div className="flex items-center gap-3 bg-upay-yellow px-4 pt-2 pb-4 text-black">
        <Image src={upayLogo} alt="upay" unoptimized className="size-11 rounded-full bg-white" />
        <div className="min-w-0 flex-1 leading-tight">
          <div className="text-sm font-semibold">{lang === "bn" ? "ডেমো গ্রাহক" : "Demo customer"}</div>
          <div className="text-xs text-black/80">01XXXXXXXXX</div>
        </div>
        <LangSwitch lang={lang} onSwitch={onLang} />
      </div>
      <ul className="grid grid-cols-4 gap-x-1 gap-y-4 px-2 py-5 text-center">
        {SERVICES.map(([bn, en, d], index) => (
          <li key={en} className={cx("flex flex-col items-center gap-1.5", index > 0 && "opacity-60")}>
            <span className="grid size-11 place-items-center rounded-2xl bg-upay-blue/10 text-upay-blue"><Icon d={d} className="size-6" /></span>
            <span className="text-[11px] leading-tight font-medium">{lang === "bn" ? bn : en}</span>
          </li>
        ))}
      </ul>
      <div className="mx-4 rounded-2xl border border-upay-blue/20 bg-upay-blue/5 px-3 py-3 text-sm text-neutral-700">
        <div className="font-semibold text-upay-blue">{T.appName[lang]}</div>
        {T.chooseLeft[lang]}
      </div>
      <p className="mt-auto px-4 pb-4 text-center text-[11px] text-neutral-600">{T.helpline[lang]}</p>
    </div>
  );
}

function PhoneButton({ tone = "dark", ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "dark" | "light" | "red" }) {
  const look = {
    dark: "bg-upay-blue font-semibold text-white hover:brightness-110 disabled:opacity-40",
    light: "border border-upay-blue bg-white font-medium text-upay-blue hover:bg-upay-blue/5",
    red: "border border-red-300 bg-red-50 font-medium text-red-800 hover:bg-red-100",
  }[tone];
  return (
    <button
      type="button"
      {...rest}
      className={cx("min-h-11 w-full rounded-full px-3 py-2.5 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-upay-blue disabled:cursor-not-allowed", look)}
    />
  );
}

/** The warning, worded for the kind of scam the decision looks like. Without a cue
 * (an older API), the policy's own message is shown instead. */
function Warning({ decision, lang }: { decision: NonNullable<PayResult["decision"]>; lang: Lang }) {
  const copy = decision.cue ? CUE[decision.cue] : null;
  if (!copy) return decision.customer_message ? <p className="text-[15px] leading-relaxed">{decision.customer_message[lang]}</p> : null;
  return (
    <div data-testid="phone-warning" data-cue={decision.cue}>
      <h3 className="text-base leading-snug font-bold">{copy.title[lang]}</h3>
      <p className="mt-1.5 text-[15px] leading-relaxed text-neutral-800">{copy.body[lang]}</p>
      <p className="mt-2 flex gap-2 rounded-xl bg-neutral-100 px-3 py-2 text-[13px] text-neutral-800">
        <Icon d="M12 8v5M12 16.5v.5M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z" className="mt-0.5 size-4 shrink-0" />
        {copy.check[lang]}
      </p>
    </div>
  );
}

/** "What happens next": the steps after an interruption, the current one marked. */
function Timeline({ tier, minutes, lang }: { tier: Exclude<Tier, "allow">; minutes: number | null; lang: Lang }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="rounded-2xl border border-neutral-200 px-3 py-2">
      <h3 id={id} className="text-xs font-semibold tracking-wide text-neutral-600 uppercase">{T.whatNext[lang]}</h3>
      <ol className="mt-2 space-y-2">
        {steps(tier, minutes, lang).map((step, index) => (
          <li key={index} aria-current={step.state === "now" ? "step" : undefined} className="flex gap-2 text-[13px] leading-snug">
            <span
              aria-hidden="true"
              className={cx(
                "mt-0.5 grid size-5 shrink-0 place-items-center rounded-full text-[11px] font-bold",
                step.state === "done" ? "bg-green-700 text-white" : step.state === "now" ? "bg-upay-blue text-white" : "border border-neutral-400 text-neutral-600",
              )}
            >
              {step.state === "done" ? "✓" : digits(index + 1, lang)}
            </span>
            <span className={cx(step.state === "next" ? "text-neutral-600" : "text-neutral-900", step.state === "now" && "font-semibold")}>{step.text}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** A visible countdown. Screen readers get the time on request rather than every second. */
function Countdown({ seconds, total, label, lang, tone }: { seconds: number; total: number; label: string; lang: Lang; tone: "orange" | "red" }) {
  const done = total > 0 ? Math.min(1, Math.max(0, 1 - seconds / total)) : 1;
  return (
    <div className={cx("rounded-2xl border px-3 py-2", tone === "orange" ? "border-orange-300 bg-orange-50 text-orange-950" : "border-red-300 bg-red-50 text-red-950")}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-semibold">{label}</span>
        <span role="timer" aria-live="off" aria-label={`${label} ${clockSpoken(seconds, lang)}`} data-testid="phone-countdown" className="text-2xl font-bold tabular-nums">
          {clockText(seconds, lang)}
        </span>
      </div>
      <div aria-hidden="true" className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white">
        <div className={cx("h-full rounded-full", tone === "orange" ? "bg-orange-600" : "bg-red-700")} style={{ width: `${done * 100}%` }} />
      </div>
    </div>
  );
}

function Demo({ scenarios, startedAt }: { scenarios: Scenario[]; startedAt: string }) {
  const { canReview } = useSession();
  // Demo is only rendered in the browser, after the scenarios load, so storage can be read here.
  const [lang, setLang] = useState<Lang>(savedLang);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [result, setResult] = useState<PayResult | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [reported, setReported] = useState<Reported | null>(null);
  const [reporting, setReporting] = useState(false);
  const [appealing, setAppealing] = useState(false);
  const [appeal, setAppeal] = useState<CustomerAppeal | null>(null);
  const [relation, setRelation] = useState<AppealRelation | null>(null);
  const [reason, setReason] = useState("");
  const [pin, setPin] = useState("");
  // Seconds left of a cooling-off wait, and until a hold's review deadline.
  const [wait, setWait] = useState<number | null>(null);
  const [review, setReview] = useState<number | null>(null);
  const [now, setNow] = useState(startedAt);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [custom, setCustom] = useState({ sender: "", receiver: "", amount: "" });
  // Where the customer is while paying: empty means at home, with no network sent.
  const [where, setWhere] = useState({ district: "", ip: "" });
  const habits = useApi<Habits>(payment ? `/v1/demo/habits${qs({ sender_id: payment.sender_id })}` : null);
  const relationName = useId();

  const decision = result?.decision ?? null;
  const profile = habits.data;
  const status = outcome?.status ?? result?.status ?? null;
  const w = (words: Words) => words[lang];

  function switchLang() {
    const next: Lang = lang === "bn" ? "en" : "bn";
    setLang(next);
    try {
      window.localStorage.setItem(LANG_KEY, next);
    } catch {
      // a blocked store only means the choice is not remembered
    }
  }

  // The countdowns run in real time; the platform's clock still has the last word.
  const ticking = (wait ?? 0) > 0 || (review ?? 0) > 0;
  useEffect(() => {
    if (!ticking) return;
    const timer = window.setInterval(() => {
      setWait((left) => (left === null ? null : Math.max(0, left - 1)));
      setReview((left) => (left === null ? null : left - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [ticking]);

  function start(next: Payment, id: string | null) {
    setPayment(next);
    setPicked(id);
    setWhere({ district: "", ip: "" });
    setCheck(null);
    setResult(null);
    setOutcome(null);
    setReported(null);
    setReporting(false);
    setAppealing(false);
    setAppeal(null);
    setRelation(null);
    setReason("");
    setPin("");
    setWait(null);
    setReview(null);
    setError(null);
  }

  // What the app would show as soon as the customer has chosen who to pay.
  useEffect(() => {
    if (!payment) return;
    let live = true;
    api<Check>("/v1/demo/recipient-check", { sender_id: payment.sender_id, receiver_id: payment.receiver_id }).then(
      (answer) => live && setCheck(answer),
      () => live && setCheck(null), // advice only: the payment still works without it
    );
    return () => {
      live = false;
    };
  }, [payment]);

  async function run(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (problem) {
      const retry = problem instanceof ApiError && problem.code === "cooling_off" ? Number(problem.extra.retry_after_seconds) : NaN;
      if (Number.isFinite(retry)) setWait(retry);
      else setError(problem as Error);
    } finally {
      setBusy(false);
    }
  }

  const putCustom = (event: React.FormEvent) => {
    event.preventDefault();
    const sender = custom.sender.trim().toUpperCase();
    const receiver = custom.receiver.trim().toUpperCase();
    const amount = Number(custom.amount);
    if (!WALLET_ID.test(sender) || !WALLET_ID.test(receiver)) return setError(new Error("Enter two wallet numbers, such as W0001234."));
    if (!(amount > 0 && amount <= 10_000_000)) return setError(new Error("Enter an amount between 1 and 1,00,00,000 taka."));
    void run(async () => {
      const draft = await api<Payment>(`/v1/demo/payment-draft${qs({ sender_id: sender, receiver_id: receiver, amount })}`);
      start(draft, null);
    });
  };

  const send = () =>
    run(async () => {
      if (!payment) return;
      const paid = await api<PayResult>("/v1/demo/pay", {
        sender_id: payment.sender_id,
        receiver_id: payment.receiver_id,
        amount: payment.amount,
        ...(where.district && { district: where.district }),
        ...(where.ip && { ip: where.ip }),
      });
      setResult(paid);
      habits.reload(); // a payment that went through is part of the wallet's habits now
      // Tell the customer about the wait up front; the server still has the last word when they try to send.
      if (paid.decision?.tier === "step_up" && paid.decision.cooling_off_minutes) setWait(paid.decision.cooling_off_minutes * 60);
      if (paid.status === "held" && paid.decision?.review_sla_minutes) setReview(paid.decision.review_sla_minutes * 60);
    });

  const respond = (action: "proceed" | "cancel", passed = false) =>
    run(async () => {
      if (!result) return;
      setOutcome(await api<Outcome>("/v1/demo/respond", { txn_id: result.txn_id, action, step_up_passed: passed }));
      setWait(null);
    });

  const report = (category: string, label: string) =>
    run(async () => {
      if (!payment) return;
      setReported(
        await api<Reported>("/v1/demo/report", {
          reporter_id: payment.sender_id,
          reported_wallet_id: payment.receiver_id,
          txn_id: result?.txn_id ?? null,
          category,
          description: `Reported from the app with one tap: ${label}.`,
        }),
      );
      setReporting(false);
    });

  const fileAppeal = (event: React.FormEvent) => {
    event.preventDefault();
    if (!result || !relation || reason.trim().length < 3) return;
    void run(async () => {
      setAppeal(await api<CustomerAppeal>("/v1/demo/appeal", { txn_id: result.txn_id, relation, reason: reason.trim() }));
      setAppealing(false);
    });
  };

  /** Ask where the appeal stands; an approved hold has been released by then. */
  const refreshAppeal = () =>
    run(async () => {
      if (!result) return;
      const fresh = await api<CustomerAppeal>(`/v1/demo/appeal${qs({ txn_id: result.txn_id })}`);
      setAppeal(fresh);
      if (fresh.txn_status && fresh.txn_status !== status && fresh.txn_status !== "pending_customer" && fresh.txn_status !== "held") {
        setOutcome({ status: fresh.txn_status, status_reason: null });
        setReview(null);
      }
    });

  const skip = (minutes: number) =>
    run(async () => {
      const answer = await api<{ now: string }>("/v1/demo/advance-clock", { minutes });
      setNow(answer.now);
      setWait((old) => (old === null ? null : Math.max(0, old - minutes * 60)));
      setReview((old) => (old === null ? null : old - minutes * 60));
    });

  const reportButton = !reported && payment && (
    <PhoneButton tone="red" onClick={() => setReporting(true)} disabled={busy}>{w(T.report)}</PhoneButton>
  );
  // Disputing a warning or a hold. Offered once per payment; a hold that has ended has nothing left to appeal.
  const appealButton = !appeal && decision && decision.tier !== "allow" && (
    <PhoneButton tone="light" onClick={() => setAppealing(true)} disabled={busy} data-testid="phone-appeal">{w(T.appeal)}</PhoneButton>
  );
  const appealNote = appeal && (
    <div role="status" data-testid="phone-appeal-status" data-status={appeal.status} className="rounded-2xl border border-upay-blue/30 bg-upay-blue/5 px-3 py-2 text-[13px] text-neutral-900">
      <div className="font-semibold text-upay-blue">
        {appeal.status === "approved" ? w(T.appealApproved) : appeal.status === "rejected" ? w(T.appealRejected) : w(T.appealSent)}
      </div>
      {appeal.status === "pending" && (
        <p className="mt-0.5">
          {w(T.appealBy)} {digits(new Date(appeal.sla_due_at).toLocaleTimeString(lang === "bn" ? "bn-BD" : "en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Dhaka" }), lang)}
          {appeal.tier !== "hold" && <span className="mt-1 block text-neutral-700">{w(T.appealNoMoney)}</span>}
        </p>
      )}
      {appeal.status === "pending" && (
        <button type="button" onClick={refreshAppeal} disabled={busy} className="mt-1 min-h-11 font-semibold text-upay-blue underline underline-offset-2">
          {w(T.checkStatus)}
        </button>
      )}
    </div>
  );
  const label = "text-xs font-semibold text-neutral-600";
  const avatar = (
    <span className="grid size-10 shrink-0 place-items-center rounded-full bg-upay-blue/10 text-upay-blue">
      <Icon d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20a7.5 7.5 0 0 1 15 0" className="size-5" />
    </span>
  );
  const amount = payment ? digits(taka(payment.amount), lang) : "";
  // One row once there is a decision to read, so the message and the buttons get the room.
  const party = payment && (result ? (
    <div className="flex items-center gap-3 rounded-2xl border border-neutral-200 px-3 py-2">
      {avatar}
      <span className="min-w-0 flex-1">
        <span className={cx(label, "block")}>{w(T.to)}</span>
        <span className="font-mono">{maskId(payment.receiver_id)}</span>
      </span>
      <span className="text-xl font-bold tabular-nums text-upay-blue">{amount}</span>
    </div>
  ) : (
    <div>
      <div className={label}>{w(T.to)}</div>
      <div className="mt-1 flex items-center gap-3">
        {avatar}
        <span className="font-mono text-[15px]">{maskId(payment.receiver_id)}</span>
      </div>
      <div className={cx(label, "mt-3 border-t border-neutral-200 pt-3")}>{w(T.amount)}</div>
      <div className="text-3xl font-bold tabular-nums text-upay-blue">{amount}</div>
    </div>
  ));
  const sendMoney = w(T.appName);
  // Back to the home screen. Not offered while a warning waits for the customer's answer.
  const home = busy ? undefined : () => { setPayment(null); setPicked(null); setResult(null); setOutcome(null); setReported(null); setAppeal(null); setAppealing(false); setError(null); setWait(null); setReview(null); };
  const screenProps = { lang, onLang: switchLang };

  let screen: ReactNode;
  let screenKey: string;
  if (!payment) {
    screenKey = "home";
    screen = <Home {...screenProps} />;
  } else if (reporting) {
    screenKey = "report";
    screen = (
      <Screen title={w(T.whatHappened)} onBack={() => setReporting(false)} {...screenProps}>
        {CATEGORIES.map(([id, bn, en]) => (
          <button
            key={id}
            type="button"
            disabled={busy}
            onClick={() => report(id, en)}
            className="min-h-11 rounded-2xl border border-neutral-200 px-3 py-2 text-left hover:bg-neutral-50 focus-visible:outline-2 focus-visible:outline-upay-blue disabled:opacity-50"
          >
            {lang === "bn" ? bn : en}
          </button>
        ))}
      </Screen>
    );
  } else if (appealing && decision) {
    screenKey = "appeal";
    const left = REASON_MAX - reason.length;
    screen = (
      <Screen title={w(T.appealTitle)} onBack={() => setAppealing(false)} {...screenProps}>
        <form onSubmit={fileAppeal} className="flex flex-1 flex-col gap-3" data-testid="phone-appeal-form">
          <p className="text-neutral-800">{w(T.appealIntro)}</p>
          <fieldset>
            <legend className="mb-1.5 text-[13px] font-semibold">{w(T.relation)}</legend>
            <div className="grid grid-cols-2 gap-1.5">
              {RELATIONS.map(([value, text]) => (
                <label
                  key={value}
                  className={cx(
                    "flex min-h-11 cursor-pointer items-center gap-2 rounded-xl border px-2.5 text-[13px] has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-upay-blue",
                    relation === value ? "border-upay-blue bg-upay-blue/10 font-semibold" : "border-neutral-300",
                  )}
                >
                  <input type="radio" name={relationName} value={value} checked={relation === value} onChange={() => setRelation(value)} className="accent-upay-blue" />
                  {text[lang]}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="text-[13px] font-semibold">
            {w(T.reason)}
            <textarea
              value={reason}
              maxLength={REASON_MAX}
              rows={3}
              required
              minLength={3}
              aria-describedby={`${relationName}-hint`}
              onChange={(event) => setReason(event.target.value)}
              className="mt-1 w-full rounded-xl border border-neutral-300 bg-white px-3 py-2 text-sm font-normal text-neutral-900 outline-none focus:border-upay-blue focus:ring-2 focus:ring-upay-blue/30"
            />
          </label>
          <p id={`${relationName}-hint`} className="-mt-2 flex justify-between text-xs text-neutral-600">
            <span>{w(T.reasonHint)}</span>
            <span aria-live="polite">{digits(left, lang)}</span>
          </p>
          <div className={FOOT}>
            <PhoneButton type="submit" disabled={busy || !relation || reason.trim().length < 3}>{w(T.submitAppeal)}</PhoneButton>
          </div>
        </form>
      </Screen>
    );
  } else if (!result) {
    screenKey = "draft";
    screen = (
      <Screen title={sendMoney} onBack={home} {...screenProps}>
        {party}
        <div className="text-xs text-neutral-700">
          {w(T.balance)}: {digits(taka(payment.sender_balance_before), lang)} · {w(T.from)} {maskId(payment.sender_id)}
        </div>
        {check && check.level !== "none" && check.message && (
          <div className={cx("rounded-2xl border px-3 py-2", check.level === "high" ? "border-red-300 bg-red-50" : "border-amber-300 bg-amber-50")}>
            <div className="mb-1 text-xs font-semibold tracking-wide text-neutral-700 uppercase">{w(T.beforeYouSend)}</div>
            <p className="text-[15px] leading-relaxed">{check.message[lang]}</p>
          </div>
        )}
        <div className={FOOT}>
          <PhoneButton onClick={send} disabled={busy}>{busy ? w(T.sending) : w(T.send)}</PhoneButton>
        </div>
      </Screen>
    );
  } else if (status === "pending_customer" && decision?.tier === "step_up") {
    screenKey = "step_up";
    const total = (decision.cooling_off_minutes || 30) * 60;
    const waiting = wait !== null && wait > 0;
    screen = (
      <Screen title={sendMoney} tone="step_up" heading={w(STRIP_TEXT.step_up)} {...screenProps}>
        {party}
        <Warning decision={decision} lang={lang} />
        {waiting ? (
          <>
            <Countdown seconds={wait} total={total} label={w(T.availableIn)} lang={lang} tone="orange" />
            <p className="text-[13px] text-neutral-800">{w(T.coolingOff)}</p>
          </>
        ) : (
          <p role="status" className="rounded-2xl border border-green-300 bg-green-50 px-3 py-2 text-[13px] text-green-900">{w(T.waitOver)}</p>
        )}
        <Timeline tier="step_up" minutes={decision.cooling_off_minutes} lang={lang} />
        {appealNote}
        <div className={FOOT}>
          {/* Cancelling is the first choice and is always open. */}
          <PhoneButton onClick={() => respond("cancel")} disabled={busy}>{w(T.cancel)}</PhoneButton>
          <label className="flex items-center justify-between gap-3 text-xs font-semibold text-neutral-700">
            <span>{w(T.pin)}</span>
            <input
              inputMode="numeric"
              autoComplete="off"
              maxLength={4}
              value={pin}
              onChange={(event) => setPin(event.target.value.replace(/\D/g, ""))}
              className="min-h-11 w-32 rounded-xl border border-neutral-400 bg-white px-3 py-1.5 text-center text-lg tracking-[0.5em] text-neutral-900 outline-none focus:border-upay-blue focus:ring-2 focus:ring-upay-blue/30"
              type="password"
            />
          </label>
          <PhoneButton tone="light" onClick={() => respond("proceed", true)} disabled={busy || pin.length !== 4}>
            {waiting ? `${w(T.verifySend)} · ${clockText(wait, lang)}` : w(T.verifySend)}
          </PhoneButton>
          {appealButton}
          {reportButton}
        </div>
      </Screen>
    );
  } else if (status === "pending_customer" && decision) {
    screenKey = "warn";
    screen = (
      <Screen title={sendMoney} tone="warn" heading={w(STRIP_TEXT.warn)} {...screenProps}>
        {party}
        <Warning decision={decision} lang={lang} />
        <Timeline tier="warn" minutes={null} lang={lang} />
        {appealNote}
        <div className={FOOT}>
          <PhoneButton onClick={() => respond("cancel")} disabled={busy}>{w(T.cancel)}</PhoneButton>
          {/* A warning is advice: the customer may always go ahead. */}
          <PhoneButton tone="light" onClick={() => respond("proceed")} disabled={busy}>{w(T.sendAnyway)}</PhoneButton>
          {appealButton}
          {reportButton}
        </div>
      </Screen>
    );
  } else if (status === "held" && decision) {
    screenKey = "hold";
    const total = (decision.review_sla_minutes ?? 30) * 60;
    screen = (
      <Screen title={sendMoney} tone="hold" heading={w(STRIP_TEXT.hold)} onBack={home} {...screenProps}>
        {party}
        <p className="flex items-center gap-2 rounded-2xl border border-green-300 bg-green-50 px-3 py-2 text-[13px] font-semibold text-green-900">
          <Icon d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z" className="size-4 shrink-0" />
          {w(T.heldMoney)}
        </p>
        <Warning decision={decision} lang={lang} />
        {review !== null && (review > 0
          ? <Countdown seconds={review} total={total} label={w(T.reviewIn)} lang={lang} tone="red" />
          : <p role="status" className="rounded-2xl border border-red-300 bg-red-50 px-3 py-2 text-[13px] text-red-900">{w(T.reviewLate)}</p>)}
        <Timeline tier="hold" minutes={decision.review_sla_minutes} lang={lang} />
        {appealNote}
        <p className="text-xs text-neutral-700">{w(T.helpline)}</p>
        <div className={FOOT}>
          {appealButton}
          {reportButton}
        </div>
      </Screen>
    );
  } else {
    screenKey = `done-${status}`;
    const sent = status === "completed";
    const title = sent ? w(T.sent) : status === "cancelled" ? w(T.cancelled) : w(T.notSent);
    screen = (
      <Screen title={sendMoney} onBack={home} {...screenProps}>
        <div className="flex flex-col items-center gap-2 pt-2 text-center">
          <span className={cx("grid size-14 place-items-center rounded-full text-white", sent ? "bg-green-700" : "bg-neutral-500")}>
            <Icon d={sent ? "M5 12.5l4.5 4.5L19 7.5" : "M6 6l12 12M18 6L6 18"} className="size-7" />
          </span>
          <div role="status" data-testid="phone-outcome" data-status={status} className="text-base font-bold">{title}</div>
        </div>
        {party}
        <p className="text-neutral-800">{sent ? w(T.sentBody) : status === "cancelled" ? w(T.cancelledBody) : w(T.refusedBody)}</p>
        {appealNote}
        <div className={FOOT}>
          {reportButton}
          <PhoneButton tone="light" onClick={() => start(payment, picked)}>{w(T.newPayment)}</PhoneButton>
        </div>
      </Screen>
    );
  }

  const waitMinutes = wait ? Math.min(60, Math.ceil(wait / 60)) : 0;
  const reviewMinutes = !waitMinutes && review && review > 0 ? Math.min(60, Math.ceil(review / 60)) : 0;

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem_minmax(0,1fr)]">
      <div className="space-y-4">
        <Card title="1 · Choose a payment" hint="Each is a recent payment that would still get this answer from the policy right now.">
          <div className="space-y-2">
            {scenarios.map((scenario) => (
              <button
                key={scenario.id}
                type="button"
                aria-pressed={picked === scenario.id}
                data-scenario={scenario.id}
                onClick={() => start(scenario.payment, scenario.id)}
                className={cx(
                  "flex w-full items-start justify-between gap-3 rounded-2xl border px-3 py-2 text-left text-sm",
                  picked === scenario.id ? "border-accent/60 bg-accent/10" : "border-line hover:bg-wash",
                )}
              >
                <span>
                  <span className="font-medium text-fg">{SCENARIO[scenario.id]?.title ?? words(scenario.id)}</span>
                  <span className="block text-xs text-fg-3">
                    {SCENARIO[scenario.id]?.text} {taka(scenario.payment.amount)} to {maskId(scenario.payment.receiver_id)}.
                  </span>
                </span>
                <TierBadge tier={scenario.expected_tier} />
              </button>
            ))}
            {!scenarios.length && <p className="text-sm text-fg-3">No ready payments: the platform has not scored any yet.</p>}
          </div>
        </Card>
        <Card title="Or enter your own" hint="Both wallets must be ones the platform has seen.">
          <form onSubmit={putCustom} className="grid grid-cols-2 gap-2 text-xs text-fg-2">
            <label>From wallet<input className={`${inputClass} mt-1 w-full`} value={custom.sender} maxLength={32} onChange={(e) => setCustom({ ...custom, sender: e.target.value })} /></label>
            <label>To wallet<input className={`${inputClass} mt-1 w-full`} value={custom.receiver} maxLength={32} onChange={(e) => setCustom({ ...custom, receiver: e.target.value })} /></label>
            <label className="col-span-2">Amount (taka)<input className={`${inputClass} mt-1 w-full`} inputMode="decimal" value={custom.amount} maxLength={10} onChange={(e) => setCustom({ ...custom, amount: e.target.value })} /></label>
            <Button type="submit" disabled={busy} className="col-span-2 mt-1">Put on the phone</Button>
          </form>
        </Card>
        {payment && profile && (
          <Card title="Where the customer is" hint="A payment far above the wallet's usual amounts, from a place or a connection it is not normally used from, is raised.">
            <div className="grid grid-cols-2 gap-2 text-xs text-fg-2">
              <label>Paying from
                <select className={`${inputClass} mt-1 w-full`} value={where.district} disabled={!!result} onChange={(e) => setWhere({ ...where, district: e.target.value })}>
                  <option value="">{profile.home} (home)</option>
                  {profile.districts.filter((district) => district !== profile.home).map((district) => (
                    <option key={district} value={district}>{district}</option>
                  ))}
                </select>
              </label>
              <label>Connection
                <select className={`${inputClass} mt-1 w-full`} value={where.ip} disabled={!!result} onChange={(e) => setWhere({ ...where, ip: e.target.value })}>
                  {NETWORKS.map((network) => <option key={network.ip} value={network.ip}>{network.label}</option>)}
                </select>
              </label>
            </div>
            <p className="mt-2 text-xs text-fg-3">
              Usually used from {profile.places.slice(0, 3).map((place) => `${place.district} (${place.transactions})`).join(", ") || "nowhere yet"}.{" "}
              {profile.network_transactions >= profile.network_history_needed
                ? "Its usual connection is known."
                : `Its connection is not known yet: ${profile.network_transactions} of the ${profile.network_history_needed} payments needed have carried one, so send a few from the usual connection first.`}
            </p>
          </Card>
        )}
      </div>

      <div>
        <div
          lang={lang}
          data-testid="phone"
          className="mx-auto h-[40rem] w-full max-w-[22rem] overflow-hidden rounded-[2.75rem] border-[10px] border-black bg-white shadow-2xl shadow-black/60 ring-1 ring-white/15 [color-scheme:light]"
        >
          <div key={screenKey} className="h-full">{screen}</div>
        </div>
        {reported && (
          <p className="mt-3 text-center text-xs text-fg-2">
            Report received.{" "}
            {canReview ? <Link href={`/cases/${reported.case_id}`} className="text-info hover:underline">It is on case #{reported.case_id}</Link> : `It is on case #${reported.case_id}`}.
          </p>
        )}
        {appeal && (
          <p className="mt-3 text-center text-xs text-fg-2">
            Appeal #{appeal.id} is {appeal.status}.{" "}
            {canReview && <Link href="/appeals" className="text-info hover:underline">Answer it in Customer appeals</Link>}
          </p>
        )}
        {error && <div className="mt-3"><ErrorNote error={error} /></div>}
      </div>

      <div className="space-y-4">
        <Card title="2 · What FraudLens decided" hint="The customer sees only the phone. This is the other side of the same payment.">
          {result ? (
            decision ? (
              <Facts
                rows={[
                  ["Tier", <TierBadge key="tier" tier={decision.tier} />],
                  ["Risk score", <ScoreBar key="score" score={decision.risk_score} />],
                  ["Payment now", <StatusBadge key="status" status={status} />],
                  ["Warning shown", decision.cue ? words(decision.cue) : "–"],
                  ["Decided in", `${decision.latency_ms.toFixed(1)} ms, inside the payment request`],
                  ["By", `${words(decision.mode)} · model ${decision.model_version} · policy ${decision.policy_version}`],
                  ["Needs a person", decision.requires_review ? "Yes: a reviewer releases or blocks it" : "No: the customer decides"],
                  ...(decision.case_id
                    ? ([[
                        "Case",
                        canReview ? <Link key="case" href={`/cases/${decision.case_id}`} className="text-info hover:underline">#{decision.case_id}</Link> : `#${decision.case_id}`,
                      ]] as [ReactNode, ReactNode][])
                    : []),
                ]}
              />
            ) : (
              <p className="text-sm text-fg-2">
                Not scored: the payment was {words(result.status).toLowerCase()} ({words(result.status_reason ?? "no reason given").toLowerCase()}).
              </p>
            )
          ) : (
            <p className="text-sm text-fg-3">Send the payment to see the decision.</p>
          )}
          {result && decision && canReview && (
            <Link href={`/decisions/${result.txn_id}`} className="mt-3 inline-block text-sm text-info hover:underline">
              Open the full explanation: reasons, rules and similar cases →
            </Link>
          )}
        </Card>
        <Card title="Platform clock" hint="The demo runs on simulated time, so a cooling-off period or a review deadline can be shown ending.">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>{when(now)} <span className="text-fg-3">Dhaka time</span></span>
            {waitMinutes > 0 ? (
              <Button small onClick={() => skip(waitMinutes)} disabled={busy}>Skip ahead {waitMinutes} minutes</Button>
            ) : reviewMinutes > 0 ? (
              <Button small onClick={() => skip(reviewMinutes)} disabled={busy}>Skip to the review deadline</Button>
            ) : (
              <Badge>{wait === 0 ? "cooling-off has passed" : "no wait in progress"}</Badge>
            )}
          </div>
          <p className="mt-2 text-xs text-fg-3">
            Skipping ahead moves the clock for the whole platform: review deadlines on open cases come closer by the same amount. It is recorded in the
            audit log.
          </p>
        </Card>
        <p className="text-xs text-fg-3">
          In production upay’s own app shows these screens and checks the PIN; it calls the same endpoints with a service account. Here a member of staff
          plays the customer, any four digits pass the PIN check, and every action is audited as a demo action. The phone speaks Bangla by default; its
          English switch is remembered in this browser only.
        </p>
        <p className="text-xs text-fg-3">
          The phone is a mock-up in upay’s colours, not the upay app. The upay name and logo belong to UCB Fintech Company Limited.
        </p>
      </div>
    </div>
  );
}

export default function PhonePage() {
  const scenarios = useApi<{ now: string; scenarios: Scenario[] }>("/v1/demo/scenarios");
  return (
    <>
      <PageHeader
        title="Customer phone demo"
        sub="What a customer sees when FraudLens interrupts a payment, in Bangla or English: a warning that names the scam, a second check with a cooling-off countdown, or a pause for review with its deadline, what happens next, and a way to appeal or report."
      />
      <Async state={scenarios}>{(data) => <Demo scenarios={data.scenarios} startedAt={data.now} />}</Async>
    </>
  );
}
