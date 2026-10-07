"use client";

import { useState } from "react";

import { HBars, Legend, LineChart } from "@/components/charts";
import { Async, Badge, Card, Chip, Empty, PageHeader, Stat, Table, Td } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, pct, words } from "@/lib/format";
import type { FairRow, Insights, Mitigation as MitigationReport, MitigationMetric, Report } from "@/lib/types";

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
          color: !row.too_small && (row.ratio_to_overall ?? 0) >= NOTABLE ? "var(--color-warn)" : "var(--color-fg-4)",
          note: row.too_small ? "too few to judge" : `${num(row.ratio_to_overall, 2)}×`,
        }))}
      />
      <Table className="mt-4 border-t border-line" head={["Group", "Honest payments", "Interrupted", "Held", "Fraud caught"]}>
        {rows.map((row) => (
          <tr key={row.group} className={row.too_small ? "text-fg-4" : undefined}>
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

const isRatio = (metric: MitigationMetric) => metric.key.includes("ratio");

function shown(metric: MitigationMetric, v: number | null): string {
  return isRatio(metric) ? `${num(v, 2)}×` : pct(v, 2);
}

/** A 95% interval of a change: ratios as they are, rates in percentage points. */
function interval(metric: MitigationMetric, [lo, hi]: [number | null, number | null]): string {
  const f = (v: number | null) => (v === null ? "–" : isRatio(metric) ? num(v, 2) : `${(v * 100).toFixed(2)} pp`);
  return `${f(lo)} to ${f(hi)}`;
}

const YOUNG = [
  { key: "receiver_young_ratio", name: "Wallet receiving, under 30 days", color: "var(--color-warn)" },
  { key: "sender_young_ratio", name: "Customer sending, under 30 days", color: "var(--color-info)" },
];

/** Policy v3: young wallets get cut-offs of their own. What it changed on the test period, and what closing the gap further would cost. */
function Mitigation({ m, side }: { m: MitigationReport; side: "sender" | "receiver" }) {
  const metric = (key: string) => m.before_after.metrics.find((row) => row.key === key);
  const headline = [
    { key: "receiver_young_ratio", label: "Wallet receiving, under 30 days", sub: "honest payments interrupted, against the overall rate" },
    { key: "sender_young_ratio", label: "Customer sending, under 30 days", sub: "the same, by who sends" },
    { key: "victim_recall", label: "Victim transfers alerted", sub: "warned, checked again or held" },
    { key: "taka_recall", label: "Victims’ money alerted", sub: "share of the taka scammed" },
  ];
  const path = m.fit.path_warn;
  const warn = (point: (typeof path)[number]) => Object.values(point.scales)[0].warn;
  const limit = path.findIndex((point, i) => point.admissible && !path[i + 1]?.admissible);
  const series = YOUNG.map((y) => ({ ...y, values: path.map((point) => point.test[y.key]) }));
  return (
    <>
      <h2 className="mt-8 text-base font-semibold">Narrowing the gap: policy {m.policy_version}</h2>
      <p className="mt-1 max-w-3xl text-sm text-fg-3">
        Payments to or from a wallet under 30 days old are scored against cut-offs of their own, fitted on the validation period and measured
        here on the {num(m.days)}-day test period ({num(m.rows.test)} payments) against {m.base_policy}. The rules, including the hard ones, are
        unchanged, and a held payment still waits for a person.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {headline.map((h) => {
          const row = metric(h.key);
          if (!row) return null;
          const ratio = isRatio(row);
          return (
            <Stat
              key={h.key}
              label={h.label}
              value={ratio ? `${num(row.before, 1)}× → ${num(row.after, 1)}×` : `${pct(row.before, 1)} → ${pct(row.after, 1)}`}
              tone={ratio ? ((row.after ?? 0) >= NOTABLE ? "warn" : "good") : "plain"}
              sub={`${h.sub} · change ${interval(row, row.difference_ci)} (95%)`}
            />
          );
        })}
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card
          title={`Before (${m.base_policy}) and after (${m.policy_version})`}
          hint={`Test period. Intervals from ${num(m.before_after.reps)} bootstrap resamples of ${m.before_after.resampled}.`}
          flush
        >
          <Table head={["", "Before", "After", "Change, 95% interval"]}>
            {m.before_after.metrics.map((row) => (
              <tr key={row.key}>
                <Td>{row.label}</Td>
                <Td right>{shown(row, row.before)}</Td>
                <Td right>{shown(row, row.after)}</Td>
                <Td right className="text-fg-3">{interval(row, row.difference_ci)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card
          title="What closing the gap costs"
          hint="Both segments’ warn cut-off raised together, as a multiple of the model’s. Past the guard a payment the model would hold for anyone would not even be warned, so a policy cannot go there."
        >
          <LineChart
            series={series}
            labels={path.map((point) => `×${num(warn(point), 1)}`)}
            format={(v) => `${num(v, 0)}×`}
            xTitle="Warn cut-off, times the model’s (test period)"
            mark={{ index: limit, label: "guard" }}
          />
          <div className="mt-2"><Legend items={series} /></div>
          <Table className="mt-4 border-t border-line" head={["Warn ×", "Receiving", "Sending", "Victim transfers alerted", "Victims’ money alerted", ""]}>
            {path.map((point) => (
              <tr key={warn(point)} className={point.admissible ? undefined : "text-fg-4"}>
                <Td>×{num(warn(point), 1)}</Td>
                <Td right>{num(point.test.receiver_young_ratio, 2)}×</Td>
                <Td right>{num(point.test.sender_young_ratio, 2)}×</Td>
                <Td right>{pct(point.test.victim_recall, 2)}</Td>
                <Td right>{pct(point.test.taka_recall, 2)}</Td>
                <Td>{point.admissible ? null : <Badge tone="slate">past the guard</Badge>}</Td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>

      <Card
        className="mt-4"
        title="Account age × channel × area"
        hint={`By ${side === "sender" ? "the customer sending" : "the wallet receiving"}: honest payments interrupted, ${m.base_policy} → ${m.policy_version}.`}
        flush
      >
        <Table head={["Group", "Honest payments", "Interrupted", "Against overall", "Fraud caught"]}>
          {m.intersectional[side].map((row) => (
            <tr key={row.group} className={row.too_small ? "text-fg-4" : undefined}>
              <Td>{row.group}{row.too_small ? " (too few to judge)" : ""}</Td>
              <Td right>{num(row.legitimate)}</Td>
              <Td right>{pct(row.false_alert_rate, 2)} → {pct(row.false_alert_rate_after, 2)}</Td>
              <Td right className={!row.too_small && (row.ratio_to_overall_after ?? 0) >= NOTABLE ? "text-warn" : undefined}>
                {num(row.ratio_to_overall, 1)}× → {num(row.ratio_to_overall_after, 1)}×
              </Td>
              <Td right>{row.victim_transfers ? `${pct(row.victim_transfers_alerted, 0)} → ${pct(row.victim_transfers_alerted_after, 0)}` : "–"}</Td>
            </tr>
          ))}
        </Table>
      </Card>
    </>
  );
}

function Fairness({ fairness, version, mitigation }: { fairness: Insights["fairness"]; version: string; mitigation?: MitigationReport | null }) {
  const [side, setSide] = useState<"sender" | "receiver">("sender");
  const groups = fairness[side];
  const order = [...Object.keys(DIMENSION).filter((name) => name in groups), ...Object.keys(groups).filter((name) => !(name in DIMENSION))];
  return (
    <>
      <div data-tour="fairness-kpis" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
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
        <span className="text-fg-3">Group payments by</span>
        <Chip on={side === "sender"} onClick={() => setSide("sender")}>the customer sending</Chip>
        <Chip on={side === "receiver"} onClick={() => setSide("receiver")}>the wallet receiving</Chip>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        {order.map((name) => <Dimension key={`${side}-${name}`} name={name} rows={groups[name]} overall={fairness.overall.false_alert_rate} />)}
      </div>

      {mitigation && <Mitigation m={mitigation} side={side} />}

      <Card className="mt-4" title="How to read this">
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-fg-2">
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
          {mitigation && (
            <li>
              Policy {mitigation.policy_version} trades a little detection for a fairer spread, and the numbers above say how much. Its cut-offs
              can only narrow the gap so far: much of what remains comes from rules and from honest payments the model scores very high.
            </li>
          )}
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
      <PageHeader tour="fairness-header"
        title="Fairness report"
        sub="Who pays for false alarms: the false-positive rate by region, account age, balance and other customer groups."
      />
      <Async state={report}>
        {(data) =>
          data.insights?.fairness ? (
            <Fairness fairness={data.insights.fairness} version={data.model_version} mitigation={data.mitigation} />
          ) : (
            <Empty>This model version was evaluated without a fairness breakdown.</Empty>
          )
        }
      </Async>
    </>
  );
}
