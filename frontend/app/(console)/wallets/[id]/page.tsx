"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { Id, useSession } from "@/components/session";
import { FreezeList, TxnTable } from "@/components/tables";
import { Async, Badge, Button, Card, Empty, Facts, PageHeader, ReasonDialog, StatusBadge, Table, Td, TierBadge } from "@/components/ui";
import { FreezeButton, NetworkCard, RiskProfile } from "@/components/wallet";
import { api, ApiError, useApi } from "@/lib/api";
import { day, when, words } from "@/lib/format";
import type { Wallet } from "@/lib/types";

function Detail({ data, reload }: { data: Wallet; reload: () => void }) {
  const router = useRouter();
  const { canReview, canApprove } = useSession();
  const [dialog, setDialog] = useState<"case" | "unfreeze" | null>(null);
  const frozen = data.status === "frozen";
  const openCase = data.cases.find((row) => row.status !== "closed");
  const pending = data.freeze_requests.some((request) => request.status === "pending");

  return (
    <>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            Wallet <Id value={data.wallet_id} link={false} />
            {frozen && <Badge tone="blue">Frozen</Badge>}
            {data.risk.confirmed_fraud && <Badge tone="red">Confirmed fraud</Badge>}
            {!data.registered && <Badge tone="amber">Not in the customer register</Badge>}
          </span>
        }
        sub="Everything FraudLens knows about this wallet. Opening this page is recorded in the audit log."
        actions={
          canReview && (
            <>
              {openCase ? (
                <Link href={`/cases/${openCase.id}`} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700">Open case #{openCase.id}</Link>
              ) : (
                data.registered && <Button variant="primary" onClick={() => setDialog("case")}>Open a case</Button>
              )}
              {data.registered && !frozen && !pending && <FreezeButton walletId={data.wallet_id} caseId={openCase?.id} onDone={reload} />}
              {pending && <Badge tone="amber">Freeze waiting for approval</Badge>}
              {frozen && canApprove && <Button onClick={() => setDialog("unfreeze")}>Lift freeze</Button>}
            </>
          )
        }
      />

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4">
          <Card title="Account">
            <Facts
              rows={[
                ["Status", <StatusBadge key="s" status={data.status} />],
                ["Opened", data.created_at ? day(data.created_at) : "unknown"],
                ["District", data.district ? `${data.district}${data.area_type ? ` (${data.area_type})` : ""}` : "unknown"],
                ["Customer type", data.segment ? words(data.segment) : "unknown"],
                ["Usual channel", data.channel ?? "unknown"],
                ...(data.frozen_at ? [["Frozen at", when(data.frozen_at)] as [string, string]] : []),
                ...(data.flag
                  ? [[
                      "Fraud flag",
                      <span key="f">
                        {words(data.flag.reason)} · {when(data.flag.flagged_at)}
                        {data.flag.case_id && <> · <Link href={`/cases/${data.flag.case_id}`} className="text-sky-700 hover:underline">case #{data.flag.case_id}</Link></>}
                      </span>,
                    ] as [string, React.ReactNode]]
                  : []),
              ]}
            />
          </Card>
          <Card title="Cases" flush>
            {data.cases.length ? (
              <Table head={["Case", "Status", "Priority", "Opened", "Verdict"]}>
                {data.cases.map((row) => (
                  <tr key={row.id}>
                    <Td><Link href={`/cases/${row.id}`} className="text-sky-700 hover:underline">#{row.id}</Link></Td>
                    <Td><StatusBadge status={row.status} /></Td>
                    <Td><TierBadge tier={row.priority} /></Td>
                    <Td className="whitespace-nowrap text-slate-600">{day(row.opened_at)}</Td>
                    <Td><StatusBadge status={row.verdict} /></Td>
                  </tr>
                ))}
              </Table>
            ) : (
              <Empty>This wallet has never had a case.</Empty>
            )}
          </Card>
          <Card title="Freeze requests"><FreezeList rows={data.freeze_requests} /></Card>
        </div>
        <div className="space-y-4 xl:col-span-2">
          <Card title="Behaviour and risk" hint="What the wallet has done, and what the mule model sees.">
            <RiskProfile risk={data.risk} />
          </Card>
          <NetworkCard walletId={data.wallet_id} threshold={data.risk.mule_threshold} />
          <Card title="Interrupted payments" hint="Recent payments this wallet sent or received that were warned, challenged or held." flush>
            <TxnTable rows={data.recent_alerts} subject={data.wallet_id} />
          </Card>
          <Card title="Recent transactions" flush>
            <TxnTable rows={data.recent_transactions} subject={data.wallet_id} />
          </Card>
        </div>
      </div>

      {dialog === "case" && (
        <ReasonDialog
          title="Open a case on this wallet"
          label="Why you are investigating"
          confirm="Open case"
          onClose={() => setDialog(null)}
          onSubmit={async (reason) => {
            try {
              const made = await api<{ id: number }>("/v1/cases", { wallet_id: data.wallet_id, reason });
              router.push(`/cases/${made.id}`);
            } catch (problem) {
              // Someone opened one in the meantime: go to theirs.
              if (problem instanceof ApiError && problem.code === "case_exists" && problem.extra.case_id) {
                router.push(`/cases/${problem.extra.case_id}`);
                return;
              }
              throw problem;
            }
          }}
        />
      )}
      {dialog === "unfreeze" && (
        <ReasonDialog
          title="Lift the freeze"
          intro="The wallet can send and receive again straight away."
          confirm="Lift freeze"
          onClose={() => setDialog(null)}
          onSubmit={async (reason) => {
            await api(`/v1/wallets/${data.wallet_id}/unfreeze`, { reason });
            reload();
          }}
        />
      )}
    </>
  );
}

export default function WalletPage() {
  const { id } = useParams<{ id: string }>();
  const valid = /^[A-Za-z0-9_-]{1,32}$/.test(id);
  const wallet = useApi<Wallet>(valid ? `/v1/wallets/${id}` : null);
  if (!valid) return <Empty>That is not a wallet number.</Empty>;
  return <Async state={wallet}>{(data) => <Detail data={data} reload={wallet.reload} />}</Async>;
}
