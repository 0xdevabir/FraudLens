"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";

import { ScoreBar } from "@/components/charts";
import { useSession } from "@/components/session";
import { Async, Badge, Button, Card, cx, ErrorNote, Facts, inputClass, PageHeader, StatusBadge, TierBadge } from "@/components/ui";
import { api, ApiError, qs, useApi } from "@/lib/api";
import { maskId, num, taka, when, words } from "@/lib/format";
import type { PayResult, Scenario, Text2, Tier } from "@/lib/types";

interface Payment {
  sender_id: string;
  receiver_id: string;
  amount: number;
  sender_balance_before: number;
}
interface Check { level: "none" | "caution" | "high"; message: Text2 | null }
interface Outcome { status: string; status_reason: string | null }
interface Reported { report_id: number; case_id: number }

const SCENARIO: Record<string, { title: string; text: string }> = {
  allow: { title: "An ordinary payment", text: "Goes straight through; the customer never sees FraudLens." },
  warn: { title: "Looks like a scam", text: "The customer gets a Bangla warning and chooses." },
  step_up: { title: "Riskier", text: "Verify again, then wait out a cooling-off period." },
  hold: { title: "Very likely fraud", text: "Paused until a reviewer decides." },
  confirmed_fraud: { title: "To a confirmed-fraud wallet", text: "Held by a hard rule, whatever the model says." },
};
const CATEGORIES: [string, string, string][] = [
  ["impersonation", "কেউ কর্মকর্তা সেজে ফোন করেছে", "Someone posed as staff"],
  ["prize_or_lottery", "পুরস্কার বা লটারির কথা বলেছে", "A prize or lottery"],
  ["investment", "বিনিয়োগে লাভের লোভ দেখিয়েছে", "An investment offer"],
  ["account_takeover", "আমার অ্যাকাউন্ট অন্য কেউ ব্যবহার করেছে", "Someone used my account"],
  ["wrong_send", "ভুল নম্বরে পাঠিয়েছি", "I sent to the wrong number"],
  ["other", "অন্য কিছু", "Something else"],
];
const WALLET_ID = /^[A-Za-z0-9_-]{1,32}$/;

function Screen({ tone, title, children }: { tone: Tier | "plain"; title: ReactNode; children: ReactNode }) {
  const bar = {
    plain: "bg-fill text-fg", allow: "bg-good text-accent-ink", warn: "bg-warn text-accent-ink",
    step_up: "bg-alert text-accent-ink", hold: "bg-bad text-accent-ink",
  }[tone];
  return (
    <div className="flex h-full flex-col">
      <div className={cx("px-4 pt-5 pb-3 text-sm font-semibold transition-colors duration-500", bar)}>{title}</div>
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4 text-sm text-fg">{children}</div>
    </div>
  );
}

function Message({ text }: { text: Text2 | null | undefined }) {
  if (!text) return null;
  return (
    <div>
      <p lang="bn" className="text-[15px] leading-relaxed text-fg">{text.bn}</p>
      <p className="mt-2 text-xs leading-relaxed text-fg-3">{text.en}</p>
    </div>
  );
}

function PhoneButton({ tone = "dark", ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "dark" | "light" | "red" }) {
  const look = {
    dark: "bg-accent font-semibold text-accent-ink disabled:opacity-40",
    light: "bg-white/8 text-fg hover:bg-white/14",
    red: "border border-bad/30 bg-bad/10 text-bad",
  }[tone];
  return <button type="button" {...rest} className={cx("w-full rounded-2xl px-3 py-2.5 text-sm font-medium disabled:cursor-not-allowed", look)} />;
}

