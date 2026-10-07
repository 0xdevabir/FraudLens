"use client";

import { useState } from "react";

import { Async, Badge, Button, Card, CategoryBadges, Empty, ErrorNote, Facts, inputClass, PageHeader, Stat, Table, Tabs, Td, type Tone } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { num, pct, taka, when, words } from "@/lib/format";
import type { CheckLevel, FraudCategory, FraudTaxonomy, MessageCheck, PaymentClaim, PaymentClaims, PaymentProof } from "@/lib/types";

type Tab = "coverage" | "message" | "payment";

const DETECTOR: Record<string, { label: string; tone: Tone; what: string }> = {
  transaction: { label: "Payment models", tone: "blue", what: "the transaction and recipient models, the rules and the payment graph" },
  text: { label: "Message classifier", tone: "violet", what: "the scam-message classifier, trained on English, Bangla and Banglish" },
  links: { label: "Link check", tone: "orange", what: "fixed checks on any link in a message; not a model" },
  ledger: { label: "Ledger check", tone: "green", what: "the payment is looked up in the recorded transactions" },
};

const LEVEL: Record<CheckLevel, { label: string; tone: Tone }> = {
  none: { label: "Nothing suspicious found", tone: "green" },
  caution: { label: "Be careful", tone: "amber" },
  high: { label: "Likely a scam", tone: "red" },
};

const LANGUAGE: Record<string, string> = { en: "English", bn: "Bangla", bl: "Banglish" };

/** Made-up messages to try. None is a real message from a real customer. */
const SAMPLES: [string, string][] = [
  ["Fake support call", "Sir ami upay head office theke bolchi. Apnar account block hoye jabe, ekhoni OTP code ta bolun."],
  ["Lottery fee", "অভিনন্দন! আপনি ১০ লক্ষ টাকার লটারি জিতেছেন। টাকা পেতে ২,৫০০ টাকা প্রসেসিং ফি এই নম্বরে পাঠান।"],
  ["Phishing link", "Your upay account will be suspended today. Verify your KYC now: http://upay-verify.xyz/login"],
  ["Fake payment", "Bhai vul kore apnar number e 5,000 taka chole geche, screenshot dilam. Taka ta ferot pathan please."],
  ["Job offer", "Work from home, earn 3,000 taka daily. Pay 500 taka registration fee to this number to start today."],
  ["Real OTP notice", "Your upay OTP is 482913. Never share your OTP or PIN with anyone, even upay staff."],
  ["Lunch plan", "Dupure ki khaba? Ami 1 tar dike ber hobo, office er niche theko."],
];

function rate(value: number | null | undefined, digits = 0): string {
  return value == null ? "–" : pct(value, digits);
}

function Coverage({ data }: { data: FraudTaxonomy }) {
  const report = data.report;
  return (
    <>
      {report ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Stat
            label="Scam messages caught"
            value={rate(report.test.with_link_check.recall, 1)}
            tone="good"
            sub={`new wording of scripts it was trained on, classifier with link check; ${num(report.test.scam)} scam messages`}
          />
          <Stat
            label="Scripts it never saw"
            value={rate(report.unseen.with_link_check.recall, 1)}
            tone="warn"
            sub={`${num(report.corpus.held_out_families)} whole scripts kept out of training; ${rate(report.unseen.caution.recall, 1)} by the classifier alone`}
          />
          <Stat
            label="Harmless messages flagged"
            value={rate(report.test.with_link_check.false_positive_rate, 2)}
            sub={`of ${num(report.test.harmless)} harmless messages, real OTP notices and receipts among them`}
          />
          <Stat
            label="Corpus"
            value={num(Object.values(report.corpus.messages).reduce((a, b) => a + b, 0))}
            sub={`synthetic messages from ${num(report.corpus.templates)} templates in ${num(report.corpus.families)} scripts`}
          />
        </div>
      ) : (
        <ErrorNote error="The scam-message classifier has not been trained yet (run `make intel`). The link check and the ledger check work without it." />
      )}

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        {data.categories.map((category) => <Category key={category.id} category={category} report={report} />)}
      </div>

      {report && (
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <Card title="By language" hint="Share of scam messages flagged, and of harmless ones flagged by mistake." flush>
            <Table head={["Language", "Caught, known scripts", "Flagged by mistake", "Caught, unseen scripts"]}>
              {Object.entries(report.test.languages).map(([lang, row]) => (
                <tr key={lang}>
                  <Td>{LANGUAGE[lang] ?? lang}</Td>
                  <Td right>{rate(row.recall, 1)}</Td>
                  <Td right>{rate(row.false_positive_rate, 2)}</Td>
                  <Td right>{rate(report.unseen.languages[lang]?.recall, 1)}</Td>
                </tr>
              ))}
            </Table>
          </Card>
          <Card title="Scripts held out of training" hint="Share of each script's messages the classifier flagged without ever seeing that script." flush>
            <Table head={["Script", "Kind", "Messages", "Flagged"]}>
              {Object.entries(report.unseen.families ?? {}).map(([family, row]) => (
                <tr key={family}>
                  <Td>{words(family)}</Td>
                  <Td>{row.scam ? "scam" : "harmless"}</Td>
                  <Td right>{num(row.messages)}</Td>
                  <Td right>{rate(row.flagged, 0)}</Td>
                </tr>
              ))}
            </Table>
          </Card>
        </div>
      )}

      <Card className="mt-4" title="What this does not show">
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-fg-2">
          {(report?.limits ?? []).map((limit) => <li key={limit}>{limit}</li>)}
          <li>The category on an alert is read from evidence already on it (the scenario, similar past cases, the mule score). It is a label for the reviewer and changes no decision.</li>
          <li>Text a customer sends to be checked is scored and thrown away. Only the level and the category names are kept in the audit log.</li>
        </ul>
      </Card>
    </>
  );
}

