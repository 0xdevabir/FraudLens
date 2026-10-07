"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Id } from "@/components/session";
import { Async, Button, Card, Empty, inputClass, PageHeader, StatusBadge, Table, Td, TierBadge } from "@/components/ui";
import { useApi } from "@/lib/api";
import { idKind, num, ringLabel, taka } from "@/lib/format";
import type { CaseRow, Ring } from "@/lib/types";

const ID = /^[A-Za-z0-9_-]{1,32}$/;

export default function NetworkPage() {
  const router = useRouter();
  const [text, setText] = useState("");
  const rings = useApi<Ring[]>("/v1/rings");
  const cases = useApi<{ cases: CaseRow[] }>("/v1/cases?status=open&status=in_review&status=escalated&limit=8");
  const id = text.trim().toUpperCase();
  const valid = ID.test(id);

  function go(event: React.FormEvent) {
    event.preventDefault();
    if (valid) router.push(idKind(id) === "agent" ? `/agents/${id}` : `/wallets/${id}`);
  }

  return (
    <>
      <PageHeader tour="network-header"
        title="Network explorer"
        sub="Start from a wallet or an agent and follow the money: who paid in, where it went, which handsets are shared."
      />
      <Card tour="network-search" className="mb-4">
        <form onSubmit={go} className="flex flex-wrap items-end gap-2">
          <div>
            <label htmlFor="lookup" className="mb-1 block text-xs font-medium text-fg-2">Wallet or agent number</label>
            <input
              id="lookup"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="W0000324 or A00394"
              autoComplete="off"
              spellCheck={false}
              className={`${inputClass} w-64 font-mono`}
            />
          </div>
          <Button type="submit" variant="primary" disabled={!valid}>Open</Button>
          {text.trim() && !valid && <span className="text-xs text-bad">Letters, digits, dash and underscore only.</span>}
        </form>
        <p className="mt-2 text-xs text-fg-3">Opening a wallet is recorded in the audit log.</p>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Wallets under investigation" hint="Subjects of the open cases." flush>
          <Async state={cases}>
            {(data) =>
              data.cases.length ? (
                <Table head={["Wallet", "Case", "Priority", "Status", "Held"]}>
                  {data.cases.map((row) => (
                    <tr key={row.id} className="hover:bg-wash">
                      <Td><Id value={row.subject_id} caseId={row.id} /></Td>
                      <Td><Link href={`/cases/${row.id}`} className="text-info hover:underline">#{row.id}</Link></Td>
                      <Td><TierBadge tier={row.priority} /></Td>
                      <Td><StatusBadge status={row.status} /></Td>
                      <Td right>{row.held ? taka(row.held_amount) : "–"}</Td>
                    </tr>
                  ))}
                </Table>
              ) : (
                <Empty>No open cases.</Empty>
              )
            }
          </Async>
        </Card>
        <Card tour="network-rings" title="Largest rings" hint="Groups of wallets tied together by shared handsets and transfers." actions={<Link href="/rings" className="text-xs text-info hover:underline">All rings</Link>} flush>
          <Async state={rings}>
            {(data) =>
              data.length ? (
                <Table head={["Ring", "Wallets", "Confirmed", "Received"]}>
                  {data.slice(0, 8).map((ring) => (
                    <tr key={ring.ring_id} className="hover:bg-wash">
                      <Td><Link href={`/rings/${ring.ring_id}`} className="font-mono text-xs text-info hover:underline">{ringLabel(ring.ring_id)}</Link></Td>
                      <Td right>{num(ring.size)}</Td>
                      <Td right>{num(ring.confirmed.length)}</Td>
                      <Td right>{taka(ring.received_total, true)}</Td>
                    </tr>
                  ))}
                </Table>
              ) : (
                <Empty>No rings detected.</Empty>
              )
            }
          </Async>
        </Card>
      </div>
    </>
  );
}
