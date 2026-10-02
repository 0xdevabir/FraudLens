"use client";

import { useParams } from "next/navigation";

import { HBars } from "@/components/charts";
import { Id } from "@/components/session";
import { TxnTable } from "@/components/tables";
import { Async, Badge, Card, Empty, PageHeader, Stat, Table, Td } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, taka, words } from "@/lib/format";
import type { Agent } from "@/lib/types";

function Detail({ agent }: { agent: Agent }) {
  const metrics = Object.entries(agent.metrics)
    .filter(([, metric]) => metric.z != null)
    .sort(([, a], [, b]) => (b.z ?? 0) - (a.z ?? 0));
  return (
    <>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            Agent <Id value={agent.agent_id} link={false} />
            {agent.review_suggested && <Badge tone="red">Review suggested</Badge>}
            {!agent.eligible && <Badge>Too little activity to rank</Badge>}
          </span>
        }
        sub={`${agent.district} · ranked ${num(agent.rank)} by risk. The score compares this agent with the others; it does not say the agent did anything wrong.`}
      />
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Risk score" value={agent.risk == null ? "–" : num(agent.risk, 1)} tone={agent.review_suggested ? "bad" : "plain"} sub="distance from a typical agent" />
        <Stat label="Cash-outs" value={num(agent.n_cashouts)} sub={taka(agent.cashout_value, true)} />
        <Stat label="Cash-ins" value={num(agent.n_cashins)} />
        <Stat label="Customers" value={num(agent.n_customers)} sub={`${num(agent.confirmed_fraud_customers?.length ?? 0)} confirmed as fraud`} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="What stands out" hint="Each reason is a measure where this agent is far from the others.">
          {agent.reasons.length ? (
            <ul className="space-y-2 text-sm">
              {agent.reasons.map((reason) => (
                <li key={reason.metric} className="flex items-start justify-between gap-3">
                  <span className="text-fg">{reason.metric === "avg_cashout" ? "Unusually large cash-outs on average" : `Unusually many ${reason.text}`}</span>
                  <Badge tone={reason.z >= 3 ? "red" : "amber"}>{num(reason.z, 1)}σ above typical</Badge>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>Nothing about this agent is unusual.</Empty>
          )}
        </Card>
        <Card title="Every measure" hint="Standard deviations from the typical agent. Above 3 is rare.">
          <HBars
            rows={metrics.map(([name, metric]) => ({
              label: words(name),
              value: Math.max(metric.z ?? 0, 0),
              note: `${num(metric.z ?? 0, 1)}σ · value ${num(metric.value ?? 0, (metric.value ?? 0) >= 100 ? 0 : 3)}`,
              color: (metric.z ?? 0) >= 3 ? "var(--color-bad)" : "var(--color-fg-4)",
            }))}
            format={() => ""}
            limit={{ value: 3, label: "3σ" }}
          />
        </Card>
        <Card title="Busiest customers" flush>
          {agent.top_customers?.length ? (
            <Table head={["Wallet", "Transactions", ""]}>
              {agent.top_customers.map((customer) => (
                <tr key={customer.wallet_id}>
                  <Td><Id value={customer.wallet_id} /></Td>
                  <Td right>{num(customer.transactions)}</Td>
                  <Td>{agent.confirmed_fraud_customers?.includes(customer.wallet_id) && <Badge tone="red">confirmed fraud</Badge>}</Td>
                </tr>
              ))}
            </Table>
          ) : (
            <Empty>No customers seen.</Empty>
          )}
        </Card>
        <Card title="Confirmed-fraud wallets that cashed out here">
          {agent.confirmed_fraud_customers?.length ? (
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
              {agent.confirmed_fraud_customers.map((id) => <Id key={id} value={id} />)}
            </div>
          ) : (
            <Empty>None.</Empty>
          )}
        </Card>
      </div>
      <Card title="Recent cash-outs" className="mt-4" flush>
        <TxnTable rows={agent.recent_cash_outs ?? []} />
      </Card>
    </>
  );
}

export default function AgentPage() {
  const { id } = useParams<{ id: string }>();
  const valid = /^[A-Za-z0-9_-]{1,32}$/.test(id);
  const agent = useApi<Agent>(valid ? `/v1/agents/${id}` : null);
  if (!valid) return <Empty>That is not an agent number.</Empty>;
  return <Async state={agent}>{(data) => <Detail agent={data} />}</Async>;
}
