"use client";

import { useEffect, useState } from "react";

import { Legend, LineChart } from "@/components/charts";
import { Button, Card, Chip, ErrorNote, Loading, Stat, Table, Td, cx, inputClass } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { num, pct, taka } from "@/lib/format";
import type { BusinessAssumptions, BusinessCase, BusinessPoint } from "@/lib/types";

/** Send-money and cash-out payments a month: orders of magnitude, not provider figures. */
const VOLUMES = [
  { label: "upay scale", value: 20_000_000 },
  { label: "bKash scale", value: 300_000_000 },
] as const;

/** The assumptions a CFO is most likely to change, as the input shows them. */
type Edited = Pick<BusinessAssumptions, "monthly_payments" | "analyst_monthly_cost_taka" | "abandon_rate" | "scam_loss_bps">;

function signed(value: number): string {
  if (!value) return "same as today";
  return `${value > 0 ? "+" : "−"}${taka(Math.abs(value), true)} vs today`;
}

function NumberField({
  label, value, onChange, min, max, step, suffix,
}: {
  label: string; value: number; onChange: (value: number) => void; min: number; max: number; step: number; suffix?: string;
}) {
  return (
    <label className="block text-sm text-fg-2">
      {label}
      <span className="mt-1 flex items-center gap-2">
        <input
          type="number"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isFinite(next)) onChange(Math.min(max, Math.max(min, next)));
          }}
          className={`${inputClass} w-32`}
        />
        {suffix && <span className="text-xs text-fg-3">{suffix}</span>}
      </span>
    </label>
  );
}

/** Low and high net benefit of one assumption, drawn around today's net benefit. */
function Tornado({ sensitivity, scale }: { sensitivity: BusinessCase["sensitivity"]; scale: [number, number] }) {
  const [lo, hi] = scale;
  const at = (value: number) => ((value - lo) / (hi - lo || 1)) * 100;
  const base = at(sensitivity.net_benefit);
  return (
    <Table head={["Assumption", "Tried", "Net benefit a month", ""]}>
      {sensitivity.rows.map((row) => {
        const left = Math.min(row.net_low, row.net_high);
        const right = Math.max(row.net_low, row.net_high);
        const fmt = (v: number) => (row.unit === "share" ? pct(v, 0) : num(v, 2));
        return (
          <tr key={row.key}>
            <Td className="font-medium">{row.label}</Td>
            <Td className="whitespace-nowrap text-fg-3">{fmt(row.low)} – {fmt(row.high)}</Td>
            <Td right className="whitespace-nowrap">{taka(row.net_low, true)} → {taka(row.net_high, true)}</Td>
            <Td className="w-[38%] min-w-40">
              <div className="relative h-3 rounded-full bg-white/8" title={`Swing ${taka(row.swing, true)}`}>
                <div
                  className={cx("absolute inset-y-0 rounded-full", row.net_high >= row.net_low ? "bg-good/70" : "bg-bad/70")}
                  style={{ left: `${at(left)}%`, width: `${Math.max(at(right) - at(left), 0.5)}%` }}
                />
                <div className="absolute inset-y-[-3px] w-px bg-fg" style={{ left: `${base}%` }} />
                {lo < 0 && <div className="absolute inset-y-[-3px] w-px bg-bad" style={{ left: `${at(0)}%` }} />}
              </div>
            </Td>
          </tr>
        );
      })}
    </Table>
  );
}

