"use client";

import { Id } from "@/components/session";
import { Async, Badge, Card, Empty, PageHeader, Table, Td } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, taka } from "@/lib/format";
import type { Agent } from "@/lib/types";

export default function AgentsPage() {
  const agents = useApi<Agent[]>("/v1/agents/risk?limit=100");
  return (
    <>
      <PageHeader
        title="Agent risk"
        sub="Cash-out agents ranked by how far their business sits from other agents of the same size: who they serve, how fast money arrives and leaves, and how much of it is their own cash-in coming straight back. A high rank is a reason to look, not a finding."
      />
      <Card flush>
        <Async state={agents}>
          {(data) =>
            data.length ? (
              <Table head={["Rank", "Agent", "District", "Risk", "", "Cash-outs", "Cash-out value", "Customers", "What stands out"]}>
                {data.map((agent) => (
                  <tr key={agent.agent_id} className="hover:bg-wash">
                    <Td right className="text-fg-3">{agent.rank}</Td>
                    <Td><Id value={agent.agent_id} /></Td>
                    <Td>{agent.district}</Td>
                    <Td right className="font-medium">{agent.risk == null ? "–" : num(agent.risk, 1)}</Td>
                    <Td>
                      {agent.review_suggested ? <Badge tone="red">review suggested</Badge> : !agent.eligible ? <Badge title="Too few cash-outs to compare fairly">too little activity</Badge> : null}
                    </Td>
                    <Td right>{num(agent.n_cashouts)}</Td>
                    <Td right>{taka(agent.cashout_value, true)}</Td>
                    <Td right>{num(agent.n_customers)}</Td>
                    <Td className="max-w-md whitespace-normal text-fg-2">{agent.reasons[0]?.text ?? "nothing unusual"}</Td>
                  </tr>
                ))}
              </Table>
            ) : (
              <Empty>No agents have been scored.</Empty>
            )
          }
        </Async>
      </Card>
    </>
  );
}
