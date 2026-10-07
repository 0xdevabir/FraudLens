"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { Id, useSession } from "@/components/session";
import { AlertTable, FreezeList } from "@/components/tables";
import { Async, Badge, Button, Card, CategoryBadges, Empty, ErrorNote, Facts, inputClass, PageHeader, ReasonDialog, SlaBadge, StatusBadge, TierBadge } from "@/components/ui";
import { FreezeButton, NetworkCard, RiskProfile } from "@/components/wallet";
import { api, useApi } from "@/lib/api";
import { taka, when, words } from "@/lib/format";
import type { CaseDetail, CaseEvent, CaseRefunds, Me, Refund } from "@/lib/types";

type Verdict = "confirmed_fraud" | "legitimate" | "inconclusive";

/** Why a reviewer called an alert a false alarm. Read in the feedback view; nothing trains on it. */
const REASONS: [string, string][] = [
  ["known_recipient", "The sender knows the recipient"],
  ["family_transfer", "Family transfer or remittance"],
  ["merchant", "A genuine seller or merchant"],
  ["new_phone", "The customer changed phone"],
  ["other", "Something else"],
];

const VERDICTS: Record<Verdict, { button: string; variant: "danger" | "good" | "primary"; intro: string }> = {
  confirmed_fraud: {
    button: "Confirm fraud",
    variant: "danger",
    intro: "Every payment still waiting in this case is blocked, the wallet is marked as confirmed fraud, and the verdict becomes a training label.",
  },
  legitimate: {
    button: "False positive",
    variant: "good",
    intro: "Held payments in this case are released to the receiver, and the verdict becomes a training label.",
  },
  inconclusive: {
    button: "Inconclusive",
    variant: "primary",
    intro: "Held payments are released. The case closes without a label, so it does not train the model.",
  },
};

function eventText(event: CaseEvent): string {
  const data = event.data;
  switch (event.kind) {
    case "opened": return data.txn_id ? `Case opened by a ${words(String(data.tier ?? "")).toLowerCase()} decision on payment #${data.txn_id}` : "Case opened by hand";
    case "alert_added": return `Payment #${data.txn_id} added (${words(String(data.tier ?? "alert")).toLowerCase()})`;
    case "assigned": return `Assigned to ${data.assignee}`;
    case "note": return "Note";
    case "escalated": return "Escalated to a supervisor";
    case "sla_breached": return "Review deadline missed";
    case "verdict": {
      const released = (data.released as number[] | undefined)?.length ?? 0;
      const blocked = (data.blocked as number[] | undefined)?.length ?? 0;
      return `Verdict: ${words(String(data.verdict))}${data.reason_code ? ` (${words(String(data.reason_code)).toLowerCase()})` : ""}${blocked ? ` · ${blocked} payment${blocked > 1 ? "s" : ""} blocked` : ""}${released ? ` · ${released} released` : ""}`;
    }
    case "freeze_requested": return data.for_refunds ? "Freeze requested, so the victims can be refunded" : "Freeze requested";
    case "freeze_approved": return "Freeze approved: the wallet is frozen";
    case "freeze_rejected": return "Freeze rejected";
    case "customer_report": return "A customer reported this wallet";
    case "refund_claimed": return `Refund claimed for payment #${data.txn_id} (${taka(Number(data.amount))})`;
    case "refund_paid": return `Refunded ${taka(Number(data.amount))} for payment #${data.txn_id}`;
    case "refund_unrecoverable": return `Nothing left to refund for payment #${data.txn_id}`;
    case "refund_declined":
      return `Refund declined for payment #${data.txn_id}: ${data.outcome === "not_a_victim" ? "not a victim" : "fraud not confirmed"}`;
    case "customer_response":
      return data.action === "cancel" ? `The customer cancelled payment #${data.txn_id}` : `The customer went ahead with payment #${data.txn_id}`;
    default: return words(event.kind);
  }
}

function Activity({ events }: { events: CaseEvent[] }) {
  return (
    <ol className="space-y-3 border-l border-line pl-4 text-sm">
      {events.map((event) => (
        <li key={event.id} className="relative">
          <span className="absolute top-1.5 -left-[21px] size-2 rounded-full bg-fg-4" />
          <div className="font-medium text-fg">{eventText(event)}</div>
          {event.body && <p className="whitespace-pre-line text-fg-2">{event.body}</p>}
          <div className="text-xs text-fg-3">{event.actor ?? "system"} · recorded {when(event.at)}</div>
        </li>
      ))}
    </ol>
  );
}