export function BusinessSection({
  index, setIndex, minutes, version,
}: {
  index: number; setIndex: (index: number) => void; minutes: number; version: string;
}) {
  const [edited, setEdited] = useState<Edited | null>(null);
  const [state, setState] = useState<{ data?: BusinessCase; error: ApiError | null }>({ error: null });

  const body = edited ? { ...edited, review_minutes: minutes } : { review_minutes: minutes };
  const key = JSON.stringify(body);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api<BusinessCase>(`/v1/model/business?version=${version}`, JSON.parse(key), controller.signal).then(
        (data) => setState({ data, error: null }),
        (error) => {
          if (error?.name !== "AbortError") setState((old) => ({ data: old.data, error }));
        },
      );
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [key, version]);

  const data = state.data;
  if (!data) return state.error ? <ErrorNote error={state.error} /> : <Loading label="Pricing the sweep…" />;

  const a: Edited = edited ?? data.assumptions;
  const change = (patch: Partial<Edited>) => setEdited({ ...a, ...patch });
  const today = data.policy;
  const point: BusinessPoint = index === data.today_index ? today : data.points[index];
  const best = data.points[data.best_index];
  const xs = data.points.map((p) => p.alert_rate * 100);
  const labels = data.points.map((p) => pct(p.alert_rate, 2));
  const money = [
    { name: "Scam money kept from fraudsters", color: "var(--color-good)", values: data.points.map((p) => p.prevented_total) },
    { name: "Cost of running it (friction, analysts, platform)", color: "var(--color-bad)", values: data.points.map((p) => p.operating_cost) },
  ];
  const nets = data.sensitivity.rows.flatMap((r) => [r.net_low, r.net_high]);
  const span: [number, number] = [Math.min(0, ...nets), Math.max(...nets)];
  const breakEven = data.break_even_bps;

  return (
    <>
      <div className="mt-8 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Business case, a month at your volume</h2>
          <p className="text-sm text-fg-3">
            The same threshold, priced in taka for {num(a.monthly_payments)} send-money and cash-out payments a month and a scam rate of{" "}
            {num(a.scam_loss_bps, 2)} basis points of payment value ({taka(data.scam_loss_at_risk, true)} at risk a month).
          </p>
        </div>
        <Button variant="ghost" small onClick={() => setIndex(data.best_index)} disabled={index === data.best_index}>
          Show the threshold that nets most
        </Button>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Net benefit a month"
          tone={point.net_benefit >= 0 ? "good" : "bad"}
          value={taka(point.net_benefit, true)}
          sub={`${signed(point.net_benefit - today.net_benefit)} · to the provider alone ${taka(point.provider_net_benefit, true)}`}
        />
        <Stat
          label="Scam money kept from fraudsters"
          tone="good"
          value={taka(point.prevented_total, true)}
          sub={`${pct(point.prevented_share)} of what is at risk · ${point.prevented_per_taka_cost === null ? "–" : `৳${num(point.prevented_per_taka_cost, 2)}`} kept per ৳1 of running cost`}
        />
        <Stat
          label="Cost of friction"
          tone={point.friction_total > today.friction_total ? "warn" : "plain"}
          value={taka(point.friction_total, true)}
          sub={`${num(point.abandoned_payments)} honest payments abandoned, ${num(point.support_contacts)} support contacts · ${num(point.honest_per_10k, 1)} honest customers interrupted per 10,000 payments`}
        />
        <Stat
          label="Analysts needed"
          value={num(point.analysts)}
          sub={`${num(point.held_for_review)} holds, ${num(point.review_hours)} review hours (${num(point.workload_fte, 1)} FTE of work, at least ${num(data.assumptions.min_analysts)} for 24x7 cover) · ${taka(point.analyst_cost, true)}`}
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card
          title="Money kept against money spent"
          hint={`The gap is the net benefit. It peaks at ${pct(best.alert_rate, 2)} of payments interrupted (${taka(best.net_benefit, true)} a month); the policy in force makes ${taka(today.net_benefit, true)}.`}
        >
          <LineChart series={money} labels={labels} xs={xs} format={(v) => taka(v, true)} xTitle="Share of payments interrupted" mark={{ index, label: "selected" }} />
          <div className="mt-2"><Legend items={money} /></div>
        </Card>
        <Card title="Assumptions you can change" hint="Everything else is in the table of sources below and can be changed through the API.">
          <div className="space-y-3">
            <div>
              <div className="flex flex-wrap gap-2">
                {VOLUMES.map((v) => (
                  <Chip key={v.value} on={a.monthly_payments === v.value} onClick={() => change({ monthly_payments: v.value })}>
                    {v.label}
                  </Chip>
                ))}
              </div>
              <div className="mt-2">
                <NumberField
                  label="Send-money and cash-out payments a month (millions)"
                  value={a.monthly_payments / 1e6}
                  onChange={(v) => change({ monthly_payments: Math.round(v * 1e6) })}
                  min={0.01}
                  max={2000}
                  step={1}
                />
              </div>
            </div>
            <NumberField
              label="Analyst cost a month, loaded"
              value={a.analyst_monthly_cost_taka}
              onChange={(v) => change({ analyst_monthly_cost_taka: v })}
              min={1000}
              max={2_000_000}
              step={5000}
              suffix="BDT"
            />
            <NumberField
              label="Honest payments abandoned after an interruption"
              value={Math.round(a.abandon_rate * 1000) / 10}
              onChange={(v) => change({ abandon_rate: v / 100 })}
              min={0}
              max={100}
              step={1}
              suffix="%"
            />
            <NumberField
              label="Scam losses, share of payment value"
              value={a.scam_loss_bps}
              onChange={(v) => change({ scam_loss_bps: v })}
              min={0.01}
              max={100}
              step={0.5}
              suffix="basis points"
            />
            <p className="text-xs text-fg-3">
              Analyst minutes per hold come from the assumption above. Running the policy in force pays for itself above{" "}
              {breakEven.net_benefit === null ? "–" : `${num(breakEven.net_benefit, 2)} bp`} of scam losses
              {breakEven.provider_net_benefit === null ? "" : ` (${num(breakEven.provider_net_benefit, 2)} bp counting the provider’s own money only)`}.
            </p>
            <Button variant="ghost" small onClick={() => setEdited(null)} disabled={!edited}>Back to the defaults</Button>
          </div>
        </Card>
      </div>

      <div className="mt-4">
        <Card
          title="Which assumptions matter most"
          hint={`Net benefit of the policy in force (${taka(data.sensitivity.net_benefit, true)} a month, the white line) with one assumption at a time moved to the low and the high end of its range. Green: the high end pays more.`}
          flush
        >
          <Tornado sensitivity={data.sensitivity} scale={span} />
        </Card>
      </div>

      <details className="mt-4 rounded-2xl border border-line bg-card px-4 py-3 text-sm">
        <summary className="cursor-pointer font-medium">Where each assumption comes from</summary>
        <Table head={["Assumption", "Value used", "Source or reasoning"]} className="mt-3">
          {data.sources.map((s) => (
            <tr key={s.key}>
              <Td className="font-medium">{s.label}</Td>
              <Td right className="whitespace-nowrap">
                {s.unit === "share" ? pct(data.assumptions[s.key], 0) : num(data.assumptions[s.key], 2)}
                {s.unit === "share" ? "" : ` ${s.unit}`}
              </Td>
              <Td className="text-fg-3">{s.source}</Td>
            </tr>
          ))}
        </Table>
      </details>

      <p className="mt-4 text-xs text-fg-3">
        Rates come from the back-test of model {data.model_version}. The simulator’s scams take{" "}
        {taka(data.synthetic.scam_loss_per_payment)} per payment, far more than real traffic, so scam money and true alerts are scaled by{" "}
        {num(data.synthetic.scale, 4)} to the assumed rate. These are estimates on synthetic data to show the shape of the
        trade-off, not a forecast: a provider would replace the assumptions with its own figures first. See docs/BUSINESS_CASE.md.
      </p>
    </>
  );
}
