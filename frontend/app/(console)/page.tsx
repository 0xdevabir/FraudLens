"use client";

import Link from "next/link";

import { HBars, Legend, LineChart, StackedBars } from "@/components/charts";
import { Async, Badge, Card, PageHeader, Stat } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, pct, shortDay, taka, TIER_COLOR, when, words } from "@/lib/format";
import type { DailyRow, Report, Summary } from "@/lib/types";

const ALERT_TIERS = ["warn", "step_up", "hold"] as const;
const TIER_NAME = { warn: "Warned", step_up: "Step-up check", hold: "Held for review" } as const;

function Live({ summary, daily }: { summary: Summary; daily: DailyRow[] }) {
  const tiers = summary.decisions.by_tier;
  const interrupted = ALERT_TIERS.reduce((sum, tier) => sum + (tiers[tier] ?? 0), 0);
  const blocked = summary.alert_outcomes.blocked ?? { count: 0, amount: 0 };
  const latency = summary.decision_latency_ms;
  const labels = daily.map((row) => shortDay(row.date));
  const bars = ALERT_TIERS.map((tier) => ({
    name: TIER_NAME[tier],
    color: TIER_COLOR[tier],
    values: daily.map((row) => row.decisions[tier] ?? 0),
  }));
  const money = [
    { name: "Stopped", color: "var(--color-good)", values: daily.map((row) => row.alerts.blocked?.amount ?? 0) },
    { name: "Went through after an alert", color: "var(--color-fg-4)", values: daily.map((row) => row.alerts.completed?.amount ?? 0) },
  ];
  const verdicts = summary.cases.verdicts;

  return (
    <>
      <div data-tour="home-kpis" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Money kept from leaving"
          value={taka(blocked.amount, true)}
          tone="good"
          sub={`${num(blocked.count)} payments stopped: the customer backed out, failed the check, or a reviewer confirmed fraud`}
        />
        <Stat
          label="Customers interrupted"
          value={pct(interrupted / Math.max(summary.decisions.total, 1), 2)}
          sub={`${num(interrupted)} of ${num(summary.decisions.total)} payments scored; the rest went straight through`}
        />
        <Stat
          label="Waiting for a reviewer"
          value={num(summary.waiting.held.count)}
          tone={summary.cases.overdue ? "warn" : "plain"}
          sub={`${taka(summary.waiting.held.amount, true)} on hold · ${num(summary.cases.overdue)} cases past their deadline`}
        />
        <Stat
          label="Time to decide (p95)"
          value={latency.p95 === null ? "–" : `${latency.p95.toFixed(1)} ms`}
          sub={`median ${latency.p50?.toFixed(1) ?? "–"} ms · p99 ${latency.p99?.toFixed(1) ?? "–"} ms, inside the payment request`}
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card tour="home-chart" title="Payments interrupted per day" hint="Each bar is one day of live decisions, by what the customer saw.">
          <StackedBars series={bars} labels={labels} />
          <div className="mt-2"><Legend items={bars} /></div>
        </Card>
        <Card title="Taka behind those alerts" hint="Of the money that triggered an alert each day: how much was stopped, and how much the customer sent anyway.">
          <StackedBars series={money} labels={labels} format={(value) => taka(value, true)} />
          <div className="mt-2"><Legend items={money} /></div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card title="What reviewers found" hint="Verdicts on closed cases. These become the labels the next model is trained on.">
          <HBars
            rows={[
              { label: "Confirmed fraud", value: verdicts.confirmed_fraud ?? 0, color: "var(--color-bad)" },
              { label: "Legitimate (false alarm)", value: verdicts.legitimate ?? 0, color: "var(--color-good)" },
              { label: "Inconclusive", value: verdicts.inconclusive ?? 0, color: "var(--color-fg-4)" },
            ]}
          />
          <p className="mt-3 text-xs text-fg-3">
            {num(summary.cases.by_status.open ?? 0)} open · {num(summary.cases.by_status.in_review ?? 0)} in review ·{" "}
            {num(summary.cases.by_status.escalated ?? 0)} escalated · {num(summary.cases.by_status.closed ?? 0)} closed.{" "}
            <Link href="/cases" className="text-info hover:underline">Open the case queue</Link>
          </p>
        </Card>
        <Card title="Wallets acted on" hint="A freeze always needs a second person.">
          <div className="grid grid-cols-3 gap-3 text-center">
            {[
              ["Confirmed fraud", summary.wallets_confirmed_fraud],
              ["Frozen", summary.wallets_frozen],
              ["Freeze requests waiting", summary.freeze_requests_pending],
            ].map(([label, value]) => (
              <div key={label}>
                <div className="text-2xl font-semibold tabular-nums">{num(value as number)}</div>
                <div className="text-xs text-fg-3">{label}</div>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-fg-3">
            Payments to a confirmed-fraud wallet are held by rule; payments to a frozen wallet are refused outright.
          </p>
        </Card>
        <Card title="Platform health">
          <div className="space-y-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-fg-2">Scoring mode</span>
              <span className="flex gap-1">
                {Object.entries(summary.decisions.by_mode).map(([mode, count]) => (
                  <Badge key={mode} tone={mode === "model" ? "green" : "amber"}>{words(mode)} · {num(count)}</Badge>
                ))}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-fg-2">Event stream worker</span>
              <Badge tone={summary.stream.worker_running ? "green" : "red"}>{summary.stream.worker_running ? "Running" : "Stopped"}</Badge>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-fg-2">Stream backlog</span>
              <span className="tabular-nums">{num(summary.stream.backlog)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-fg-2">Events that could not be processed</span>
              <span className="tabular-nums">{num(summary.stream.dead_lettered)}</span>
            </div>
          </div>
        </Card>
      </div>
    </>
  );
}

function Backtest({ report }: { report: Report }) {
  const insights = report.insights;
  if (!insights) return null;
  const { warn, hold } = insights.at_thresholds;
  const lines = [
    { name: "Taken from victims", color: "var(--color-bad)", values: insights.daily.map((row) => row.victim_taka) },
    { name: "Of which FraudLens would have stopped", color: "var(--color-good)", values: insights.daily.map((row) => row.taka_stopped) },
  ];
  return (
    <Card
      className="mt-4"
      title="Against known fraud: the held-out test period"
      hint={`Model ${report.model_version} replayed over ${insights.days} days (${num(insights.rows)} payments) it never trained on, where the simulator knows which payments were fraud.`}
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div className="space-y-3">
          <div>
            <div className="text-2xl font-semibold tabular-nums text-good">{pct(warn.taka_recall_with_exit_holds)}</div>
            <div className="text-xs text-fg-3">
              of victims’ money interrupted before it left the system: {taka(warn.taka_stopped, true)} at the payment itself, the rest by holding the mule’s cash-out
            </div>
          </div>
          <div>
            <div className="text-2xl font-semibold tabular-nums">{num(warn.false_alerts_per_day, 1)} a day</div>
            <div className="text-xs text-fg-3">
              honest payments interrupted, out of {num(warn.alerts_per_day, 1)} alerts a day ({pct(warn.alert_rate, 2)} of traffic)
            </div>
          </div>
          <div>
            <div className="text-2xl font-semibold tabular-nums">{pct(hold.precision)}</div>
            <div className="text-xs text-fg-3">of payments held for a reviewer really were fraud</div>
          </div>
          <Link href="/impact" className="inline-block text-sm text-info hover:underline">Move the threshold and see the trade-off →</Link>
        </div>
        <div>
          <LineChart series={lines} labels={insights.daily.map((row) => shortDay(row.date))} format={(value) => taka(value, true)} />
          <div className="mt-2"><Legend items={lines} /></div>
        </div>
      </div>
    </Card>
  );
}

export default function Overview() {
  const summary = useApi<Summary>("/v1/metrics/summary", 20_000);
  const daily = useApi<DailyRow[]>("/v1/metrics/daily");
  const report = useApi<Report>("/v1/model/report");
  return (
    <>
      <PageHeader tour="home-header"
        title="Executive summary"
        sub={
          summary.data
            ? `Live decisions since go-live, as of ${when(summary.data.as_of)} Dhaka time on the platform clock. All customers and payments are simulated.`
            : "Live decisions since go-live."
        }
      />
      <Async state={summary}>{(data) => <Live summary={data} daily={daily.data ?? []} />}</Async>
      {report.data && <Backtest report={report.data} />}
    </>
  );
}
