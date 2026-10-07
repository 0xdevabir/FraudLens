"use client";

import Link from "next/link";
import { useState } from "react";

import { ExportButton, SavedViewsBar } from "@/components/filterbar";
import { Id, useSession } from "@/components/session";
import { Async, Badge, Button, Card, Chip, Empty, inputClass, PageHeader, SlaBadge, Stat, StatusBadge, Table, Td, TierBadge } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { type Filters, one, toggled, useSavedViews, useUrlFilters } from "@/lib/filters";
import { num, taka, when, words } from "@/lib/format";
import type { CaseRow, Workload } from "@/lib/types";

const STATUSES = ["open", "in_review", "escalated", "closed"] as const;
const PRIORITIES = ["hold", "step_up", "warn"] as const;
const SLA = ["breached", "at_risk", "ok"] as const;
const KEYS = ["status", "assigned", "priority", "sla", "verdict", "q"];
const OPEN: Filters = { status: ["open", "in_review", "escalated"] };
const PAGE = 50;

function WorkloadPanel() {
  const board = useApi<Workload>("/v1/cases/workload", 30_000);
  return (
    <Card
      title="Workload"
      hint="Open cases per reviewer. A case is due soon once less than a third of its review window is left."
      flush
    >
      <Async state={board}>
        {(data) => (
          <>
            <div className="grid gap-3 border-b border-line p-4 sm:grid-cols-2 xl:grid-cols-4">
              <Stat label="Open cases" value={num(data.totals.open)} sub={`${num(data.totals.escalated)} escalated`} />
              <Stat label="Overdue" value={num(data.totals.breached)} sub="review deadline passed" />
              <Stat label="Due soon" value={num(data.totals.at_risk)} sub="deadline close" />
              <Stat label="Money held" value={taka(data.totals.held_amount, true)} sub="waiting for a person" />
            </div>
            {data.reviewers.length ? (
              <Table head={["Reviewer", "Open", "Escalated", "Due soon", "Overdue", "Held", "Oldest case", "Next deadline"]}>
                {data.reviewers.map((row) => (
                  <tr key={row.assignee_id ?? "none"}>
                    <Td className="font-medium">{row.assignee ?? <span className="text-fg-3">Unassigned</span>}</Td>
                    <Td right>{num(row.open)}</Td>
                    <Td right>{num(row.escalated)}</Td>
                    <Td right>{row.at_risk ? <Badge tone="amber">{row.at_risk}</Badge> : <span className="text-fg-4">0</span>}</Td>
                    <Td right>{row.breached ? <Badge tone="red">{row.breached}</Badge> : <span className="text-fg-4">0</span>}</Td>
                    <Td right>{taka(row.held_amount)}</Td>
                    <Td className="whitespace-nowrap text-fg-2">{when(row.oldest_opened_at)}</Td>
                    <Td className="whitespace-nowrap text-fg-2">{row.next_due_at ? when(row.next_due_at) : "–"}</Td>
                  </tr>
                ))}
              </Table>
            ) : (
              <Empty>No open cases.</Empty>
            )}
          </>
        )}
      </Async>
    </Card>
  );
}

export default function CasesPage() {
  const { canApprove } = useSession();
  const [filters, setFilters, ready] = useUrlFilters(KEYS, OPEN);
  const [views, saveView, removeView] = useSavedViews("cases");
  const [page, setPage] = useState(0);
  const [draft, setDraft] = useState<string | null>(null);

  const query = {
    status: filters.status ?? [],
    assigned: one(filters, "assigned"),
    priority: filters.priority ?? [],
    sla: filters.sla ?? [],
    verdict: one(filters, "verdict"),
    q: one(filters, "q"),
  };
  const cases = useApi<{ total: number; cases: CaseRow[] }>(
    ready ? `/v1/cases${qs({ ...query, limit: PAGE, offset: page * PAGE })}` : null,
    15_000,
  );
  const total = cases.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const search = draft ?? one(filters, "q");

  function change(next: Filters) {
    setFilters(next);
    setPage(0);
  }

  return (
    <>
      <PageHeader tour="cases-header"
        title="Cases"
        sub="One case per wallet under investigation. Held payments stay held until a reviewer closes the case with a verdict."
        actions={<ExportButton path={`/v1/cases.csv${qs(query)}`} name="fraudlens-cases" canReveal={canApprove} />}
      />
      <div className="mb-4"><WorkloadPanel /></div>
      <Card flush tour="cases-list">
        <div className="space-y-2 border-b border-line px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-xs font-medium text-fg-3">Status</span>
              {STATUSES.map((status) => (
                <Chip key={status} on={(filters.status ?? []).includes(status)} onClick={() => change(toggled(filters, "status", status))}>{words(status)}</Chip>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-xs font-medium text-fg-3">Assigned</span>
              <Chip on={!one(filters, "assigned")} onClick={() => change({ ...filters, assigned: [] })}>Anyone</Chip>
              <Chip on={one(filters, "assigned") === "me"} onClick={() => change({ ...filters, assigned: ["me"] })}>Me</Chip>
              <Chip on={one(filters, "assigned") === "unassigned"} onClick={() => change({ ...filters, assigned: ["unassigned"] })}>Nobody</Chip>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-xs font-medium text-fg-3">Priority</span>
              {PRIORITIES.map((priority) => (
                <Chip key={priority} on={(filters.priority ?? []).includes(priority)} onClick={() => change(toggled(filters, "priority", priority))}>{words(priority)}</Chip>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-xs font-medium text-fg-3">Deadline</span>
              {SLA.map((state) => (
                <Chip key={state} on={(filters.sla ?? []).includes(state)} onClick={() => change(toggled(filters, "sla", state))}>
                  {state === "breached" ? "Overdue" : state === "at_risk" ? "Due soon" : "On time"}
                </Chip>
              ))}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <form
              className="flex items-center gap-1.5"
              onSubmit={(event) => {
                event.preventDefault();
                change({ ...filters, q: search.trim() ? [search.trim()] : [] });
                setDraft(null);
              }}
            >
              <input
                aria-label="Search by case number or wallet"
                placeholder="Case number or wallet"
                value={search}
                maxLength={32}
                pattern="[A-Za-z0-9_\-]*"
                onChange={(event) => setDraft(event.target.value)}
                className={`${inputClass} w-52 py-1 text-xs`}
              />
              <Button small type="submit">Search</Button>
            </form>
            <select
              aria-label="Verdict"
              className={`${inputClass} py-1 text-xs`}
              value={one(filters, "verdict")}
              onChange={(event) => change({ ...filters, verdict: event.target.value ? [event.target.value] : [] })}
            >
              <option value="">Any verdict</option>
              <option value="confirmed_fraud">Confirmed fraud</option>
              <option value="legitimate">Legitimate</option>
              <option value="inconclusive">Inconclusive</option>
            </select>
            <Button small onClick={() => change(OPEN)}>Reset</Button>
            <span className="ml-auto text-xs text-fg-3">{cases.data ? `${num(total)} cases` : ""}</span>
          </div>
          <SavedViewsBar views={views} current={filters} onApply={change} onSave={saveView} onRemove={removeView} />
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
                      {row.sla_due_at ? <>{when(row.sla_due_at)} <SlaBadge state={row.sla_state} seconds={row.sla_remaining_seconds} /></> : "–"}
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
