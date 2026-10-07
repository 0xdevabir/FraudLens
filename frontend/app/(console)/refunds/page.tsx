"use client";

import Link from "next/link";
import { useState } from "react";

import { Id, useSession } from "@/components/session";
import { Async, Badge, Button, Card, Chip, Empty, PageHeader, ReasonDialog, StatusBadge, Table, Td } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { taka, when, words } from "@/lib/format";
import type { Refund, RefundQueue, RefundStatus } from "@/lib/types";

const STATUSES: RefundStatus[] = ["open", "paid", "unrecoverable", "declined"];
const OUTCOME: Record<string, string> = {
  confirmed_fraud: "Fraud confirmed",
  nothing_left: "Fraud confirmed, nothing left in the wallet",
  not_confirmed: "Fraud not confirmed",
  not_a_victim: "Claimant not a victim",
};

/** Time left until the promise to the customer, or how late it is. */
function due(refund: Refund, now: string): { text: string; late: boolean } {
  const hours = Math.round((Date.parse(refund.sla_due_at) - Date.parse(now)) / 3_600_000);
  const span = Math.abs(hours);
  const text = span >= 24 ? `${Math.floor(span / 24)} d ${span % 24} h` : `${span} h`;
  return hours >= 0 ? { text: `${text} left`, late: false } : { text: `${text} late`, late: true };
}

export default function RefundsPage() {
  const { canReview } = useSession();
  const [status, setStatus] = useState<RefundStatus>("open");
  const queue = useApi<RefundQueue>(`/v1/refunds?status=${status}&limit=200`);
  const [declining, setDeclining] = useState<Refund | null>(null);

  return (
    <>
      <PageHeader tour="refunds-header"
        title="Victim refunds"
        sub="A customer who reports a scam payment claims a refund for it. Once a person confirms the fraud and two people have frozen the receiving wallet, upay pays back what is left in it, shared by what each victim lost. Nobody pays one by hand."
      />
      <Card flush tour="refunds-queue">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {STATUSES.map((value) => (
              <Chip key={value} on={status === value} onClick={() => setStatus(value)}>
                {words(value)}
                {queue.data ? ` (${queue.data.counts[value]})` : ""}
              </Chip>
            ))}
          </div>
          {queue.data && <span className="text-sm text-fg-2">{taka(queue.data.refunded_total)} returned to victims so far</span>}
        </div>
        <Async state={queue}>
          {({ refunds, now }) =>
            refunds.length ? (
              <Table head={["Claim", "Payment", "Victim → wallet", status === "open" ? "Next step" : "Outcome", status === "open" ? "Promised by" : "Settled", "Case", ""]}>
                {refunds.map((refund) => {
                  const deadline = due(refund, now);
                  return (
                    <tr key={refund.id} className="hover:bg-wash">
                      <Td>
                        #{refund.id}
                        <div className="text-xs text-fg-3">{when(refund.filed_at)}</div>
                      </Td>
                      <Td className="whitespace-nowrap">
                        <Link href={`/decisions/${refund.txn_id}`} className="text-info hover:underline">{taka(refund.amount_claimed)}</Link>
                        {refund.status === "paid" && <div className="text-xs text-good">{taka(refund.amount_refunded ?? 0)} refunded</div>}
                      </Td>
                      <Td className="whitespace-nowrap text-xs">
                        <Id value={refund.victim_id} caseId={refund.case_id} /> → <Id value={refund.wallet_id} caseId={refund.case_id} />
                        {refund.wallet_frozen && <div className="mt-1"><StatusBadge status="frozen" /></div>}
                      </Td>
                      <Td className="max-w-xs whitespace-normal text-fg-2">
                        {refund.status === "open" ? (
                          refund.wallet_frozen ? "Confirm the fraud on the case" : "Freeze the wallet and confirm the fraud"
                        ) : (
                          <>
                            <StatusBadge status={refund.status} /> {OUTCOME[refund.outcome ?? ""] ?? words(refund.outcome ?? "")}
                            {refund.settler && <span className="text-fg-3"> · {refund.settler}</span>}
                            {refund.note && <div className="text-xs text-fg-3">{refund.note}</div>}
                          </>
                        )}
                      </Td>
                      <Td className="whitespace-nowrap">
                        {refund.status === "open" ? (
                          <Badge tone={deadline.late ? "red" : "amber"} title={`Due ${when(refund.sla_due_at)}`}>{deadline.text}</Badge>
                        ) : (
                          <span className="text-fg-3">{when(refund.settled_at)}</span>
                        )}
                      </Td>
                      <Td>
                        {refund.case_id ? <Link href={`/cases/${refund.case_id}`} className="text-info hover:underline">#{refund.case_id}</Link> : <span className="text-fg-4">–</span>}
                      </Td>
                      <Td>
                        {canReview && refund.status === "open" && <Button small onClick={() => setDeclining(refund)}>Not a victim</Button>}
                      </Td>
                    </tr>
                  );
                })}
              </Table>
            ) : (
              <Empty>No {words(status).toLowerCase()} refund claims.</Empty>
            )
          }
        </Async>
      </Card>
      {declining && (
        <ReasonDialog
          title={`Decline refund claim #${declining.id}`}
          intro="Take this claimant out: they look like part of the scheme, not a victim of it. No money moves, and the other victims' shares grow."
          confirm="Decline refund"
          variant="danger"
          onClose={() => setDeclining(null)}
          onSubmit={async (note) => {
            await api(`/v1/refunds/${declining.id}/decline`, { note });
            queue.reload();
          }}
        />
      )}
    </>
  );
}