function NoteBox({ caseId, onDone }: { caseId: number; onDone: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/v1/cases/${caseId}/notes`, { body: text.trim() });
      setText("");
      onDone();
    } catch (problem) {
      setError(problem as Error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="mt-4 space-y-2">
      <label htmlFor="note" className="block text-xs font-medium text-fg-2">Add a note</label>
      <textarea id="note" rows={2} maxLength={4000} value={text} onChange={(event) => setText(event.target.value)} className={`${inputClass} w-full`} />
      {error && <ErrorNote error={error} />}
      <Button type="submit" small variant="primary" disabled={!text.trim() || busy}>{busy ? "Saving…" : "Save note"}</Button>
    </form>
  );
}

function Assign({ data, onDone }: { data: CaseDetail; onDone: () => void }) {
  const { me, canApprove } = useSession();
  const reviewers = useApi<Me[]>(canApprove ? "/v1/users/reviewers" : null);
  const [error, setError] = useState<Error | null>(null);
  async function assign(assigneeId: number) {
    setError(null);
    try {
      await api(`/v1/cases/${data.id}/assign`, { assignee_id: assigneeId });
      onDone();
    } catch (problem) {
      setError(problem as Error);
    }
  }
  return (
    <>
      {data.assigned_to !== me.id && <Button small onClick={() => assign(me.id)}>Take this case</Button>}
      {canApprove && reviewers.data && (
        <select
          aria-label="Assign to"
          className={`${inputClass} py-1 text-xs`}
          value=""
          onChange={(event) => event.target.value && assign(Number(event.target.value))}
        >
          <option value="">Assign to…</option>
          {reviewers.data.filter((user) => user.id !== data.assigned_to).map((user) => (
            <option key={user.id} value={user.id}>{user.display_name}</option>
          ))}
        </select>
      )}
      {error && <ErrorNote error={error} />}
    </>
  );
}

function Step({ done, children }: { done: boolean; children: React.ReactNode }) {
  return (
    <li className="flex gap-2">
      <span aria-hidden="true" className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full text-[11px] font-bold ${done ? "bg-good text-accent-ink" : "border border-line text-fg-3"}`}>
        {done ? "✓" : ""}
      </span>
      <span className={done ? "text-fg" : "text-fg-2"}>{children}</span>
    </li>
  );
}

