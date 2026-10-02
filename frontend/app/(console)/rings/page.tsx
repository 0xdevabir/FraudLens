"use client";

import Link from "next/link";

import { Async, Badge, Card, Empty, PageHeader, Table, Td } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, ringLabel, taka } from "@/lib/format";
import type { Ring } from "@/lib/types";

export default function RingsPage() {
  const rings = useApi<Ring[]>("/v1/rings");
  return (
    <>
      <PageHeader
        title="Rings"
        sub="Wallets that belong together: they share handsets with, or pass money between, wallets already confirmed as fraud. A ring is a lead for a reviewer, not a verdict."
      />
      <Card flush>
        <Async state={rings}>
          {(data) =>
            data.length ? (
              <Table head={["Ring", "Wallets", "Confirmed fraud", "Linked by handset only", "Takeover victims", "Shared handsets", "Transfers inside", "Money received", "Frozen"]}>
                {data.map((ring) => (
                  <tr key={ring.ring_id} className="hover:bg-slate-50">
                    <Td><Link href={`/rings/${ring.ring_id}`} className="font-mono text-xs font-medium text-sky-700 hover:underline">{ringLabel(ring.ring_id)}</Link></Td>
                    <Td right>{num(ring.size)}</Td>
                    <Td right>{num(ring.confirmed.length)}</Td>
                    <Td right>{num(ring.linked_only.length)}</Td>
                    <Td right>{num(ring.takeover_victims.length)}</Td>
                    <Td right>{num(ring.shared_devices.length)}</Td>
                    <Td right>{num(ring.transfer_links)}</Td>
                    <Td right>{taka(ring.received_total)}</Td>
                    <Td right>
                      {ring.frozen.length ? <Badge tone="blue">{ring.frozen.length} of {ring.size}</Badge> : <span className="text-slate-400">none</span>}
                    </Td>
                  </tr>
                ))}
              </Table>
            ) : (
              <Empty>No rings have been detected.</Empty>
            )
          }
        </Async>
      </Card>
    </>
  );
}
