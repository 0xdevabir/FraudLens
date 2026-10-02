"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { api, useApi } from "@/lib/api";
import { idKind, num, pct, taka } from "@/lib/format";
import type { Network, WalletRisk } from "@/lib/types";

import { Graph, GraphLegend } from "./graph";
import { Id, useSession } from "./session";
import { Async, Badge, Button, Card, Chip, Facts, ReasonDialog } from "./ui";

function span(seconds: number): string {
  if (seconds < 90) return `${num(seconds)} s`;
  if (seconds < 5400) return `${num(seconds / 60)} min`;
  if (seconds < 172800) return `${num(seconds / 3600, 1)} h`;
  return `${num(seconds / 86400, 1)} days`;
}

/** The receiving-side behaviour the mule model reads, in the words a reviewer would use. */
const FEATURES: [string, string, (value: number) => string][] = [
  ["r_age_days", "Wallet age", (v) => `${num(v)} days`],
  ["r_fan_in_24h", "Different senders, last 24 h", (v) => num(v)],
  ["r_fan_in_7d", "Different senders, last 7 days", (v) => num(v)],
  ["r_in_sum_24h", "Received, last 24 h", (v) => taka(v)],
  ["r_new_sender_share_7d", "Senders never seen before", (v) => pct(v, 0)],
  ["r_cross_district_share_7d", "Senders from another district", (v) => pct(v, 0)],
  ["r_dwell_secs", "Typical time money stays", span],
  ["r_fast_exit_share", "Money gone within the hour", (v) => pct(v, 0)],
  ["r_cashout_share", "Cashed out, per taka received", (v) => `৳${v.toFixed(2)}`],
  ["r_reciprocity", "Senders it also pays back", (v) => pct(v, 0)],
  ["r_device_other_wallets", "Other wallets on its handsets", (v) => num(v)],
  ["r_flagged_neighbors", "Confirmed-fraud contacts", (v) => num(v)],
];

export function RiskProfile({ risk }: { risk: WalletRisk }) {
  const summary = risk.summary;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {risk.confirmed_fraud && <Badge tone="red">Confirmed fraud</Badge>}
        {risk.mule_alert && !risk.confirmed_fraud && <Badge tone="amber">Suspected mule</Badge>}
        {!risk.confirmed_fraud && !risk.mule_alert && <Badge>No mule alert</Badge>}
        <span className="text-slate-600">
          Mule score {risk.mule_score == null ? "not available (too little incoming activity)" : num(risk.mule_score, 3)}
          {risk.mule_score != null && ` · alert at ${num(risk.mule_threshold, 3)}`}
          {risk.highest_mule_score_seen != null && ` · highest seen ${num(risk.highest_mule_score_seen, 3)}`}
        </span>
      </div>
      <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
        <Facts
          rows={[
            ["Payments made", num(summary.transactions_initiated)],
            ["People paid", num(summary.recipients)],
            ["People who paid in", num(summary.senders)],
            ["Received", `${taka(summary.received_total)} in ${num(summary.received_count)}`],
            ["Cashed out", taka(summary.cashed_out_total)],
            ["Handsets used", num(summary.handsets)],
            ["Agents used", num(summary.agents_used)],
            ["Confirmed-fraud contacts", num(summary.confirmed_fraud_neighbours)],
          ]}
        />
        <Facts
          rows={FEATURES.filter(([key]) => risk.features[key] != null).map(([key, label, format]) => [
            label,
            format(risk.features[key] as number),
          ])}
        />
      </div>
      {risk.shares_handset_with.length > 0 && (
        <div className="text-sm">
          <span className="text-slate-500">Shares a handset with </span>
          <span className="inline-flex flex-wrap gap-x-3 gap-y-1">
            {risk.shares_handset_with.map((id) => <Id key={id} value={id} />)}
          </span>
        </div>
      )}
    </div>
  );
}

const SIZES = [25, 60, 120];

/** One hop around a wallet: who paid it, who it paid, where it cashed out, and who shares its handset. */
export function NetworkCard({ walletId, threshold }: { walletId: string; threshold?: number }) {
  const router = useRouter();
  const [limit, setLimit] = useState(SIZES[1]);
  const network = useApi<Network>(`/v1/wallets/${walletId}/network?limit=${limit}`);
  const graph = useMemo(() => {
    if (!network.data) return null;
    return {
      nodes: network.data.nodes.map((node) => ({
        id: node.id,
        kind: node.kind,
        flagged: node.flagged,
        frozen: node.frozen,
        subject: node.role === "subject",
        suspected: threshold != null && node.mule_score != null && node.mule_score >= threshold,
      })),
      edges: network.data.edges.map((edge) => ({ ...edge, kind: edge.kind === "shared_handset" ? "device" : edge.kind })),
    };
  }, [network.data, threshold]);

  return (
    <Card
      title="Network"
      hint="Arrows follow the money. Click a node to open it."
      actions={SIZES.map((size) => <Chip key={size} on={limit === size} onClick={() => setLimit(size)}>{size} links</Chip>)}
    >
      <Async state={network}>
        {(data) => (
          <>
            {graph && (
              <Graph
                nodes={graph.nodes}
                edges={graph.edges}
                onSelect={(id) => id !== walletId && router.push(idKind(id) === "agent" ? `/agents/${id}` : `/wallets/${id}`)}
              />
            )}
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <GraphLegend extra={[{ label: "Shared handset", dashed: true }]} />
              <span className="text-xs text-slate-500">
                {num(data.nodes.length)} nodes{data.truncated && " · busiest links only, raise the limit to see more"}
              </span>
            </div>
          </>
        )}
      </Async>
    </Card>
  );
}

/** Ask for a freeze. It only ever creates a request: a supervisor who is not the requester decides. */
export function FreezeButton({ walletId, caseId, onDone, small }: { walletId: string; caseId?: number | null; onDone: () => void; small?: boolean }) {
  const { canReview } = useSession();
  const [open, setOpen] = useState(false);
  if (!canReview) return null;
  return (
    <>
      <Button small={small} variant="danger" onClick={() => setOpen(true)}>Request freeze</Button>
      {open && (
        <ReasonDialog
          title="Request a wallet freeze"
          intro="Nothing is frozen yet. The request waits for a supervisor other than you to approve or reject it."
          confirm="Send for approval"
          variant="danger"
          onClose={() => setOpen(false)}
          onSubmit={async (reason) => {
            await api(`/v1/wallets/${walletId}/freeze-requests`, { reason, ...(caseId ? { case_id: caseId } : {}) });
            onDone();
          }}
        />
      )}
    </>
  );
}
