"use client";

import Link from "next/link";

import { taka, when, words } from "@/lib/format";
import type { Alert, Freeze, Txn } from "@/lib/types";

import { ScoreBar } from "./charts";
import { Id } from "./session";
import { Badge, Empty, StatusBadge, Table, Td, TierBadge } from "./ui";

/** "model", or the rule that overrode it. */
export function DecidedBy({ value }: { value: string }) {
  if (value === "model") return <Badge>Model</Badge>;
  return <Badge tone="violet" title={value}>{value.replace(/^rule:/, "Rule ").split("_")[0]}</Badge>;
}

export function AlertTable({ alerts, showCase = true }: { alerts: Alert[]; showCase?: boolean }) {
  if (!alerts.length) return <Empty>No alerts match.</Empty>;
  return (
    <Table head={["Time", "Tier", "Risk", "Payment", "Amount", "From → to", "Outcome", "Decided by", ...(showCase ? ["Case"] : []), "Why"]}>
      {alerts.map((alert) => (
        <tr key={alert.txn_id} className="hover:bg-slate-50">
          <Td className="whitespace-nowrap text-slate-600">{when(alert.ts)}</Td>
          <Td><TierBadge tier={alert.tier} /></Td>
          <Td><ScoreBar score={alert.risk_score} /></Td>
          <Td className="whitespace-nowrap">
            <Link href={`/decisions/${alert.txn_id}`} className="text-sky-700 hover:underline">{words(alert.type)}</Link>
            <div className="text-xs text-slate-500">{alert.district}</div>
          </Td>
          <Td right className="whitespace-nowrap font-medium">{taka(alert.amount)}</Td>
          <Td className="whitespace-nowrap">
            <div><Id value={alert.sender_id} caseId={alert.case_id} /></div>
            <div><span className="text-slate-400">→ </span><Id value={alert.receiver_id} caseId={alert.case_id} /></div>
          </Td>
          <Td>
            <StatusBadge status={alert.status} />
            {alert.customer_response && <div className="mt-0.5 text-xs text-slate-500">customer: {words(alert.customer_response)}</div>}
          </Td>
          <Td><DecidedBy value={alert.decided_by} /></Td>
          {showCase && (
            <Td>
              {alert.case_id ? (
                <Link href={`/cases/${alert.case_id}`} className="text-sky-700 hover:underline">#{alert.case_id}</Link>
              ) : (
                <span className="text-slate-400">–</span>
              )}
            </Td>
          )}
          <Td className="whitespace-normal text-slate-600"><span className="line-clamp-2 w-48 wrap-anywhere" title={alert.headline?.en}>{alert.headline?.en}</span></Td>
        </tr>
      ))}
    </Table>
  );
}

export function TxnTable({ rows, subject }: { rows: Txn[]; subject?: string }) {
  if (!rows.length) return <Empty>No transactions.</Empty>;
  return (
    <Table head={["Time", "Type", "Amount", "From", "To", "Status", "Tier"]}>
      {rows.map((row) => (
        <tr key={row.txn_id} className="hover:bg-slate-50">
          <Td className="whitespace-nowrap text-slate-600">{when(row.ts)}</Td>
          <Td className="whitespace-nowrap">
            {row.tier && row.tier !== "allow" ? (
              <Link href={`/decisions/${row.txn_id}`} className="text-sky-700 hover:underline">{words(row.type)}</Link>
            ) : (
              words(row.type)
            )}
          </Td>
          <Td right className="whitespace-nowrap">
            <span className={subject && row.sender_id === subject ? "text-slate-900" : "text-emerald-700"}>
              {subject ? (row.sender_id === subject ? "−" : "+") : ""}{taka(row.amount)}
            </span>
          </Td>
          <Td>{row.sender_id === subject ? <span className="text-slate-400">this wallet</span> : <Id value={row.sender_id} />}</Td>
          <Td>{row.receiver_id === subject ? <span className="text-slate-400">this wallet</span> : <Id value={row.receiver_id} />}</Td>
          <Td><StatusBadge status={row.status} /></Td>
          <Td>{row.tier ? <TierBadge tier={row.tier} /> : <span className="text-slate-400">–</span>}</Td>
        </tr>
      ))}
    </Table>
  );
}

export function FreezeList({ rows }: { rows: Freeze[] }) {
  if (!rows.length) return <Empty>No freeze has been requested.</Empty>;
  return (
    <ul className="divide-y divide-slate-100 text-sm">
      {rows.map((row) => (
        <li key={row.id} className="py-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={row.status} />
            <span className="text-slate-600">
              asked by {row.requester ?? `user ${row.requested_by}`} · {when(row.created_at)}
            </span>
          </div>
          <p className="mt-1 text-slate-800">{row.reason}</p>
          {row.status !== "pending" && (
            <p className="mt-1 text-xs text-slate-500">
              {words(row.status)} by {row.decider ?? "–"}: {row.decision_note}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}