/** The victims who reported a payment into this wallet, and what a verdict will give back. */
function Refunds({ data, reload }: { data: CaseDetail; reload: () => void }) {
  const { canReview } = useSession();
  const [declining, setDeclining] = useState<Refund | null>(null);
  const { claims, recoverable, wallet_frozen: frozen }: CaseRefunds = data.refunds;
  const open = claims.filter((claim) => claim.status === "open");
  const owed = open.reduce((sum, claim) => sum + claim.amount_claimed, 0);
  const confirmed = data.verdict === "confirmed_fraud";
  const pendingFreeze = data.freeze_requests.some((request) => request.status === "pending");
  const cover = owed > 0 ? Math.min(1, recoverable / owed) : 1;
  return (
    <Card title="Victim refunds" hint="Paid from what is still in the wallet, once a person confirms the fraud and two people have frozen it.">
      {claims.length ? (
        <>
          <ol className="space-y-1.5 text-sm" aria-label="Refund steps">
            <Step done>{claims.length} reported payment{claims.length > 1 ? "s" : ""}{open.length ? `, ${taka(owed)} still owed` : ""}</Step>
            <Step done={frozen}>{frozen ? "Wallet frozen: nothing more can be cashed out" : pendingFreeze ? "Freeze waiting for a second person" : "Freeze the wallet to protect the money"}</Step>
            <Step done={confirmed}>{confirmed ? "Fraud confirmed" : data.verdict ? `Closed as ${words(data.verdict).toLowerCase()}: no refund` : "Confirm the fraud with a verdict"}</Step>
          </ol>
          {open.length > 0 && (
            <p className="mt-3 rounded-xl border border-line bg-wash px-3 py-2 text-sm">
              <span className="font-semibold">{taka(recoverable)}</span> is still in the wallet:{" "}
              {cover >= 1 ? "enough to refund every open claim in full." : `each victim would get ${Math.floor(cover * 100)}% of what they lost.`}
            </p>
          )}
          {canReview && open.length > 0 && !frozen && !pendingFreeze && data.status !== "closed" && (
            <div className="mt-3"><FreezeButton small walletId={data.subject_id} caseId={data.id} onDone={reload} /></div>
          )}
          <ul className="mt-3 divide-y divide-line text-sm">
            {claims.map((claim) => (
              <li key={claim.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <Id value={claim.victim_id} caseId={data.id} /> lost {taka(claim.amount_claimed)}
                  <span className="block text-xs text-fg-3">
                    payment #{claim.txn_id}
                    {claim.status === "paid" && ` · ${taka(claim.amount_refunded ?? 0)} refunded ${when(claim.settled_at)}`}
                    {claim.status === "open" && ` · promised by ${when(claim.sla_due_at)}`}
                    {claim.note && ` · ${claim.note}`}
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  <StatusBadge status={claim.status} />
                  {canReview && claim.status === "open" && <Button small onClick={() => setDeclining(claim)}>Not a victim</Button>}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <Empty>No victim has claimed a refund. A customer who reports a payment into this wallet claims one.</Empty>
      )}
      {declining && (
        <ReasonDialog
          title={`Decline the refund for payment #${declining.txn_id}`}
          intro="Take this claimant out: they look like part of the scheme, not a victim of it. No money moves, and the others' shares grow."
          confirm="Decline refund"
          variant="danger"
          onClose={() => setDeclining(null)}
          onSubmit={async (note) => {
            await api(`/v1/refunds/${declining.id}/decline`, { note });
            reload();
          }}
        />
      )}
    </Card>
  );
}

function verdictIntro(verdict: Verdict, data: CaseDetail): string {
  const open = data.refunds.claims.filter((claim) => claim.status === "open").length;
  if (!open) return VERDICTS[verdict].intro;
  const victims = open > 1 ? `${open} victims are` : "1 victim is";
  if (verdict !== "confirmed_fraud") return `${VERDICTS[verdict].intro} No refund is paid: ${open > 1 ? `${open} refund claims are` : "the refund claim is"} declined.`;
  return data.refunds.wallet_frozen
    ? `${VERDICTS[verdict].intro} ${victims} refunded now from the ${taka(data.refunds.recoverable)} left in the frozen wallet.`
    : `${VERDICTS[verdict].intro} A freeze is requested for you; ${victims} refunded as soon as a second person approves it.`;
}

function Detail({ data, reload }: { data: CaseDetail; reload: () => void }) {
  const { me, canReview, canApprove } = useSession();
  const [dialog, setDialog] = useState<Verdict | "escalate" | null>(null);
  const [reason, setReason] = useState("");
  const open = data.status !== "closed";
  const mayDecide = canReview && open && (canApprove || (data.status !== "escalated" && (data.assigned_to == null || data.assigned_to === me.id)));
  const waiting = data.alerts.filter((alert) => alert.status === "held" || alert.status === "pending_customer");

  return (
    <>
      <PageHeader tour="case-header"
        title={<span className="flex flex-wrap items-center gap-2">Case #{data.id} <StatusBadge status={data.status} /> <TierBadge tier={data.priority} /> {data.overdue && <Badge tone="red">review overdue</Badge>}</span>}
        sub={<>About wallet <Id value={data.subject_id} caseId={data.id} /> · opened {when(data.opened_at)} · {data.source === "manual" ? "opened by a reviewer" : data.source === "customer_report" ? "opened by a customer report" : "opened by an alert"}</>}
      />

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card
            title="Payments in this case"
            hint={waiting.length ? `${waiting.length} still waiting, ${taka(waiting.reduce((sum, alert) => sum + alert.amount, 0))} in total. Times are when the payment was made.` : "Times are when the payment was made."}
            flush
          >
            <AlertTable alerts={data.alerts} showCase={false} />
          </Card>
          <Card title="Wallet profile" hint="What the wallet has done, and what the mule model sees.">
            <RiskProfile risk={data.subject} />
          </Card>
          <NetworkCard walletId={data.subject_id} threshold={data.subject.mule_threshold} />
        </div>

        <div className="space-y-4">
          <Card tour="case-decision" title="Decision" hint="A person closes every case. The model only ranks and explains.">
            <Facts
              rows={[
                ["Assigned to", data.assignee ?? "nobody"],
                ["Review due", data.sla_due_at ? <span key="d">{when(data.sla_due_at)} <SlaBadge state={data.sla_state} seconds={data.sla_remaining_seconds} /></span> : "no deadline"],
                ["Verdict", data.verdict ? <StatusBadge key="v" status={data.verdict} /> : "not yet"],
                ...(data.reason_code ? [["Why a false alarm", words(data.reason_code)] as [string, string]] : []),
                ...(data.closed_at ? [["Closed", `${when(data.closed_at)} by ${data.closer ?? "–"}`] as [string, string]] : []),
              ]}
            />
            {canReview && open && (
              <div className="mt-4 space-y-3">
                <div className="flex flex-wrap items-center gap-2"><Assign data={data} onDone={reload} /></div>
                <div className="flex flex-wrap gap-2">
                  {(Object.keys(VERDICTS) as Verdict[]).map((verdict) => (
                    <Button key={verdict} variant={VERDICTS[verdict].variant} disabled={!mayDecide} onClick={() => { setReason(""); setDialog(verdict); }}>
                      {VERDICTS[verdict].button}
                    </Button>
                  ))}
                </div>
                {!mayDecide && (
                  <p className="text-xs text-fg-3">
                    {data.status === "escalated" ? "An escalated case needs a supervisor's verdict." : "The case is assigned to someone else."}
                  </p>
                )}
                <div className="flex flex-wrap gap-2">
                  {data.status !== "escalated" && <Button onClick={() => setDialog("escalate")}>Escalate</Button>}
                  <FreezeButton walletId={data.subject_id} caseId={data.id} onDone={reload} />
                </div>
              </div>
            )}
          </Card>

          <Refunds data={data} reload={reload} />

          <Card title="Case activity" hint="Recorded on the server clock, in the order it happened.">
            {data.timeline.length ? <Activity events={data.timeline} /> : <Empty>Nothing has happened yet.</Empty>}
            {canReview && <NoteBox caseId={data.id} onDone={reload} />}
          </Card>

          <Card title="Freeze requests"><FreezeList rows={data.freeze_requests} /></Card>

          <Card title="Customer reports" hint="Sent from the app with one tap.">
            {data.customer_reports.length ? (
              <ul className="divide-y divide-line text-sm">
                {data.customer_reports.map((report) => (
                  <li key={report.id} className="py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="amber">{words(report.category)}</Badge>
                      <span className="text-fg-2">from <Id value={report.reporter_id} caseId={data.id} /> · {when(report.reported_at)}</span>
                    </div>
                    {report.description && <p className="mt-1 text-fg">{report.description}</p>}
                    {report.fraud_categories?.length > 0 && (
                      <p className="mt-1 text-xs text-fg-3">Looks like: <CategoryBadges categories={report.fraud_categories} /></p>
                    )}
                    {report.txn_id && <p className="text-xs text-fg-3">about payment #{report.txn_id}</p>}
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No customer has reported this wallet.</Empty>
            )}
          </Card>
        </div>
      </div>

      {dialog === "escalate" && (
        <ReasonDialog
          title={`Escalate case #${data.id}`}
          intro="The case goes to a supervisor, who gives the verdict. Held payments stay held."
          confirm="Escalate"
          onClose={() => setDialog(null)}
          onSubmit={async (reason) => {
            await api(`/v1/cases/${data.id}/escalate`, { reason });
            reload();
          }}
        />
      )}
      {dialog && dialog !== "escalate" && (
        <ReasonDialog
          title={`${VERDICTS[dialog].button}: case #${data.id}`}
          label="What you found"
          intro={verdictIntro(dialog, data)}
          confirm={VERDICTS[dialog].button}
          variant={VERDICTS[dialog].variant}
          onClose={() => setDialog(null)}
          onSubmit={async (note) => {
            await api(`/v1/cases/${data.id}/verdict`, { verdict: dialog, note, ...(dialog === "legitimate" && reason ? { reason_code: reason } : {}) });
            reload();
          }}
        >
          {dialog === "legitimate" && (
            <label className="block text-xs font-medium text-fg-2">
              Why was it a false alarm? <span className="font-normal text-fg-3">(optional)</span>
              <select className={`${inputClass} mt-1 block w-full`} value={reason} onChange={(event) => setReason(event.target.value)}>
                <option value="">Not given</option>
                {REASONS.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
              </select>
            </label>
          )}
        </ReasonDialog>
      )}
    </>
  );
}

export default function CasePage() {
  const { id } = useParams<{ id: string }>();
  const valid = /^\d+$/.test(id);
  const detail = useApi<CaseDetail>(valid ? `/v1/cases/${id}` : null);
  if (!valid) return <Empty>That is not a case number.</Empty>;
  return <Async state={detail}>{(data) => <Detail data={data} reload={detail.reload} />}</Async>;
}
