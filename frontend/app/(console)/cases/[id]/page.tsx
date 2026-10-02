"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { Id, useSession } from "@/components/session";
import { AlertTable, FreezeList } from "@/components/tables";
import { Async, Badge, Button, Card, Empty, ErrorNote, Facts, inputClass, PageHeader, ReasonDialog, StatusBadge, TierBadge } from "@/components/ui";
import { FreezeButton, NetworkCard, RiskProfile } from "@/components/wallet";
import { api, useApi } from "@/lib/api";
import { taka, when, words } from "@/lib/format";
import type { CaseDetail, CaseEvent, Me } from "@/lib/types";

type Verdict = "confirmed_fraud" | "legitimate" | "inconclusive";

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
    case "verdict": {
      const released = (data.released as number[] | undefined)?.length ?? 0;
      const blocked = (data.blocked as number[] | undefined)?.length ?? 0;
      return `Verdict: ${words(String(data.verdict))}${blocked ? ` · ${blocked} payment${blocked > 1 ? "s" : ""} blocked` : ""}${released ? ` · ${released} released` : ""}`;
    }
    case "freeze_requested": return "Freeze requested";
    case "freeze_approved": return "Freeze approved: the wallet is frozen";
    case "freeze_rejected": return "Freeze rejected";
    case "customer_report": return "A customer reported this wallet";
    case "customer_response":
      return data.action === "cancel" ? `The customer cancelled payment #${data.txn_id}` : `The customer went ahead with payment #${data.txn_id}`;
    default: return words(event.kind);
  }
}

function Activity({ events }: { events: CaseEvent[] }) {
  return (
    <ol className="space-y-3 border-l border-slate-200 pl-4 text-sm">
      {events.map((event) => (
        <li key={event.id} className="relative">
          <span className="absolute top-1.5 -left-[21px] size-2 rounded-full bg-slate-400" />
          <div className="font-medium text-slate-800">{eventText(event)}</div>
          {event.body && <p className="whitespace-pre-line text-slate-700">{event.body}</p>}
          <div className="text-xs text-slate-500">{event.actor ?? "system"} · recorded {when(event.at)}</div>
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
      <label htmlFor="note" className="block text-xs font-medium text-slate-600">Add a note</label>
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

function Detail({ data, reload }: { data: CaseDetail; reload: () => void }) {
  const { me, canReview, canApprove } = useSession();
  const [dialog, setDialog] = useState<Verdict | "escalate" | null>(null);
  const open = data.status !== "closed";
  const mayDecide = canReview && open && (canApprove || (data.status !== "escalated" && (data.assigned_to == null || data.assigned_to === me.id)));
  const waiting = data.alerts.filter((alert) => alert.status === "held" || alert.status === "pending_customer");

  return (
    <>
      <PageHeader
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
          <Card title="Decision" hint="A person closes every case. The model only ranks and explains.">
            <Facts
              rows={[
                ["Assigned to", data.assignee ?? "nobody"],
                ["Review due", data.sla_due_at ? when(data.sla_due_at) : "no deadline"],
                ["Verdict", data.verdict ? <StatusBadge key="v" status={data.verdict} /> : "not yet"],
                ...(data.closed_at ? [["Closed", `${when(data.closed_at)} by ${data.closer ?? "–"}`] as [string, string]] : []),
              ]}
            />
            {canReview && open && (
              <div className="mt-4 space-y-3">
                <div className="flex flex-wrap items-center gap-2"><Assign data={data} onDone={reload} /></div>
                <div className="flex flex-wrap gap-2">
                  {(Object.keys(VERDICTS) as Verdict[]).map((verdict) => (
                    <Button key={verdict} variant={VERDICTS[verdict].variant} disabled={!mayDecide} onClick={() => setDialog(verdict)}>
                      {VERDICTS[verdict].button}
                    </Button>
                  ))}
                </div>
                {!mayDecide && (
                  <p className="text-xs text-slate-500">
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

          <Card title="Case activity" hint="Recorded on the server clock, in the order it happened.">
            {data.timeline.length ? <Activity events={data.timeline} /> : <Empty>Nothing has happened yet.</Empty>}
            {canReview && <NoteBox caseId={data.id} onDone={reload} />}
          </Card>

          <Card title="Freeze requests"><FreezeList rows={data.freeze_requests} /></Card>

          <Card title="Customer reports" hint="Sent from the app with one tap.">
            {data.customer_reports.length ? (
              <ul className="divide-y divide-slate-100 text-sm">
                {data.customer_reports.map((report) => (
                  <li key={report.id} className="py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="amber">{words(report.category)}</Badge>
                      <span className="text-slate-600">from <Id value={report.reporter_id} caseId={data.id} /> · {when(report.reported_at)}</span>
                    </div>
                    {report.description && <p className="mt-1 text-slate-800">{report.description}</p>}
                    {report.txn_id && <p className="text-xs text-slate-500">about payment #{report.txn_id}</p>}
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
          intro={VERDICTS[dialog].intro}
          confirm={VERDICTS[dialog].button}
          variant={VERDICTS[dialog].variant}
          onClose={() => setDialog(null)}
          onSubmit={async (note) => {
            await api(`/v1/cases/${data.id}/verdict`, { verdict: dialog, note });
            reload();
          }}
        />
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
