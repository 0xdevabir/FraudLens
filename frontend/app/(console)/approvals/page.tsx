"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Id, useSession } from "@/components/session";
import { Async, Badge, Button, Card, Chip, Empty, PageHeader, ReasonDialog, StatusBadge, Table, Td } from "@/components/ui";
import { api, ApiError, useApi } from "@/lib/api";
import { when, words } from "@/lib/format";
import type { Freeze } from "@/lib/types";

const STATUSES = ["pending", "approved", "rejected"] as const;
type Status = (typeof STATUSES)[number];

/** A ring-level proposal is one request per wallet, each carrying the ring in its reason. */
function ringOf(request: Freeze): string | null {
  return /^Ring (R-[A-Za-z0-9_-]+): /.exec(request.reason)?.[1] ?? null;
}

interface Pending {
  title: string;
  requests: Freeze[];
  approve: boolean;
}

export default function ApprovalsPage() {
  const { me, canApprove } = useSession();
  const [status, setStatus] = useState<Status>("pending");
  const requests = useApi<Freeze[]>(`/v1/freeze-requests?status=${status}&limit=200`);
  const [dialog, setDialog] = useState<Pending | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);

  const rings = useMemo(() => {
    const groups = new Map<string, Freeze[]>();
    for (const request of requests.data ?? []) {
      const ring = ringOf(request);
      if (ring && request.status === "pending") groups.set(ring, [...(groups.get(ring) ?? []), request]);
    }
    return [...groups.entries()];
  }, [requests.data]);

  const mine = (request: Freeze) => request.requested_by === me.id;

  async function decide(pending: Pending, note: string) {
    let done = 0;
    const failed: string[] = [];
    for (const request of pending.requests) {
      try {
        await api(`/v1/freeze-requests/${request.id}/${pending.approve ? "approve" : "reject"}`, { note });
        done += 1;
      } catch (problem) {
        if (pending.requests.length === 1) throw problem;
        failed.push(problem instanceof ApiError ? problem.code : "failed");
      }
    }
    setOutcome(
      `${done} request${done === 1 ? "" : "s"} ${pending.approve ? "approved" : "rejected"}` +
        (failed.length ? `, ${failed.length} not changed (${[...new Set(failed)].map(words).join(", ")})` : ""),
    );
    requests.reload();
  }

  return (
    <>
      <PageHeader
        title="Freeze approvals"
        sub="Freezing a wallet takes two people: a reviewer asks, and a supervisor who is not that reviewer decides. Nothing here is automatic."
      />
      {outcome && <div role="status" className="mb-3 rounded-xl border border-good/30 bg-good/10 px-3 py-2 text-sm text-good">{outcome}</div>}
      {!canApprove && (
        <div className="mb-3 rounded-xl border border-line bg-card px-3 py-2 text-sm text-fg-2">
          You can see the requests. Only a supervisor can approve or reject them.
        </div>
      )}

      {status === "pending" && rings.length > 0 && (
        <Card title="Ring proposals" hint="Requests that were raised together for one ring. Each wallet is still decided and audited separately." className="mb-4">
          <ul className="divide-y divide-line text-sm">
            {rings.map(([ring, group]) => {
              const allowed = group.filter((request) => !mine(request));
              return (
                <li key={ring} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span>
                    <Link href={`/rings/${ring}`} className="font-medium text-info hover:underline">{ring}</Link>
                    <span className="text-fg-2"> · {group.length} wallets waiting · asked by {group[0].requester}</span>
                  </span>
                  {canApprove && (
                    <span className="flex gap-2">
                      <Button small variant="danger" disabled={!allowed.length} onClick={() => setDialog({ title: `Freeze ${allowed.length} wallets in ${ring}`, requests: allowed, approve: true })}>
                        Approve all
                      </Button>
                      <Button small disabled={!allowed.length} onClick={() => setDialog({ title: `Reject ${allowed.length} requests in ${ring}`, requests: allowed, approve: false })}>
                        Reject all
                      </Button>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      <Card flush>
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-4 py-3">
          {STATUSES.map((value) => <Chip key={value} on={status === value} onClick={() => setStatus(value)}>{words(value)}</Chip>)}
        </div>
        <Async state={requests}>
          {(rows) =>
            rows.length ? (
              <Table head={["Request", "Wallet", "Status", "Reason", "Asked by", "Case", status === "pending" ? "" : "Decision"]}>
                {rows.map((request) => (
                  <tr key={request.id} className="hover:bg-wash">
                    <Td>#{request.id}</Td>
                    <Td><Id value={request.wallet_id} caseId={request.case_id} /></Td>
                    <Td><StatusBadge status={request.status} /></Td>
                    <Td className="max-w-md whitespace-normal text-fg-2">{request.reason}</Td>
                    <Td className="whitespace-nowrap text-fg-2">
                      {request.requester}
                      <div className="text-xs text-fg-3">{when(request.created_at)}</div>
                    </Td>
                    <Td>
                      {request.case_id ? <Link href={`/cases/${request.case_id}`} className="text-info hover:underline">#{request.case_id}</Link> : <span className="text-fg-4">–</span>}
                    </Td>
                    <Td className="max-w-sm whitespace-normal">
                      {request.status !== "pending" ? (
                        <span className="text-fg-2">{request.decider}: {request.decision_note}</span>
                      ) : !canApprove ? null : mine(request) ? (
                        <Badge title="The two-person rule: whoever asked cannot approve">you asked for this</Badge>
                      ) : (
                        <span className="flex gap-2">
                          <Button small variant="danger" onClick={() => setDialog({ title: `Freeze wallet (request #${request.id})`, requests: [request], approve: true })}>Approve</Button>
                          <Button small onClick={() => setDialog({ title: `Reject request #${request.id}`, requests: [request], approve: false })}>Reject</Button>
                        </span>
                      )}
                    </Td>
                  </tr>
                ))}
              </Table>
            ) : (
              <Empty>No {status} freeze requests.</Empty>
            )
          }
        </Async>
      </Card>

      {dialog && (
        <ReasonDialog
          title={dialog.title}
          label="Decision note"
          intro={dialog.approve
            ? "An approved freeze stops the wallet from sending or receiving until a supervisor lifts it."
            : "The wallet stays as it is. The requester sees your note."}
          confirm={dialog.approve ? "Approve freeze" : "Reject"}
          variant={dialog.approve ? "danger" : "primary"}
          onClose={() => setDialog(null)}
          onSubmit={(note) => decide(dialog, note)}
        />
      )}
    </>
  );
}
