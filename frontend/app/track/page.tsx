"use client";

import { useState } from "react";

import { Button, ErrorNote, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import { day } from "@/lib/format";
import type { ReportStatus } from "@/lib/types";

type Lang = "bn" | "en";

const COPY = {
  bn: {
    title: "আপনার অভিযোগের অবস্থা",
    sub: "অভিযোগ করার সময় পাওয়া রেফারেন্স নম্বর দিন। আমরা অভিযোগকারীর নম্বরে একটি কোড পাঠাব।",
    reference: "রেফারেন্স নম্বর",
    send: "কোড পাঠান",
    sent: "কোড পাঠানো হয়েছে (যদি রেফারেন্সটি সঠিক হয়)। ১০ মিনিটের মধ্যে কোডটি দিন।",
    code: "৬ অঙ্কের কোড",
    check: "অবস্থা দেখুন",
    again: "আবার কোড পাঠান",
    reported: "অভিযোগের তারিখ",
    updated: "সর্বশেষ হালনাগাদ",
    warn: "পিন বা ওটিপি কাউকে দেবেন না। উপায় কখনো টাকা পাঠাতে বলে না।",
  },
  en: {
    title: "Follow your report",
    sub: "Enter the reference you were given when you reported. We will send a code to the phone that made the report.",
    reference: "Reference",
    send: "Send me a code",
    sent: "If the reference is right, a code has been sent. Enter it within 10 minutes.",
    code: "6-digit code",
    check: "Show status",
    again: "Send a new code",
    reported: "Reported on",
    updated: "Last updated",
    warn: "Never share your PIN or OTP. upay will never ask you to send money.",
  },
} as const;

const STEP: Record<ReportStatus["status"], number> = { received: 1, investigating: 2, action_taken: 3, closed: 3 };

export default function TrackPage() {
  const [lang, setLang] = useState<Lang>("bn");
  const [reference, setReference] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [status, setStatus] = useState<ReportStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const t = COPY[lang];

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (problem) {
      setError(problem as Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="relative grid min-h-screen place-items-center overflow-hidden px-4 py-10">
      <div aria-hidden="true" className="pointer-events-none absolute -top-40 left-1/2 size-[36rem] -translate-x-1/2 rounded-full bg-accent/20 blur-[120px]" />
      <div className="relative w-full max-w-md animate-rise">
        <div className="mb-3 flex justify-end gap-1.5">
          <button type="button" aria-pressed={lang === "bn"} onClick={() => setLang("bn")} className={`rounded-full px-3 py-1 text-xs font-medium ${lang === "bn" ? "bg-accent text-accent-ink" : "bg-white/8 text-fg-2"}`}>বাংলা</button>
          <button type="button" aria-pressed={lang === "en"} onClick={() => setLang("en")} className={`rounded-full px-3 py-1 text-xs font-medium ${lang === "en" ? "bg-accent text-accent-ink" : "bg-white/8 text-fg-2"}`}>English</button>
        </div>
        <div className="space-y-4 rounded-3xl border border-white/12 bg-white/6 p-6 shadow-2xl shadow-black/40 backdrop-blur-2xl" lang={lang}>
          <h1 className="text-2xl font-bold tracking-tight text-fg">{t.title}</h1>
          {!status && <p className="text-sm text-fg-3">{t.sub}</p>}

          {!status && (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                if (!sent) {
                  void run(async () => {
                    await api("/v1/public/reports/code", { reference: reference.trim() });
                    setSent(true);
                  });
                } else {
                  void run(async () => setStatus(await api<ReportStatus>("/v1/public/reports/status", { reference: reference.trim(), code: code.trim() })));
                }
              }}
            >
              <label className="block text-xs font-medium text-fg-3">
                {t.reference}
                <input
                  autoFocus autoComplete="off" autoCapitalize="characters" spellCheck={false} maxLength={20} placeholder="FL-XXXX-XXXX"
                  className={`${inputClass} mt-1 block w-full font-mono`} value={reference} disabled={sent}
                  onChange={(event) => setReference(event.target.value)}
                />
              </label>
              {sent && (
                <>
                  <p className="text-sm text-fg-2">{t.sent}</p>
                  <label className="block text-xs font-medium text-fg-3">
                    {t.code}
                    <input
                      autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="[0-9]{6}"
                      className={`${inputClass} mt-1 block w-full font-mono tracking-[0.4em]`} value={code}
                      onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                    />
                  </label>
                </>
              )}
              {error && <ErrorNote error={error} />}
              <div className="flex flex-wrap gap-2">
                <Button type="submit" variant="primary" disabled={busy || reference.trim().length < 6 || (sent && code.length !== 6)}>
                  {sent ? t.check : t.send}
                </Button>
                {sent && <Button disabled={busy} onClick={() => { setSent(false); setCode(""); }}>{t.again}</Button>}
              </div>
            </form>
          )}

          {status && (
            <div className="space-y-4">
              <div className="font-mono text-xs text-fg-3">{status.reference}</div>
              <ol className="flex gap-1.5" aria-label="progress">
                {[1, 2, 3].map((step) => (
                  <li key={step} className={`h-1.5 flex-1 rounded-full ${step <= STEP[status.status] ? "bg-accent" : "bg-white/12"}`} />
                ))}
              </ol>
              <div>
                <h2 className="text-lg font-semibold text-fg">{status.title[lang]}</h2>
                <p className="mt-1 text-sm text-fg-2">{status.detail[lang]}</p>
              </div>
              <dl className="grid grid-cols-2 gap-2 text-xs text-fg-3">
                <div><dt>{t.reported}</dt><dd className="text-fg-2">{day(status.reported_at)}</dd></div>
                {status.updated_at && <div><dt>{t.updated}</dt><dd className="text-fg-2">{day(status.updated_at)}</dd></div>}
              </dl>
            </div>
          )}
          <p className="border-t border-white/10 pt-3 text-xs text-fg-4">{t.warn}</p>
        </div>
      </div>
    </main>
  );
}
