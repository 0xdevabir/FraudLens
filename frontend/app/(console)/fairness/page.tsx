"use client";

import { useState } from "react";

import { HBars } from "@/components/charts";
import { Async, Badge, Card, Chip, Empty, PageHeader, Stat, Table, Td } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, pct, words } from "@/lib/format";
import type { FairRow, Insights, Report } from "@/lib/types";

const DIMENSION: Record<string, string> = {
  region: "Region",
  account_age: "Account age",
  balance_tier: "Balance",
  area_type: "Urban or rural",
  segment: "Customer segment",
  channel: "App or USSD",
};
/** A group interrupted this many times as often as everyone else is worth a second look. */
const NOTABLE = 2;

/** Segment and channel come as identifiers (`garment_worker`, `ussd`); the other groups are already phrases. */
function label(group: string): string {
  return group === "ussd" ? "USSD" : /^[a-z_]+$/.test(group) ? words(group) : group;
}

function Dimension({ name, rows, overall }: { name: string; rows: FairRow[]; overall: number }) {
  const worst = Math.max(0, ...rows.filter((row) => !row.too_small).map((row) => row.ratio_to_overall ?? 0));
  return (
    <Card
      title={DIMENSION[name] ?? words(name)}
      hint="Share of honest payments that were interrupted."
      actions={worst >= NOTABLE ? <Badge tone="amber">up to {num(worst, 1)}× the overall rate</Badge> : <Badge tone="green">within {NOTABLE}× of overall</Badge>}
    >
      <HBars
        format={(value) => pct(value, 2)}
        limit={{ value: overall, label: `overall ${pct(overall, 2)}` }}
        rows={rows.map((row) => ({
          label: label(row.group),
          value: row.false_alert_rate,
          muted: row.too_small,
          color: !row.too_small && (row.ratio_to_overall ?? 0) >= NOTABLE ? "#f59e0b" : "#64748b",
          note: row.too_small ? "too few to judge" : `${num(row.ratio_to_overall, 2)}×`,
        }))}
      />
      <Table className="mt-4 border-t border-slate-100" head={["Group", "Honest payments", "Interrupted", "Held", "Fraud caught"]}>
        {rows.map((row) => (
          <tr key={row.group} className={row.too_small ? "text-slate-400" : undefined}>
            <Td>{label(row.group)}</Td>
            <Td right>{num(row.legitimate)}</Td>
            <Td right title={`${num(row.false_alerts)} payments`}>{pct(row.false_alert_rate, 2)}</Td>
            <Td right>{pct(row.false_hold_rate, 3)}</Td>
            <Td right title={`${num(row.victim_transfers)} victim payments in this group`}>
              {row.victim_transfers ? pct(row.victim_transfers_alerted, 0) : "–"}
            </Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function Fairness({ fairness, version }: { fairness: Insights["fairness"]; version: string }) {
  const [side, setSide] = useState<"sender" | "receiver">("sender");
  const groups = fairness[side];
  const order = [...Object.keys(DIMENSION).filter((name) => name in groups), ...Object.keys(groups).filter((name) => !(name in DIMENSION))];
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Honest payments interrupted" value={pct(fairness.overall.false_alert_rate, 2)} sub="overall false-positive rate, any alert" />
        <Stat label="Honest payments held" value={pct(fairness.overall.false_hold_rate, 3)} sub="held for a reviewer, the costliest mistake" />
        <Stat
          label="Largest gap by sender group"
          value={`${num(fairness.sender_largest_ratio, 1)}×`}
          tone={fairness.sender_largest_ratio >= NOTABLE ? "warn" : "good"}
          sub="the most-interrupted group against the overall rate"
        />
        <Stat
          label="Largest gap by receiver group"
          value={`${num(fairness.receiver_largest_ratio, 1)}×`}
          tone={fairness.receiver_largest_ratio >= NOTABLE ? "warn" : "good"}
          sub="who the money was going to"
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-500">Group payments by</span>
        <Chip on={side === "sender"} onClick={() => setSide("sender")}>the customer sending</Chip>
        <Chip on={side === "receiver"} onClick={() => setSide("receiver")}>the wallet receiving</Chip>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        {order.map((name) => <Dimension key={`${side}-${name}`} name={name} rows={groups[name]} overall={fairness.overall.false_alert_rate} />)}
      </div>

      <Card className="mt-4" title="How to read this">
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-700">
          <li>
            A false alert is an honest payment that was warned, checked again or held. The report asks whether some customers carry more of that
            cost than others, measured on model {version} over the held-out test period.
          </li>
          <li>
            New accounts are interrupted most. That is partly the signal working, because mule wallets are new, and partly a cost carried by
            honest new customers. It is the gap to watch: account age and balance are things the model sees.
          </li>
          <li>
            Region, urban or rural, customer segment and channel are not model inputs. They are joined on afterwards, only to produce this
            report.
          </li>
          <li>
            Groups with fewer than {num(fairness.min_group_rows)} honest payments are greyed out: their rates move too much with a handful of cases.
          </li>
          <li>
            “Fraud caught” is the other half: an even false-alert rate is no use if one group’s victims are protected less.
          </li>
          <li>The customers are simulated, so this shows the method and where the gaps open, not how real customers would be treated.</li>
        </ul>
      </Card>
    </>
  );
}

export default function FairnessPage() {
  const report = useApi<Report>("/v1/model/report");
  return (
    <>
      <PageHeader
        title="Fairness report"
        sub="Who pays for false alarms: the false-positive rate by region, account age, balance and other customer groups."
      />
      <Async state={report}>
        {(data) =>
          data.insights?.fairness ? (
            <Fairness fairness={data.insights.fairness} version={data.model_version} />
          ) : (
            <Empty>This model version was evaluated without a fairness breakdown.</Empty>
          )
        }
      </Async>
    </>
  );
}
