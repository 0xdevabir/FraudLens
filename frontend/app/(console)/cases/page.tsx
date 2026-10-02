"use client";

import Link from "next/link";
import { useState } from "react";

import { Id } from "@/components/session";
import { Async, Badge, Button, Card, Chip, Empty, PageHeader, StatusBadge, Table, Td, TierBadge } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { num, taka, when, words } from "@/lib/format";
import type { CaseRow } from "@/lib/types";

const STATUSES = ["open", "in_review", "escalated", "closed"] as const;
const PAGE = 50;

export default function CasesPage() {
  const [statuses, setStatuses] = useState<string[]>(["open", "in_review", "escalated"]);
  const [assigned, setAssigned] = useState<"" | "me" | "unassigned">("");
  const [page, setPage] = useState(0);
  const cases = useApi<{ total: number; cases: CaseRow[] }>(
    `/v1/cases${qs({ status: statuses, assigned, limit: PAGE, offset: page * PAGE })}`,
  );
  const total = cases.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  function pick<T>(apply: (value: T) => void, value: T) {
    apply(value);
    setPage(0);
  }

  return (
    <>
      <PageHeader
        title="Cases"
        sub="One case per wallet under investigation. Held payments stay held until a reviewer closes the case with a verdict."
      />
      <Card flush>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-4 py-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-xs font-medium text-fg-3">Status</span>
            {STATUSES.map((status) => (
              <Chip
                key={status}
                on={statuses.includes(status)}
                onClick={() => pick(setStatuses, statuses.includes(status) ? statuses.filter((s) => s !== status) : [...statuses, status])}
              >
                {words(status)}
              </Chip>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-xs font-medium text-fg-3">Assigned</span>
            <Chip on={assigned === ""} onClick={() => pick(setAssigned, "")}>Anyone</Chip>
            <Chip on={assigned === "me"} onClick={() => pick(setAssigned, "me")}>Me</Chip>
            <Chip on={assigned === "unassigned"} onClick={() => pick(setAssigned, "unassigned")}>Nobody</Chip>
          </div>
          <span className="ml-auto text-xs text-fg-3">{cases.data ? `${num(total)} cases` : ""}</span>
        </div>
        <Async state={cases}>
          {(data) =>
            data.cases.length ? (
              <Table head={["Case", "Priority", "Status", "Wallet", "Alerts", "Held", "Assigned to", "Opened", "Review due", "Verdict"]}>
                {data.cases.map((row) => (
                  <tr key={row.id} className="hover:bg-wash">
                    <Td><Link href={`/cases/${row.id}`} className="font-medium text-info hover:underline">#{row.id}</Link></Td>
                    <Td><TierBadge tier={row.priority} /></Td>
                    <Td><StatusBadge status={row.status} /></Td>
                    <Td><Id value={row.subject_id} caseId={row.id} /></Td>
                    <Td right>{num(row.alerts)}</Td>
                    <Td right className="whitespace-nowrap">
                      {row.held ? `${taka(row.held_amount)} · ${num(row.held)}` : <span className="text-fg-4">–</span>}
                    </Td>
                    <Td>{row.assignee ?? <span className="text-fg-4">nobody</span>}</Td>
                    <Td className="whitespace-nowrap text-fg-2">{when(row.opened_at)}</Td>
                    <Td className="whitespace-nowrap text-fg-2">
                      {row.sla_due_at ? when(row.sla_due_at) : "–"} {row.overdue && <Badge tone="red">overdue</Badge>}
                    </Td>
                    <Td><StatusBadge status={row.verdict} /></Td>
                  </tr>
                ))}
              </Table>
            ) : (
              <Empty>No cases match.</Empty>
            )
          }
        </Async>
        <div className="flex items-center justify-between border-t border-line px-4 py-2 text-xs text-fg-3">
          <span>Page {page + 1} of {num(pages)}</span>
          <span className="flex gap-2">
            <Button small disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
            <Button small disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</Button>
          </span>
        </div>
      </Card>
    </>
  );
}