function Category({ category, report }: { category: FraudCategory; report: FraudTaxonomy["report"] }) {
  const known = report?.test.categories[category.id];
  const unseen = report?.unseen.categories[category.id];
  const reads = category.detectors.includes("text");
  return (
    <Card
      title={`${category.number}. ${category.name.en}`}
      hint={category.name.bn}
      actions={<span className="flex flex-wrap justify-end gap-1">{category.detectors.map((id) => (
        <Badge key={id} tone={DETECTOR[id]?.tone ?? "slate"} title={DETECTOR[id]?.what}>{DETECTOR[id]?.label ?? words(id)}</Badge>
      ))}</span>}
    >
      <p className="text-sm text-fg">{category.summary}</p>
      <p className="mt-2 flex flex-wrap gap-1">{category.examples.map((example) => <Badge key={example}>{example}</Badge>)}</p>

      <h3 className="mt-4 text-xs font-semibold tracking-wide text-fg-3 uppercase">In Bangladesh</h3>
      <p className="mt-1 text-sm text-fg-2">{category.bangladesh}</p>

      <h3 className="mt-4 text-xs font-semibold tracking-wide text-fg-3 uppercase">What FraudLens looks for</h3>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-fg-2">
        {category.signals.map((signal) => <li key={signal}>{signal}</li>)}
      </ul>

      <h3 className="mt-4 text-xs font-semibold tracking-wide text-fg-3 uppercase">What the customer is told</h3>
      <p className="mt-1 text-sm text-fg-2">{category.advice.en}</p>
      <p className="mt-1 text-sm text-fg-2" lang="bn">{category.advice.bn}</p>

      {reads && known && (
        <div className="mt-4 border-t border-line pt-3">
          <Facts
            rows={[
              ["Named correctly, known scripts", `${rate(known.recall)} of ${num(known.support)} messages, ${rate(known.precision)} of the times it says so are right`],
              ["Named correctly, unseen scripts", unseen?.support ? `${rate(unseen.recall)} of ${num(unseen.support)} messages` : "no held-out script in this category"],
            ]}
          />
        </div>
      )}
    </Card>
  );
}

function CheckResult({ found }: { found: MessageCheck }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={LEVEL[found.level].tone}>{LEVEL[found.level].label}</Badge>
        <span className="text-xs text-fg-3">
          {found.risk == null ? "classifier not loaded: links only" : `scam score ${num(found.risk, 3)} · model ${found.model_version}`}
        </span>
      </div>
      <Facts
        rows={[
          ["Fraud type", <CategoryBadges key="c" categories={found.categories} />],
          [
            "Why",
            found.signals.length ? (
              <ul key="s" className="list-disc space-y-0.5 pl-4">
                {found.signals.map((signal) => <li key={signal.id} title={signal.label.bn}>{signal.label.en}</li>)}
              </ul>
            ) : "no known scam wording matched",
          ],
          [
            "Links",
            found.links.length ? (
              <ul key="l" className="space-y-1">
                {found.links.map((link) => (
                  <li key={link.host} className="flex flex-wrap items-center gap-1">
                    <code className="text-xs">{link.host}</code>
                    {link.flags.length
                      ? link.flags.map((flag) => <Badge key={flag} tone={flag === "official" ? "green" : link.level === "high" ? "red" : "amber"}>{words(flag)}</Badge>)
                      : <Badge>nothing known against it</Badge>}
                  </li>
                ))}
              </ul>
            ) : "none in the message",
          ],
        ]}
      />
      {found.advice && (
        <div className="rounded-xl border border-line bg-white/4 px-3 py-2 text-sm">
          <div className="text-xs font-medium text-fg-3">Shown to the customer</div>
          <p className="mt-1 text-fg">{found.advice.en}</p>
          <p className="mt-1 text-fg-2" lang="bn">{found.advice.bn}</p>
        </div>
      )}
    </div>
  );
}

