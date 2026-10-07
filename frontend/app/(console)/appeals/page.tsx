"use client";

import Link from "next/link";
import { useState } from "react";

import { Id, useSession } from "@/components/session";
import { Async, Badge, Button, Card, Chip, Empty, PageHeader, ReasonDialog, StatusBadge, Table, Td, TierBadge } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { taka, when, words } from "@/lib/format";
import type { Appeal, AppealQueue, AppealStatus } from "@/lib/types";

const STATUSES: AppealStatus[] = ["pending", "approved", "rejected"];
const OUTCOME_TONE = { pending: "amber", approved: "green", rejected: "slate" } as const;

/** Time left until the deadline, or how late it is: "12 min left", "1 h 5 min late". */
function due(appeal: Appeal, now: string): { text: string; late: boolean } {
  const minutes = Math.round((Date.parse(appeal.sla_due_at) - Date.parse(now)) / 60_000);
  const span = Math.abs(minutes);
  const text = span >= 60 ? `${Math.floor(span / 60)} h ${span % 60} min` : `${span} min`;
  return minutes >= 0 ? { text: `${text} left`, late: false } : { text: `${text} late`, late: true };
}

interface Pending {
  appeal: Appeal;
  approve: boolean;
}

export default function AppealsPage() {
  const { canReview } = useSession();
  const [status, setStatus] = useState<AppealStatus>("pending");
  const queue = useApi<AppealQueue>(`/v1/appeals?status=${status}&limit=200`);
  const [dialog, setDialog] = useState<Pending | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);

  async function decide({ appeal, approve }: Pending, note: string) {
    const done = await api<{ released: boolean; txn_status: string }>(
      `/v1/appeals/${appeal.id}/${approve ? "approve" : "reject"}`,
      { note },
    );
    setOutcome(
      `Appeal #${appeal.id} ${approve ? "approved" : "rejected"}. ` +
        (done.released ? "The payment was released to the recipient." : `The payment is ${words(done.txn_status)}.`),
    );
    queue.reload();
  }

  return (
    <>
      <PageHeader tour="appeals-header"
        title="Customer appeals"
        sub="Customers who say a warned or held payment is genuine. A person answers every one. Approving a held payment releases it, but never to a confirmed-fraud or frozen wallet. An approved appeal becomes a 'legitimate' training label."
      />
      {outcome && <div role="status" className="mb-3 rounded-xl border border-good/30 bg-good/10 px-3 py-2 text-sm text-good">{outcome}</div>}

      <Card flush tour="appeals-queue">
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-4 py-3">
          {STATUSES.map((value) => (
            <Chip key={value} on={status === value} onClick={() => setStatus(value)}>
              {words(value)}
              {queue.data ? ` (${queue.data.counts[value]})` : ""}
            </Chip>
          ))}
        </div>
        <Async state={queue}>
          {({ appeals, now }) =>
            appeals.length ? (
              <Table head={["Appeal", "Payment", "Tier", "Customer says", "Deadline", "Case", status === "pending" ? "" : "Decision"]}>
                {appeals.map((appeal) => {
                  const deadline = due(appeal, now);
                  return (
                    <tr key={appeal.id} className="hover:bg-wash">
                      <Td>
                        #{appeal.id}
                        <div className="text-xs text-fg-3">{when(appeal.filed_at)}</div>
                      </Td>
                      <Td className="whitespace-nowrap">
                        <Link href={`/decisions/${appeal.txn_id}`} className="text-info hover:underline">{taka(appeal.amount)}</Link>
                        <div className="text-xs text-fg-3">
                          <Id value={appeal.wallet_id} /> → {appeal.receiver_id ? <Id value={appeal.receiver_id} caseId={appeal.case_id} /> : "–"}
                        </div>
                        {appeal.txn_status && <div className="mt-1"><StatusBadge status={appeal.txn_status} /></div>}
                      </Td>
                      <Td><TierBadge tier={appeal.tier} /></Td>
                      <Td className="max-w-md whitespace-normal">
                        <Badge tone="blue">{words(appeal.relation)}</Badge>
                        <div className="mt-1 text-fg-2">{appeal.reason}</div>
                      </Td>
                      <Td className="whitespace-nowrap">
                        {appeal.status === "pending" ? (
                          <Badge tone={deadline.late ? "red" : "amber"} title={`Due ${when(appeal.sla_due_at)}`}>{deadline.text}</Badge>
                        ) : (
                          <span className="text-fg-3">{when(appeal.sla_due_at)}</span>
                        )}
                      </Td>
                      <Td>
                        {appeal.case_id ? <Link href={`/cases/${appeal.case_id}`} className="text-info hover:underline">#{appeal.case_id}</Link> : <span className="text-fg-4">–</span>}
                      </Td>
                      <Td className="max-w-sm whitespace-normal">
                        {appeal.status !== "pending" ? (
                          <span className="text-fg-2">
                            <Badge tone={OUTCOME_TONE[appeal.status]}>{words(appeal.status)}</Badge> {appeal.decider}: {appeal.decision_note}
                          </span>
                        ) : canReview ? (
                          <span className="flex gap-2">
                            <Button small onClick={() => setDialog({ appeal, approve: true })}>Approve</Button>
                            <Button small variant="danger" onClick={() => setDialog({ appeal, approve: false })}>Reject</Button>
                          </span>
                        ) : null}
                      </Td>
                    </tr>
                  );
                })}
              </Table>
            ) : (
              <Empty>No {status} appeals.</Empty>
            )
          }
        </Async>
      </Card>

      {dialog && (
        <ReasonDialog
          title={`${dialog.approve ? "Approve" : "Reject"} appeal #${dialog.appeal.id}`}
          label="Decision note"
          intro={
            dialog.approve
              ? dialog.appeal.tier === "hold"
                ? "The held payment is released to the recipient now, unless that wallet is confirmed fraud or frozen. The payment is recorded as a 'legitimate' label."
                : "No money moves: the customer still decides a warning. The payment is recorded as a 'legitimate' label, so the model learns this warning was wrong."
              : "A held payment stays held and its case decides. A rejected appeal adds no label."
          }
          confirm={dialog.approve ? "Approve appeal" : "Reject appeal"}
          variant={dialog.approve ? "primary" : "danger"}
          onClose={() => setDialog(null)}
          onSubmit={(note) => decide(dialog, note)}
        />
      )}
    </>
  );
}
