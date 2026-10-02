"use client";

import { useState } from "react";

import { Legend, LineChart } from "@/components/charts";
import { Async, Button, Card, Empty, PageHeader, Stat, Table, Td, inputClass } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, pct, taka } from "@/lib/format";
import type { ImpactPoint, Insights, Report } from "@/lib/types";

const TIER_NAME = { warn: "Warn", step_up: "Step-up check", hold: "Hold for review" } as const;
/** Hours of case work one reviewer gets through in a shift. */
const REVIEW_HOURS = 6;

/** The sweep point whose threshold is closest to `threshold` (thresholds span orders of magnitude). */
function nearest(points: ImpactPoint[], threshold: number): number {
  let best = 0;
  points.forEach((point, i) => {
    if (Math.abs(Math.log(point.threshold / threshold)) < Math.abs(Math.log(points[best].threshold / threshold))) best = i;
  });
  return best;
}

function signed(value: number, format: (value: number) => string): string {
  if (!value) return "same as today";
  return `${value > 0 ? "+" : "−"}${format(Math.abs(value))} vs today`;
}

function Simulator({ insights, version }: { insights: Insights; version: string }) {
  const points = insights.impact;
  const today = nearest(points, insights.thresholds.warn);
  const [index, setIndex] = useState(today);
  const [minutes, setMinutes] = useState(8);
  const point = points[index];
  const base = points[today];
  const atRisk = insights.at_thresholds.warn.taka_at_risk;
  const days = insights.days;
  const hours = (p: ImpactPoint) => (p.alerts_per_day * minutes) / 60;

  const xs = points.map((p) => p.alert_rate * 100);
  const labels = points.map((p) => pct(p.alert_rate, 2));
  const money = [
    { name: "Stopped, counting held mule cash-outs", color: "var(--color-good)", values: points.map((p) => p.taka_recall_with_exit_holds) },
    { name: "Stopped at the victim's own payment", color: "var(--color-info)", values: points.map((p) => p.taka_recall) },
    { name: "Share of alerts that are really fraud", color: "var(--color-fg-4)", values: points.map((p) => p.precision), dashed: true },
  ];
  const load = [
    { name: "Alerts a day", color: "var(--color-warn)", values: points.map((p) => p.alerts_per_day) },
    { name: "Of which honest payments", color: "var(--color-bad)", values: points.map((p) => p.false_alerts_per_day) },
  ];

  return (
    <>
      <Card>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-fg-3">Alert when the fraud probability is at least</div>
            <div className="text-3xl font-semibold tabular-nums">{pct(point.threshold, point.threshold < 0.01 ? 3 : 1)}</div>
            <div className="text-sm text-fg-3">
              which interrupts {pct(point.alert_rate, 2)} of payments
              {index === today ? " — the warn threshold in force today" : ""}
            </div>
          </div>
          <Button variant="ghost" small onClick={() => setIndex(today)} disabled={index === today}>Back to today’s threshold</Button>
        </div>
        <input
          type="range"
          min={0}
          max={points.length - 1}
          step={1}
          value={index}
          onChange={(event) => setIndex(Number(event.target.value))}
          aria-label="Alert threshold"
          className="mt-4 w-full accent-accent"
        />
        <div className="flex justify-between text-xs text-fg-3">
          <span>Stricter: fewer alerts, more fraud missed</span>
          <span>Looser: more fraud stopped, more customers interrupted</span>
        </div>
      </Card>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Taka saved"
          tone="good"
          value={taka(point.taka_recall_with_exit_holds * atRisk, true)}
          sub={`${pct(point.taka_recall_with_exit_holds)} of the ${taka(atRisk, true)} victims sent in ${days} days · ${signed((point.taka_recall_with_exit_holds - base.taka_recall_with_exit_holds) * atRisk, (v) => taka(v, true))}`}
        />
        <Stat
          label="Honest customers interrupted"
          tone={point.legit_customers_alerted > base.legit_customers_alerted ? "warn" : "plain"}
          value={num(point.legit_customers_alerted)}
          sub={`${num(point.false_alerts_per_day, 1)} honest payments a day · ${signed(point.legit_customers_alerted - base.legit_customers_alerted, (v) => num(v))}`}
        />
        <Stat
          label="Alerts to handle"
          value={`${num(point.alerts_per_day, 1)} a day`}
          sub={`${pct(point.precision)} of them really fraud · ${signed(point.alerts_per_day - base.alerts_per_day, (v) => num(v, 1))}`}
        />
        <Stat
          label="Reviewer workload"
          value={`${num(hours(point), 1)} hours a day`}
          sub={`${num(Math.ceil(hours(point) / REVIEW_HOURS))} reviewers at ${REVIEW_HOURS} hours of case work each, if every alert were looked at · ${signed(hours(point) - hours(base), (v) => `${num(v, 1)} h`)}`}
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card title="What it buys" hint="Share of victims’ money stopped as the threshold loosens. The vertical line is the setting above.">
          <LineChart series={money} labels={labels} xs={xs} yMax={1} format={(v) => pct(v, 0)} xTitle="Share of payments interrupted" mark={{ index, label: "selected" }} />
          <div className="mt-2"><Legend items={money} /></div>
        </Card>
        <Card title="What it costs" hint="Alerts per day, and how many of them land on honest customers.">
          <LineChart series={load} labels={labels} xs={xs} format={(v) => num(v, 0)} xTitle="Share of payments interrupted" mark={{ index, label: "selected" }} />
          <div className="mt-2"><Legend items={load} /></div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 2xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card title="The three thresholds in force" hint="A payment gets the highest tier whose threshold its score reaches." flush>
          <Table head={["Tier", "From probability", "Alerts a day", "Honest a day", "Really fraud", "Victims’ money stopped", ""]}>
            {(["warn", "step_up", "hold"] as const).map((tier) => {
              const at = insights.at_thresholds[tier];
              return (
                <tr key={tier}>
                  <Td className="font-medium">{TIER_NAME[tier]} and above</Td>
                  <Td right>{pct(insights.thresholds[tier], 2)}</Td>
                  <Td right>{num(at.alerts_per_day, 1)}</Td>
                  <Td right>{num(at.false_alerts_per_day, 1)}</Td>
                  <Td right>{pct(at.precision)}</Td>
                  <Td right>{pct(at.taka_recall_with_exit_holds)}</Td>
                  <Td>
                    <Button variant="ghost" small onClick={() => setIndex(nearest(points, insights.thresholds[tier]))}>Show</Button>
                  </Td>
                </tr>
              );
            })}
          </Table>
        </Card>
        <Card title="Assumption you can change">
          <label className="block text-sm text-fg-2">
            Minutes a reviewer spends on one alert
            <input
              type="number"
              min={1}
              max={120}
              value={minutes}
              onChange={(event) => setMinutes(Math.min(120, Math.max(1, Number(event.target.value) || 1)))}
              className={`${inputClass} mt-1 w-28`}
            />
          </label>
          <p className="mt-3 text-xs text-fg-3">
            Workload is the upper bound: today only the hold tier goes to a reviewer; warnings and step-up checks are answered by the customer.
          </p>
        </Card>
      </div>

      <p className="mt-4 text-xs text-fg-3">
        Measured by replaying model {version} over {num(insights.rows)} payments from {days} days it never trained on. The fraud is simulated, so the
        taka figures show the shape of the trade-off, not a forecast for a real customer base. Moving the slider changes nothing in production: thresholds
        are part of the versioned policy.
      </p>
    </>
  );
}

export default function ImpactPage() {
  const report = useApi<Report>("/v1/model/report");
  return (
    <>
      <PageHeader
        title="Impact simulator"
        sub="Move the alert threshold and see what it does to money saved, customers interrupted and reviewer workload."
      />
      <Async state={report}>
        {(data) =>
          data.insights?.impact.length ? (
            <Simulator insights={data.insights} version={data.model_version} />
          ) : (
            <Empty>This model version was evaluated without a threshold sweep. Re-run the evaluation to see the trade-off.</Empty>
          )
        }
      </Async>
    </>
  );
}