/** Run one request and keep its answer or its error; a second press while it runs is ignored. */
function useAsk<T>() {
  const [state, setState] = useState<{ busy: boolean; data?: T; error?: Error }>({ busy: false });
  const ask = (path: string, body: unknown) => {
    if (state.busy) return;
    setState({ busy: true });
    api<T>(path, body).then(
      (data) => setState({ busy: false, data }),
      (error: Error) => setState({ busy: false, error }),
    );
  };
  return { ...state, ask };
}

function MessageTab({ demoWallet }: { demoWallet: string }) {
  // Until it is typed over, the asking wallet is the demo's: a wallet the platform knows.
  const [typed, setWallet] = useState<string | null>(null);
  const wallet = typed ?? demoWallet;
  const [text, setText] = useState("");
  const check = useAsk<MessageCheck>();
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Is this message a scam?" hint="What the app's “check a message” answers. The text is scored and not stored.">
        <div className="space-y-3 text-sm">
          <label className="block text-fg-3">
            Asking wallet
            <input className={`${inputClass} mt-1 w-full`} value={wallet} maxLength={32} onChange={(e) => setWallet(e.target.value)} />
          </label>
          <label className="block text-fg-3">
            Message, in English, Bangla or Banglish
            <textarea
              className={`${inputClass} mt-1 h-36 w-full resize-y`}
              value={text}
              maxLength={2000}
              onChange={(e) => setText(e.target.value)}
              placeholder="Paste an SMS, a chat message or what a caller said."
            />
          </label>
          <div className="flex flex-wrap gap-1.5">
            {SAMPLES.map(([label, sample]) => <Button key={label} small onClick={() => setText(sample)}>{label}</Button>)}
          </div>
          <Button
            variant="primary"
            disabled={check.busy || !text.trim() || !wallet.trim()}
            onClick={() => check.ask("/v1/demo/message-check", { wallet_id: wallet.trim(), text })}
          >
            {check.busy ? "Checking…" : "Check the message"}
          </Button>
        </div>
      </Card>
      <Card title="Answer">
        {check.error ? <ErrorNote error={check.error} /> : check.data ? <CheckResult found={check.data} /> : <Empty>Paste a message, or pick one of the examples.</Empty>}
      </Card>
    </div>
  );
}

const PROOF: Record<PaymentProof["status"], { label: string; tone: Tone }> = {
  verified: { label: "The money arrived", tone: "green" },
  mismatch: { label: "Does not match the ledger", tone: "amber" },
  not_found: { label: "No such payment to this wallet", tone: "red" },
};

const CLAIM: Record<string, string> = {
  real: "A payment that arrived",
  edited_amount: "Same payment, amount edited",
  forged_sms: "A forged cash-in SMS",
};