function Demo({ scenarios, startedAt }: { scenarios: Scenario[]; startedAt: string }) {
  const { canReview } = useSession();
  const [payment, setPayment] = useState<Payment | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [result, setResult] = useState<PayResult | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [reported, setReported] = useState<Reported | null>(null);
  const [reporting, setReporting] = useState(false);
  const [pin, setPin] = useState("");
  const [wait, setWait] = useState<number | null>(null);
  const [now, setNow] = useState(startedAt);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [custom, setCustom] = useState({ sender: "", receiver: "", amount: "" });

  const decision = result?.decision ?? null;
  const status = outcome?.status ?? result?.status ?? null;

  function start(next: Payment, id: string | null) {
    setPayment(next);
    setPicked(id);
    setCheck(null);
    setResult(null);
    setOutcome(null);
    setReported(null);
    setReporting(false);
    setPin("");
    setWait(null);
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
      const paid = await api<PayResult>("/v1/demo/pay", { sender_id: payment.sender_id, receiver_id: payment.receiver_id, amount: payment.amount });
      setResult(paid);
      // Tell the customer about the wait up front; the server still has the last word when they try to send.
      if (paid.decision?.tier === "step_up" && paid.decision.cooling_off_minutes) setWait(paid.decision.cooling_off_minutes * 60);
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

  const skip = (minutes: number) =>
    run(async () => {
      const answer = await api<{ now: string }>("/v1/demo/advance-clock", { minutes });
      setNow(answer.now);
      setWait((old) => (old === null ? null : Math.max(0, old - minutes * 60)));
    });

  const reportButton = !reported && payment && (
    <PhoneButton tone="red" onClick={() => setReporting(true)} disabled={busy}>
      <span lang="bn">প্রতারণার অভিযোগ করুন</span> · Report a scam
    </PhoneButton>
  );
  const party = payment && (
    <div className="rounded-2xl bg-wash px-3 py-2">
      <div className="text-xs text-fg-3">To</div>
      <div className="font-mono">{maskId(payment.receiver_id)}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{taka(payment.amount)}</div>
    </div>
  );

  let screen: ReactNode;
  if (!payment) {
    screen = <Screen tone="plain" title="Send money"><p className="text-fg-3">Choose a payment on the left to begin.</p></Screen>;
  } else if (reporting) {
    screen = (
      <Screen tone="plain" title={<><span lang="bn">কী হয়েছিল?</span> · What happened?</>}>
        {CATEGORIES.map(([id, bn, en]) => (
          <button
            key={id}
            type="button"
            disabled={busy}
            onClick={() => report(id, en)}
            className="rounded-2xl border border-line px-3 py-2 text-left hover:bg-wash disabled:opacity-50"
          >
            <div lang="bn">{bn}</div>
            <div className="text-xs text-fg-3">{en}</div>
          </button>
        ))}
        <PhoneButton tone="light" onClick={() => setReporting(false)}>Back</PhoneButton>
      </Screen>
    );
  } else if (!result) {
    screen = (
      <Screen tone="plain" title="Send money">
        {party}
        <div className="text-xs text-fg-3">Balance {taka(payment.sender_balance_before)} · from {maskId(payment.sender_id)}</div>
        {check && check.level !== "none" && (
          <div className={cx("rounded-2xl border px-3 py-2", check.level === "high" ? "border-bad/30 bg-bad/10" : "border-warn/30 bg-warn/10")}>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-2">
              <span lang="bn">পাঠানোর আগে দেখুন</span> · Before you send
            </div>
            <Message text={check.message} />
          </div>
        )}
        <div className="mt-auto">
          <PhoneButton onClick={send} disabled={busy}>{busy ? "Sending…" : <><span lang="bn">পাঠান</span> · Send</>}</PhoneButton>
        </div>
      </Screen>
    );
  } else if (status === "pending_customer" && decision?.tier === "step_up") {
    const minutes = wait === null ? null : Math.ceil(wait / 60);
    screen = (
      <Screen tone="step_up" title={<><span lang="bn">আবার যাচাই করুন</span> · Verify again</>}>
        {party}
        <Message text={decision.customer_message} />
        <label className="block text-xs text-fg-2">
          <span lang="bn">পিন দিন</span> · Enter your PIN
          <input
            inputMode="numeric"
            autoComplete="off"
            maxLength={4}
            value={pin}
            onChange={(event) => setPin(event.target.value.replace(/\D/g, ""))}
            className={`${inputClass} mt-1 w-full text-center tracking-[0.5em]`}
            type="password"
          />
        </label>
        {minutes !== null && minutes > 0 && (
          <div className="rounded-2xl border border-alert/30 bg-alert/10 px-3 py-2 text-xs text-alert">
            <span lang="bn">এই লেনদেনটি আরও {num(minutes)} মিনিট পর পাঠানো যাবে।</span> You can send this in {num(minutes)} more minutes. Use the wait
            to check who asked you to pay.
          </div>
        )}
        <div className="mt-auto space-y-2">
          <PhoneButton onClick={() => respond("proceed", true)} disabled={busy || pin.length !== 4}>
            <span lang="bn">যাচাই করে পাঠান</span> · Verify and send
          </PhoneButton>
          <PhoneButton tone="light" onClick={() => respond("cancel")} disabled={busy}><span lang="bn">বাতিল করুন</span> · Cancel</PhoneButton>
          {reportButton}
        </div>
      </Screen>
    );
  } else if (status === "pending_customer") {
    screen = (
      <Screen tone="warn" title={<><span lang="bn">একটু থামুন</span> · Pause a moment</>}>
        {party}
        <Message text={decision?.customer_message} />
        <div className="mt-auto space-y-2">
          <PhoneButton onClick={() => respond("cancel")} disabled={busy}><span lang="bn">বাতিল করুন</span> · Cancel the payment</PhoneButton>
          <PhoneButton tone="light" onClick={() => respond("proceed")} disabled={busy}><span lang="bn">তবুও পাঠান</span> · Send anyway</PhoneButton>
          {reportButton}
        </div>
      </Screen>
    );
  } else if (status === "held") {
    screen = (
      <Screen tone="hold" title={<><span lang="bn">লেনদেন স্থগিত</span> · Payment paused</>}>
        {party}
        <Message text={decision?.customer_message} />
        {decision?.review_sla_minutes != null && (
          <p className="text-xs text-fg-3">Your money has not left your account. A person will review this within {decision.review_sla_minutes} minutes.</p>
        )}
        <div className="mt-auto">{reportButton}</div>
      </Screen>
    );
  } else {
    const sent = status === "completed";
    const title = sent
      ? <><span lang="bn">টাকা পাঠানো হয়েছে</span> · Sent</>
      : status === "cancelled"
        ? <><span lang="bn">বাতিল হয়েছে</span> · Cancelled</>
        : <><span lang="bn">লেনদেন হয়নি</span> · Not sent</>;
    screen = (
      <Screen tone={sent ? "allow" : "plain"} title={title}>
        {party}
        <p className="text-fg-2">
          {sent
            ? "The payment went through."
            : status === "cancelled"
              ? "Nothing was sent. Your money is still in your account."
              : `This payment was refused (${words(outcome?.status_reason ?? result.status_reason ?? status)}).`}
        </p>
        <div className="mt-auto space-y-2">
          {reportButton}
          <PhoneButton tone="light" onClick={() => start(payment, picked)}>New payment</PhoneButton>
        </div>
      </Screen>
    );
  }

  const waitMinutes = wait ? Math.min(60, Math.ceil(wait / 60)) : 0;

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
            <label>Amount (taka)<input className={`${inputClass} mt-1 w-full`} inputMode="decimal" value={custom.amount} maxLength={10} onChange={(e) => setCustom({ ...custom, amount: e.target.value })} /></label>
            <div className="flex items-end"><Button type="submit" disabled={busy}>Put on the phone</Button></div>
          </form>
        </Card>
      </div>

      <div>
        <div className="mx-auto h-[36rem] w-full max-w-[22rem] overflow-hidden rounded-[2.75rem] border-[10px] border-black bg-base shadow-2xl shadow-black/60 ring-1 ring-white/15">
          {screen}
        </div>
        {reported && (
          <p className="mt-3 text-center text-xs text-fg-2">
            <span lang="bn">অভিযোগ গ্রহণ করা হয়েছে।</span> Report received.{" "}
            {canReview ? <Link href={`/cases/${reported.case_id}`} className="text-info hover:underline">It is on case #{reported.case_id}</Link> : `It is on case #${reported.case_id}`}.
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
        <Card title="Platform clock" hint="The demo runs on simulated time, so a cooling-off period can be shown ending.">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>{when(now)} <span className="text-fg-3">Dhaka time</span></span>
            {waitMinutes > 0 ? (
              <Button small onClick={() => skip(waitMinutes)} disabled={busy}>Skip ahead {waitMinutes} minutes</Button>
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
          plays the customer, any four digits pass the PIN check, and every action is audited as a demo action.
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
        sub="What a customer sees when FraudLens interrupts a payment: a warning in Bangla, a second check with a cooling-off wait, or a pause for review, and a one-tap way to report a scam."
      />
      <Async state={scenarios}>{(data) => <Demo scenarios={data.scenarios} startedAt={data.now} />}</Async>
    </>
  );
}
