"use client";

import { useState } from "react";

import { Id } from "@/components/session";
import { Async, Badge, Button, Card, Empty, inputClass, PageHeader, Table, Td, type Tone } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { clock, day, maskId, words } from "@/lib/format";
import type { AuditRow } from "@/lib/types";

const PAGE = 100;
const ACTIONS = [
  "auth.login", "auth.login_failed", "wallet.view", "pii.reveal",
  "case.open", "case.assign", "case.note", "case.escalate", "case.verdict",
  "freeze.request", "freeze.approve", "freeze.reject", "freeze.lift",
  "customer.proceed", "customer.cancel", "customer.report",
  "demo.payment", "demo.clock_advanced",
];
const OBJECTS = ["wallet", "agent", "device", "case", "transaction", "freeze_request", "clock"];
const PERSONAL = /^[WAD]\d{4,}$/;
const SAFE = /^[\w.:-]{0,64}$/;

function tone(action: string): Tone {
  if (action.startsWith("freeze") || action === "case.verdict") return "red";
  if (action === "pii.reveal") return "violet";
  if (action.startsWith("auth")) return "slate";
  if (action.startsWith("demo") || action.startsWith("customer")) return "amber";
  return "blue";
}

/** Details are shown as recorded, except that wallet, agent and handset ids stay masked like everywhere else. */
function detail(value: unknown): string {
  if (typeof value === "string") return PERSONAL.test(value) ? maskId(value) : value;
  if (Array.isArray(value)) return value.map(detail).join(", ");
  if (value && typeof value === "object") {
    return Object.entries(value)
      .map(([key, inner]) => [key, detail(inner)])
      .filter(([, text]) => text !== "")
      .map(([key, text]) => `${words(key).toLowerCase()}: ${text}`)
      .join(" · ");
  }
  return String(value ?? "");
}

export default function AuditPage() {
  const [draft, setDraft] = useState({ actor: "", action: "", object_type: "", object_id: "" });
  const [filter, setFilter] = useState(draft);
  // ids of the first row of each earlier page, so "Newer" can step back
  const [before, setBefore] = useState<number[]>([]);
  const rows = useApi<AuditRow[]>(`/v1/audit${qs({ ...filter, limit: PAGE, before_id: before.at(-1) })}`);
  const invalid = Object.values(draft).some((value) => !SAFE.test(value.trim()));

  function apply(event: React.FormEvent) {
    event.preventDefault();
    if (invalid) return;
    setBefore([]);
    setFilter({ ...draft, actor: draft.actor.trim(), object_id: draft.object_id.trim() });
  }

  const field = (name: keyof typeof draft) => ({
    value: draft[name],
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setDraft({ ...draft, [name]: event.target.value }),
  });

  return (
    <>
      <PageHeader
        title="Audit log"
        sub="Every sign-in, every wallet opened, every identifier revealed and every decision a person took, with who did it and when. Rows are only ever added."
      />
      <Card className="mb-4">
        <form onSubmit={apply} className="flex flex-wrap items-end gap-3 text-xs text-slate-600">
          <label>Who (username)<input className={`${inputClass} mt-1 block w-36`} maxLength={64} {...field("actor")} /></label>
          <label>
            Action
            <select className={`${inputClass} mt-1 block w-44`} {...field("action")}>
              <option value="">Any</option>
              {ACTIONS.map((action) => <option key={action}>{action}</option>)}
            </select>
          </label>
          <label>
            On
            <select className={`${inputClass} mt-1 block w-36`} {...field("object_type")}>
              <option value="">Anything</option>
              {OBJECTS.map((object) => <option key={object}>{object}</option>)}
            </select>
          </label>
          <label>Its id<input className={`${inputClass} mt-1 block w-36`} maxLength={64} placeholder="W0001234 or 76" {...field("object_id")} /></label>
          <Button type="submit" variant="primary" disabled={invalid}>Filter</Button>
          {invalid && <span className="text-red-700">Letters, digits and . : _ - only.</span>}
        </form>
      </Card>
      <Card flush>
        <Async state={rows}>
          {(data) =>
            data.length ? (
              <>
                <Table head={["Recorded (Dhaka)", "Who", "Action", "On", "Details", "From"]}>
                  {data.map((row) => (
                    <tr key={row.id}>
                      <Td className="whitespace-nowrap tabular-nums">{day(row.at)} {clock(row.at)}</Td>
                      <Td>{row.actor ?? "system"}{row.role && <span className="ml-1 text-xs text-slate-400">{row.role}</span>}</Td>
                      <Td><Badge tone={tone(row.action)}>{row.action}</Badge></Td>
                      <Td>
                        {row.object_type ? (
                          <span className="inline-flex items-center gap-1">
                            <span className="text-xs text-slate-500">{row.object_type}</span>
                            {row.object_id && PERSONAL.test(row.object_id) ? <Id value={row.object_id} /> : <span className="font-mono text-xs">{row.object_id}</span>}
                          </span>
                        ) : "–"}
                      </Td>
                      <Td className="whitespace-normal text-xs text-slate-600">{detail(row.detail)}</Td>
                      <Td className="font-mono text-xs text-slate-500" title={row.request_id ? `request ${row.request_id}` : undefined}>{row.ip ?? "–"}</Td>
                    </tr>
                  ))}
                </Table>
                <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
                  <span>Entries {data.at(-1)?.id} to {data[0].id}, newest first. Times here are real time, not the simulated platform clock.</span>
                  <span className="flex gap-2">
                    <Button small disabled={!before.length} onClick={() => setBefore(before.slice(0, -1))}>Newer</Button>
                    <Button small disabled={data.length < PAGE} onClick={() => setBefore([...before, data.at(-1)!.id])}>Older</Button>
                  </span>
                </div>
              </>
            ) : (
              <Empty>Nothing recorded matches.</Empty>
            )
          }
        </Async>
      </Card>
    </>
  );
}