function PaymentTab({ claims }: { claims: PaymentClaim[] }) {
  const [form, setForm] = useState({ wallet: "", txn: "", amount: "", message: "" });
  const proof = useAsk<PaymentProof>();
  const amount = Number(form.amount);
  const ready = form.wallet.trim() && (form.txn.trim() || form.message.trim() || amount > 0) && (!form.amount || amount > 0);
  const set = (field: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [field]: e.target.value });
  const found = proof.data;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card
        title="Did this payment really arrive?"
        hint="A forged screenshot or SMS looks exactly like a real one, so the answer comes from the ledger, not from the proof."
      >
        <div className="grid grid-cols-2 gap-3 text-sm text-fg-3">
          <label className="col-span-2">
            Wallet that was told it received money
            <input className={`${inputClass} mt-1 w-full`} value={form.wallet} maxLength={32} onChange={set("wallet")} />
          </label>
          <label>
            Transaction ID
            <input className={`${inputClass} mt-1 w-full`} value={form.txn} maxLength={32} onChange={set("txn")} />
          </label>
          <label>
            Amount (taka)
            <input className={`${inputClass} mt-1 w-full`} inputMode="decimal" value={form.amount} maxLength={10} onChange={set("amount")} />
          </label>
          <label className="col-span-2">
            Or the message the customer was sent
            <textarea
              className={`${inputClass} mt-1 h-24 w-full resize-y`}
              value={form.message}
              maxLength={2000}
              onChange={set("message")}
              placeholder="Cash In Tk 2,500.00 from 01XXXXXXXXX successful. TrxID 9001…"
            />
          </label>
          {claims.length > 0 && (
            <div className="col-span-2">
              <div className="mb-1.5 text-xs">Ready to try, from the newest completed payment in the ledger</div>
              <div className="flex flex-wrap gap-1.5">
                {claims.map((claim) => (
                  <Button
                    key={claim.id}
                    small
                    title={`the check answers: ${words(claim.expects)}`}
                    onClick={() => setForm({ wallet: claim.wallet_id, txn: claim.txn_id ?? "", amount: claim.amount == null ? "" : String(claim.amount), message: claim.message })}
                  >
                    {CLAIM[claim.id] ?? words(claim.id)}
                  </Button>
                ))}
              </div>
            </div>
          )}
          <div className="col-span-2">
            <Button
              variant="primary"
              disabled={proof.busy || !ready}
              onClick={() =>
                proof.ask("/v1/demo/payment-verify", {
                  wallet_id: form.wallet.trim(),
                  txn_id: form.txn.trim() || null,
                  amount: form.amount ? amount : null,
                  message: form.message,
                })
              }
            >
              {proof.busy ? "Looking it up…" : "Verify against the ledger"}
            </Button>
          </div>
        </div>
      </Card>
      <Card title="Answer">
        {proof.error ? <ErrorNote error={proof.error} /> : !found ? (
          <Empty>Only the receiving wallet can verify a payment. Anyone else gets the same answer as for a payment that does not exist.</Empty>
        ) : (
          <div className="space-y-3">
            <Badge tone={PROOF[found.status].tone}>{PROOF[found.status].label}</Badge>
            <Facts
              rows={[
                ["Claimed", `${found.claimed.txn_id == null ? "no usable ID" : `#${found.claimed.txn_id}`} · ${found.claimed.amount == null ? "no amount" : taka(found.claimed.amount)}`],
                ...Object.entries(found.checks).map(([name, ok]): [string, React.ReactNode] => [
                  words(name),
                  <Badge key={name} tone={ok ? "green" : "red"}>{ok ? "yes" : "no"}</Badge>,
                ]),
                ...(found.transaction
                  ? ([
                      ["In the ledger", `#${found.transaction.txn_id} · ${taka(found.transaction.amount)} · ${words(found.transaction.status)}`],
                      ["Recorded", `${when(found.transaction.ts)} · ${words(found.transaction.type)} from ${found.transaction.sender_id}`],
                    ] as [string, string][])
                  : []),
              ]}
            />
            <div className="rounded-xl border border-line bg-white/4 px-3 py-2 text-sm">
              <div className="text-xs font-medium text-fg-3">Shown to the customer</div>
              <p className="mt-1 text-fg">{found.message.en}</p>
              <p className="mt-1 text-fg-2" lang="bn">{found.message.bn}</p>
            </div>
            {found.text && (
              <div className="border-t border-line pt-3">
                <div className="mb-2 text-xs font-medium text-fg-3">The pasted message, read as a message</div>
                <CheckResult found={found.text} />
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

export default function FraudTypesPage() {
  const [tab, setTab] = useState<Tab>("coverage");
  const taxonomy = useApi<FraudTaxonomy>("/v1/intel/taxonomy");
  // What the two try-it tabs start from. They work without it, with the fields typed by hand.
  const demo = useApi<PaymentClaims>("/v1/demo/payment-claims").data;
  return (
    <>
      <PageHeader tour="fraud-types-header"
        title="Fraud types"
        sub="The eight kinds of fraud wallet customers in Bangladesh meet, what detects each one here, and how well, measured."
      />
      <Tabs
        tabs={[
          { id: "coverage", label: "Coverage" },
          { id: "message", label: "Check a message" },
          { id: "payment", label: "Verify a payment" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "coverage" && <Async state={taxonomy}>{(data) => <Coverage data={data} />}</Async>}
      {tab === "message" && <MessageTab demoWallet={demo?.wallet_id ?? ""} />}
      {tab === "payment" && <PaymentTab claims={demo?.claims ?? []} />}
    </>
  );
}
