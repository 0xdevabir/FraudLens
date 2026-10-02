"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Graph, GraphLegend } from "@/components/graph";
import { Id, useSession } from "@/components/session";
import { Async, Badge, Button, Card, Empty, PageHeader, ReasonDialog, Stat, Table, Td } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { num, ringLabel, taka, words } from "@/lib/format";
import type { Freeze, Ring } from "@/lib/types";

interface Proposal {
  requested: Freeze[];
  skipped: { wallet_id: string; why: string }[];
}

function Detail({ ring, reload }: { ring: Ring; reload: () => void }) {
  const router = useRouter();
  const { canReview } = useSession();
  const [asking, setAsking] = useState(false);
  const [proposal, setProposal] = useState<Proposal | null>(null);

  const sets = useMemo(
    () => ({ confirmed: new Set(ring.confirmed), linked: new Set(ring.linked_only), frozen: new Set(ring.frozen) }),
    [ring],
  );
  const graph = useMemo(
    () => ({
      nodes: ring.wallets.map((id) => ({
        id,
        kind: "wallet" as const,
        flagged: sets.confirmed.has(id),
        suspected: !sets.linked.has(id),
        frozen: sets.frozen.has(id),
      })),
      edges: ring.edges.map((edge) => ({ source: edge.a, target: edge.b, kind: edge.kind })),
    }),
    [ring, sets],
  );
  const members = ring.size;
  const role = (id: string) =>
    sets.confirmed.has(id) ? <Badge tone="red">confirmed fraud</Badge>
      : sets.linked.has(id) ? <Badge tone="slate">linked by handset only</Badge>
        : <Badge tone="amber">suspected mule</Badge>;

  return (
    <>
      <PageHeader
        title={<span className="flex flex-wrap items-center gap-2">Ring <span className="font-mono text-base">{ringLabel(ring.ring_id)}</span></span>}
        sub="Wallets tied together by shared handsets and transfers. Customers whose wallets were taken over are listed separately: they are victims, not members."
        actions={
          canReview && (
            <Button variant="danger" disabled={ring.frozen.length >= members} onClick={() => setAsking(true)}>
              Propose freezing the ring
            </Button>
          )
        }
      />

      {proposal && (
        <div role="status" className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {proposal.requested.length} freeze request{proposal.requested.length === 1 ? "" : "s"} sent for approval. Nothing is frozen until a
          supervisor approves on the <Link href="/approvals" className="underline">approvals page</Link>.
          {proposal.skipped.length > 0 && (
            <> {proposal.skipped.length} left out: {Object.entries(
              proposal.skipped.reduce<Record<string, number>>((count, row) => ({ ...count, [row.why]: (count[row.why] ?? 0) + 1 }), {}),
            ).map(([why, n]) => `${n} ${words(why).toLowerCase()}`).join(", ")}.</>
          )}
        </div>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Wallets in the ring" value={num(members)} sub={`${num(ring.takeover_victims.length)} takeover victims kept apart`} />
        <Stat label="Confirmed fraud" value={num(ring.confirmed.length)} tone="bad" sub={`${num(ring.linked_only.length)} linked by handset only`} />
        <Stat label="Money received" value={taka(ring.received_total, true)} />
        <Stat label="Shared handsets" value={num(ring.shared_devices.length)} sub={`${num(ring.transfer_links)} transfers inside`} />
        <Stat label="Frozen" value={`${num(ring.frozen.length)} of ${num(members)}`} tone={ring.frozen.length ? "good" : "plain"} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="How the ring is connected" hint="Click a wallet to open it." className="xl:col-span-2">
          <Graph nodes={graph.nodes} edges={graph.edges} height={520} onSelect={(id) => router.push(`/wallets/${id}`)} />
          <div className="mt-2">
            <GraphLegend extra={[{ label: "Shared handset", dashed: true }]} />
          </div>
        </Card>
        <div className="space-y-4">
          <Card title="Where the money leaves" hint="The five agents the ring cashes out at most." flush>
            {ring.cash_out_agents.length ? (
              <Table head={["Agent", "Cash-outs"]}>
                {ring.cash_out_agents.map((agent) => (
                  <tr key={agent.agent_id}>
                    <Td><Id value={agent.agent_id} /></Td>
                    <Td right>{num(agent.cash_outs)}</Td>
                  </tr>
                ))}
              </Table>
            ) : (
              <Empty>No cash-outs seen.</Empty>
            )}
          </Card>
          <Card title="Shared handsets">
            {ring.shared_devices.length ? (
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
                {ring.shared_devices.map((device) => <Id key={device} value={device} link={false} />)}
              </div>
            ) : (
              <Empty>None: the ring is linked by transfers only.</Empty>
            )}
          </Card>
        </div>
      </div>

      {ring.takeover_victims.length > 0 && (
        <Card title="Takeover victims" hint="Wallets that had their own handset before they appeared on one of the ring's. They are never part of a freeze proposal." className="mt-4">
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
            {ring.takeover_victims.map((id) => <Id key={id} value={id} />)}
          </div>
        </Card>
      )}

      <Card title="Wallets" className="mt-4" flush>
        <Table head={["Wallet", "Role", "State"]}>
          {ring.wallets.map((id) => (
            <tr key={id} className="hover:bg-slate-50">
              <Td><Id value={id} /></Td>
              <Td>{role(id)}</Td>
              <Td>{sets.frozen.has(id) ? <Badge tone="blue">frozen</Badge> : <span className="text-slate-400">active</span>}</Td>
            </tr>
          ))}
        </Table>
      </Card>

      {asking && (
        <ReasonDialog
          title={`Propose freezing ${num(members - ring.frozen.length)} wallets`}
          intro="This creates one freeze request per member wallet. Takeover victims are not members and are left out. A supervisor other than you has to approve each one before anything is frozen."
          confirm="Send for approval"
          variant="danger"
          onClose={() => setAsking(false)}
          onSubmit={async (reason) => {
            setProposal(await api<Proposal>(`/v1/rings/${ring.ring_id}/freeze-requests`, { reason }));
            reload();
          }}
        />
      )}
    </>
  );
}

export default function RingPage() {
  const { id } = useParams<{ id: string }>();
  const valid = /^R-[A-Za-z0-9_-]{1,32}$/.test(id);
  const ring = useApi<Ring>(valid ? `/v1/rings/${id}` : null);
  if (!valid) return <Empty>That is not a ring.</Empty>;
  return <Async state={ring}>{(data) => <Detail ring={data} reload={ring.reload} />}</Async>;
}
