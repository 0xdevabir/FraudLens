"use client";

import { useState } from "react";

import { HBars } from "@/components/charts";
import { Async, Badge, Card, Empty, Facts, PageHeader, Stat, StatusBadge, Table, Tabs, Td } from "@/components/ui";
import { useApi } from "@/lib/api";
import { day, num, pct, shortDay, TIER_LABEL, TIERS, when, words } from "@/lib/format";
import type { Drift, Feedback, Insights, LabelRealism, Registry, Report, Shadow, Spread, Summary } from "@/lib/types";

type Tab = "performance" | "drift" | "versions" | "feedback";

const ABLATION: Record<string, string> = {
  rules_baseline: "Rules only",
  anomaly_only: "Anomaly detector only",
  mule_model_only: "Mule model only",
  transaction_model: "Transaction model (served)",
  fusion: "Fusion of all three",
};
const DRIFT_COLOR: Record<string, string> = { stable: "var(--color-good)", watch: "var(--color-warn)", shifted: "var(--color-bad)" };

function ms(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : `${value.toFixed(1)} ms`;
}

function Performance({ report, summary }: { report: Report; summary?: Summary }) {
  const test = report.model.test;
  const chain = test.risk_score.all_fraud_chain;
  const fixed = test.at_fixed_thresholds;
  const fairness = report.insights?.fairness.overall;
  const offline = report.policy.single_decisions?.decide_ms;
  const live = summary?.decision_latency_ms;
  const reliability = test.calibration.reliability;
  const typology = Object.entries(fixed.warn?.by_typology ?? {});

  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Precision of the top 1% riskiest"
          value={pct(test.at_alert_budgets["1.00%"]?.precision)}
          sub={`${pct(test.at_alert_budgets["1.00%"]?.case_recall)} of fraud cases caught inside that 1%`}
        />
        <Stat
          label="Fraud cases caught at today’s alert rate"
          value={pct(fixed.warn?.case_recall)}
          tone="good"
          sub={`${pct(fixed.warn?.alert_rate, 2)} of payments alerted · ${pct(fixed.warn?.taka_recall_with_exit_holds)} of victims’ money stopped`}
        />
        <Stat
          label="False-positive rate"
          value={pct(fairness?.false_alert_rate, 2)}
          sub={`of honest payments get any alert · ${pct(fairness?.false_hold_rate, 3)} are held`}
        />
        <Stat
          label="Scoring latency (p95)"
          value={ms(live?.p95 ?? offline?.p95)}
          sub={live?.p95 != null ? `live: median ${ms(live.p50)}, p99 ${ms(live.p99)} · offline test p95 ${ms(offline?.p95)}` : `offline test: median ${ms(offline?.p50)}, worst ${ms(offline?.max)}`}
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card
          className="xl:col-span-2"
          title="Precision and recall at an alert budget"
          hint="If the team can only interrupt the riskiest K% of payments: how many of those are fraud, and how much fraud that catches."
          flush
        >
          <Table head={["Top", "Alerts a day", "Precision", "Honest a day", "Cases caught", "Fraud payments caught", "Money stopped"]}>
            {Object.entries(test.at_alert_budgets).map(([budget, at]) => (
              <tr key={budget}>
                <Td className="font-medium">{budget}</Td>
                <Td right>{num(at.alerts_per_day, 1)}</Td>
                <Td right>{pct(at.precision)}</Td>
                <Td right>{num(at.false_alerts_per_day, 1)}</Td>
                <Td right>{pct(at.case_recall)}</Td>
                <Td right>{pct(at.loss_txn_recall)}</Td>
                <Td right>{pct(at.taka_recall_with_exit_holds)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card className="xl:col-span-2" title="At the thresholds in force" hint="Each row counts that tier and every tier above it." flush>
          <Table head={["Tier", "Alerts a day", "Precision", "Honest a day", "Cases caught", "Fraud payments caught", "Money stopped"]}>
            {(["warn", "step_up", "hold"] as const).map((tier) => {
              const at = fixed[tier];
              return at ? (
                <tr key={tier}>
                  <Td className="font-medium">{TIER_LABEL[tier]}+</Td>
                  <Td right>{num(at.alerts_per_day, 1)}</Td>
                  <Td right>{pct(at.precision)}</Td>
                  <Td right>{num(at.false_alerts_per_day, 1)}</Td>
                  <Td right>{pct(at.case_recall)}</Td>
                  <Td right>{pct(at.loss_txn_recall)}</Td>
                  <Td right>{pct(at.taka_recall_with_exit_holds)}</Td>
                </tr>
              ) : null;
            })}
          </Table>
          <p className="border-t border-line px-4 py-2 text-xs text-fg-3">
            Ranking quality over the whole test period: PR-AUC {num(chain?.pr_auc, 3)}, ROC-AUC {num(chain?.roc_auc, 3)} on{" "}
            {num(chain?.positives)} fraud payments in {num(chain?.rows)} ({pct(chain?.base_rate, 2)}).
          </p>
        </Card>

        <Card title="What each layer adds" hint="Every scorer given the same 1% alert budget on the test period. “New scam” is a fraud pattern held out of training entirely." flush>
          <Table head={["Scorer", "PR-AUC", "Precision", "Fraud caught", "Money stopped", "New scam"]}>
            {Object.entries(test["ablation_at_1.00%"] ?? {}).map(([name, row]) => (
              <tr key={name} className={name === "transaction_model" ? "bg-good/10" : undefined}>
                <Td className="font-medium">{ABLATION[name] ?? words(name)}</Td>
                <Td right>{num(row.pr_auc, 3)}</Td>
                <Td right>{pct(row.precision)}</Td>
                <Td right>{pct(row.loss_txn_recall)}</Td>
                <Td right>{pct(row.taka_recall_with_exit_holds)}</Td>
                <Td right>{pct(row.heldout_txn_recall)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Caught, by kind of fraud" hint="Share of cases with at least one payment alerted, at today’s warn threshold.">
          {typology.length ? (
            <HBars
              max={1}
              format={(value) => pct(value, 0)}
              rows={typology.map(([name, row]) => ({
                label: words(name),
                value: row.case_recall,
                note: `${num(row.cases)} cases`,
                color: row.case_recall >= 0.9 ? "var(--color-good)" : row.case_recall >= 0.7 ? "var(--color-warn)" : "var(--color-bad)",
              }))}
            />
          ) : (
            <Empty>No breakdown in this report.</Empty>
          )}
        </Card>

        <Card
          title="Is a score of 80% really 80%?"
          hint={`Payments sorted into ten equal groups by predicted probability. Brier score ${num(test.calibration.brier, 4)}; the model predicts ${pct(test.calibration.mean_predicted, 2)} on average against ${pct(test.calibration.observed_rate, 2)} observed.`}
          flush
        >
          <Table head={["Group", "Payments", "Model predicted", "Really fraud"]}>
            {reliability.map((bin, i) => (
              <tr key={i}>
                <Td>{i === 0 ? "1 (lowest risk)" : i === reliability.length - 1 ? `${i + 1} (highest risk)` : i + 1}</Td>
                <Td right>{num(bin.rows)}</Td>
                <Td right>{pct(bin.predicted, 3)}</Td>
                <Td right>{pct(bin.observed, 3)}</Td>
              </tr>
            ))}
          </Table>
          <p className="border-t border-line px-4 py-2 text-xs text-fg-3">
            Nine payments in ten score near zero and are honest. In the riskiest tenth the model says less than what happens, so its
            probabilities rank payments well but read low: thresholds are set from alert budgets, not from the number itself.
          </p>
        </Card>
        <Card title="What the transaction model leans on" hint="Share of the model’s total split gain. Names starting r_ describe the recipient, s_ the sender.">
          <HBars
            format={(value) => pct(value, 1)}
            rows={(report.model.top_features.transaction_model ?? []).slice(0, 12).map((row) => ({
              label: <code className="text-xs">{row.feature}</code>,
              value: row.gain_share,
            }))}
          />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card title="Mule wallet model">
          <Facts
            rows={[
              ["Flagged wallets that are mules", pct(test.mule_wallets.precision)],
              ["Mules found", `${pct(test.mule_wallets.recall)} of ${num(test.mule_wallets.positives)}`],
              ["Found before any victim paid", `${num(test.mule_wallets.timing?.detected_before_any_victim_paid)} of ${num(test.mule_wallets.timing?.detected)}`],
              ["Found before the victim reported", `${num(test.mule_wallets.timing?.detected_before_the_report)} of ${num(test.mule_wallets.timing?.later_reported_by_victims)}`],
              ["Typical head start on the report", `${num(test.mule_wallets.timing?.median_hours_ahead_of_report, 1)} hours`],
            ]}
          />
        </Card>
        <Card title="Agent risk">
          <Facts
            rows={[
              ["Agents scored", num(test.agents.rows)],
              ["Review list", `${num(test.agents.review_list_size)} agents`],
              ["Of those, really complicit", pct(test.agents.precision_at_k)],
              ["PR-AUC", num(test.agents.pr_auc, 3)],
            ]}
          />
        </Card>
        <Card title="Ring detection">
          <Facts
            rows={[
              ["Rings found", num(test.rings.rings)],
              ["Wallets in them", num(test.rings.wallets_in_rings)],
              ["Of those, really mules", pct(test.rings.true_mule_share)],
            ]}
          />
        </Card>
      </div>
      {report.label_realism && <UnderReporting data={report.label_realism} />}
      <p className="mt-4 text-xs text-fg-3">
        Model {report.model_version}, trained on {num(report.model.data.rows.train)} payments and measured on {num(report.model.data.rows.test)} later
        ones it never saw. The data is simulated: these numbers say the pipeline works end to end, not how it would do on real traffic.
      </p>
    </>
  );
}

const LABEL_REGIME: Record<string, string> = {
  ground_truth: "Every scam labelled (what the model was trained on)",
  reported_uniform: "Only reported scams, half at random",
  reported: "Only reported scams, as victims really report",
  reported_pu: "+ positive-unlabelled learning",
  reported_pu_known_rate: "+ positive-unlabelled, report rate known",
  reported_propagated: "+ labels spread through mule wallets",
  reported_feedback: "+ analyst verdicts on alerts",
  reported_propagated_feedback: "+ mule-wallet labels and verdicts",
};

function spread(value: Spread | undefined): string {
  return value ? `${pct(value.mean)} ± ${num(value.sd * 100, 1)}` : "–";
}

function UnderReporting({ data }: { data: LabelRealism }) {
  const rows = data.regimes.filter((name) => data.summary.regimes[name]);
  return (
    <div className="mt-4">
      <Card
        title="If only reported scams were labelled"
        hint={`The same model retrained on the labels a wallet provider really has, then measured on every scam in the test period at the served model’s false-positive rate (${pct(data.operating_point.fpr, 2)}). Mean ± sd over ${data.seeds.length} seeds, sd in points. Run \`make label-realism\`.`}
        flush
      >
        <Table head={["Training labels", "Scams caught", "New scam type caught", "PR-AUC"]}>
          {rows.map((name) => {
            const row = data.summary.regimes[name];
            return (
              <tr key={name}>
                <Td className="font-medium">{LABEL_REGIME[name] ?? words(name)}</Td>
                <Td right>{spread(row.case_recall)}</Td>
                <Td right>{spread(row.by_typology.investment_scam?.case_recall)}</Td>
                <Td right>{row.pr_auc ? `${num(row.pr_auc.mean, 3)} ± ${num(row.pr_auc.sd, 3)}` : "–"}</Td>
              </tr>
            );
          })}
        </Table>
      </Card>
    </div>
  );
}

function DriftView({ drift, backtest }: { drift: Drift; backtest?: Insights["drift"] }) {
  const worst = [...drift.features].sort((a, b) => b.psi - a.psi).slice(0, 14);
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Risk score drift (PSI)"
          value={num(drift.score.psi, 3)}
          tone={drift.score.status === "stable" ? "good" : drift.score.status === "watch" ? "warn" : "bad"}
          sub={`${words(drift.score.status)} · watch above ${drift.limits.watch}, shifted above ${drift.limits.shifted}`}
        />
        <Stat
          label="Alert rate now"
          value={pct(drift.score.alert_rate, 2)}
          sub={`${pct(drift.score.validation_alert_rate, 2)} when the thresholds were set`}
        />
        <Stat
          label="Hold rate now"
          value={pct(drift.score.hold_rate, 2)}
          sub={`${pct(drift.score.validation_hold_rate, 2)} when the thresholds were set`}
        />
        <Stat
          label="Inputs that moved"
          value={`${num(drift.feature_status.shifted ?? 0)} of ${num(drift.features.length)}`}
          tone={drift.feature_status.shifted ? "warn" : "good"}
          sub={`${num(drift.feature_status.watch ?? 0)} more to watch, ${num(drift.feature_status.stable ?? 0)} stable`}
        />
      </div>
      <p className="mt-2 text-xs text-fg-3">
        The latest {num(drift.rows)} live decisions ({when(drift.from)} to {when(drift.to)}) of model {drift.model_version}, compared with its{" "}
        {drift.reference.features} data for inputs and {words(drift.reference.score).toLowerCase()} for the score.
      </p>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card title="Inputs that moved most" hint="Population stability index per input. Bars are cut off at 1; the number is the real value.">
          <HBars
            max={1}
            format={(value) => num(value, 2)}
            limit={{ value: drift.limits.shifted, label: `shifted (${drift.limits.shifted})` }}
            rows={worst.map((row) => ({
              label: <code className="text-xs">{row.feature}</code>,
              value: row.psi,
              color: DRIFT_COLOR[row.status],
              note: row.missing ? `${pct(row.missing, 0)} missing` : undefined,
            }))}
          />
          <p className="mt-3 text-xs text-fg-3">
            Counters that only ever grow, such as account or handset age, drift by construction. What matters is whether the score moves with them,
            which is the first number above.
          </p>
        </Card>
        <div className="space-y-4">
          <Card title="Score drift by day" hint="Live decisions." flush>
            <Table head={["Day", "Decisions", "PSI", "", "Alert rate"]}>
              {drift.daily.map((row) => (
                <tr key={row.date}>
                  <Td>{shortDay(row.date)}</Td>
                  <Td right>{num(row.rows)}</Td>
                  <Td right>{num(row.psi, 3)}</Td>
                  <Td><StatusBadge status={row.status} /></Td>
                  <Td right>{pct(row.alert_rate, 2)}</Td>
                </tr>
              ))}
            </Table>
          </Card>
          {backtest && (
            <Card title="Score drift across the test period" hint="The same check on the held-out weeks, where the true fraud rate is known." flush>
              <Table head={["Period", "Payments", "PSI", "Alert rate", "Hold rate", "True fraud rate"]}>
                {backtest.score.map((row) => (
                  <tr key={row.period}>
                    <Td>{words(row.period)}</Td>
                    <Td right>{num(row.rows)}</Td>
                    <Td right>{num(row.psi, 3)}</Td>
                    <Td right>{pct(row.alert_rate, 2)}</Td>
                    <Td right>{pct(row.hold_rate, 2)}</Td>
                    <Td right>{pct(row.fraud_rate, 2)}</Td>
                  </tr>
                ))}
              </Table>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function ShadowView({ shadow }: { shadow: Shadow }) {
  if (shadow.status !== "ok" || !shadow.matrix) {
    return (
      <Card title="Shadow comparison">
        <Empty>
          {shadow.status === "no_challenger"
            ? "No challenger is running. Set FRAUDLENS_SHADOW_MODEL_VERSION to a registered version and restart the API."
            : "The challenger has not scored any payments yet."}
        </Empty>
      </Card>
    );
  }
  const reviewed = shadow.reviewed;
  return (
    <Card
      title={`Shadow mode: ${shadow.model_version} scored next to ${shadow.served_version}`}
      hint={`The challenger scores every payment but decides nothing. ${num(shadow.decisions)} payments compared.`}
      actions={shadow.live ? <Badge tone="green">scoring live</Badge> : <Badge>not running now</Badge>}
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <div className="mb-3 grid grid-cols-3 gap-3 text-center">
            <div>
              <div className="text-2xl font-semibold tabular-nums">{pct(shadow.agreement, 2)}</div>
              <div className="text-xs text-fg-3">same tier</div>
            </div>
            <div>
              <div className="text-2xl font-semibold tabular-nums">{num(shadow.challenger_only_alerts)}</div>
              <div className="text-xs text-fg-3">only the challenger would alert</div>
            </div>
            <div>
              <div className="text-2xl font-semibold tabular-nums">{num(shadow.served_only_alerts)}</div>
              <div className="text-xs text-fg-3">only the served model alerted</div>
            </div>
          </div>
          <Facts
            rows={[
              ["Alert rate", `${pct(shadow.served?.alert_rate, 2)} served · ${pct(shadow.challenger?.alert_rate, 2)} challenger`],
              ["Hold rate", `${pct(shadow.served?.hold_rate, 2)} served · ${pct(shadow.challenger?.hold_rate, 2)} challenger`],
              ...(reviewed
                ? ([
                    [
                      "Confirmed fraud alerts",
                      `challenger also alerts on ${num(reviewed.confirmed_fraud.challenger_also_alerts)} of ${num(reviewed.confirmed_fraud.alerts)}, holds ${num(reviewed.confirmed_fraud.challenger_holds)}`,
                    ],
                    [
                      "False alarms",
                      `challenger would have let ${num(reviewed.false_positive.challenger_would_allow)} of ${num(reviewed.false_positive.alerts)} through`,
                    ],
                  ] as [string, string][])
                : []),
              ...(shadow.latency?.challenger_p95_ms != null
                ? ([["Challenger latency", `mean ${ms(shadow.latency.challenger_mean_ms)}, p95 ${ms(shadow.latency.challenger_p95_ms)}`]] as [string, string][])
                : []),
            ]}
          />
          <p className="mt-3 text-xs text-fg-3">
            Reviewed alerts are only ones the served model raised, so this comparison cannot show fraud that both models missed.
          </p>
        </div>
        <Table head={["Served ↓ / challenger →", ...TIERS.map((tier) => TIER_LABEL[tier])]}>
          {TIERS.map((served) => (
            <tr key={served}>
              <Td className="font-medium">{TIER_LABEL[served]}</Td>
              {TIERS.map((challenger) => (
                <Td key={challenger} right className={served === challenger ? "bg-wash font-medium" : undefined}>
                  {num(shadow.matrix?.[served]?.[challenger] ?? 0)}
                </Td>
              ))}
            </tr>
          ))}
        </Table>
      </div>
    </Card>
  );
}

function Versions({ registry, shadow }: { registry: Registry; shadow?: Shadow }) {
  return (
    <>
      <Card
        title="Model registry"
        hint="Every trained version is kept with its thresholds and evaluation. One is promoted; the API serves the promoted one after a restart."
        actions={registry.restart_needed ? <Badge tone="amber">restart needed: serving {registry.serving}, promoted {registry.promoted}</Badge> : undefined}
        flush
      >
        <Table head={["Version", "", "Trained (recorded)", "Built on", "Trees", "PR-AUC", "Precision", "Cases caught", "Money stopped", "Reviewer labels"]}>
          {registry.versions.map((version) => (
            <tr key={version.version}>
              <Td className="font-medium">{version.version}</Td>
              <Td>
                <span className="flex gap-1">
                  {version.serving && <Badge tone="green">serving</Badge>}
                  {version.promoted && !version.serving && <Badge tone="amber">promoted</Badge>}
                  {version.shadow && <Badge tone="violet">shadow</Badge>}
                </span>
              </Td>
              <Td>{day(version.created_at)}</Td>
              <Td>{version.parent ?? "–"}</Td>
              <Td right>{num(version.trees.txn)}</Td>
              <Td right>{num(version.headline?.pr_auc, 3)}</Td>
              <Td right>{pct(version.headline?.precision)}</Td>
              <Td right>{pct(version.headline?.case_recall)}</Td>
              <Td right>{pct(version.headline?.taka_recall_with_exit_holds ?? version.headline?.taka_recall)}</Td>
              <Td right>{version.feedback ? num(version.feedback.rows) : "–"}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      <div className="mt-4">{shadow && <ShadowView shadow={shadow} />}</div>
    </>
  );
}

function FeedbackView({ feedback, registry }: { feedback: Feedback; registry?: Registry }) {
  const retrained = registry?.versions.filter((version) => version.feedback) ?? [];
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Cases closed as fraud" value={num(feedback.cases.confirmed_fraud ?? 0)} sub={`${num(feedback.labels.fraud)} payments labelled fraud`} />
        <Stat label="Cases closed as legitimate" value={num(feedback.cases.legitimate ?? 0)} sub={`${num(feedback.labels.legitimate)} payments labelled clean`} />
        <Stat label="Inconclusive" value={num(feedback.cases.inconclusive ?? 0)} sub="label nothing: the model learns only from firm verdicts" />
        <Stat label="Latest verdict" value={when(feedback.last_verdict_at)} sub="platform clock, Dhaka time" />
      </div>
      <div className="mt-4 space-y-4">
        {retrained.length ? (
          retrained.map((version) => {
            const fb = version.feedback!;
            const compare = fb.comparison_after_last_label;
            return (
              <Card
                key={version.version}
                title={`${version.version}: ${version.parent ?? "the previous model"} retrained with reviewer verdicts`}
                hint={`${num(fb.rows)} labelled payments (${num(fb.fraud)} fraud, ${num(fb.legitimate)} legitimate), the last one on ${when(fb.last_label_at)}.`}
                flush
              >
                {compare ? (
                  <>
                    <Table head={["", "PR-AUC", "Precision", "Fraud payments caught", "Money stopped"]}>
                      {(["parent", "retrained"] as const).map((side) => (
                        <tr key={side}>
                          <Td className="font-medium">{side === "parent" ? `${version.parent ?? "Parent"} (before)` : `${version.version} (after)`}</Td>
                          <Td right>{num(compare[side].pr_auc, 3)}</Td>
                          <Td right>{pct(compare[side].precision)}</Td>
                          <Td right>{pct(compare[side].loss_txn_recall)}</Td>
                          <Td right>{pct(compare[side].taka_recall)}</Td>
                        </tr>
                      ))}
                    </Table>
                    <p className="border-t border-line px-4 py-2 text-xs text-fg-3">
                      Compared only on the {num(fb.test_rows_after_last_label)} payments made after the last label, so the retrained model is never
                      scored on something a reviewer already told it about. Reviewers only see payments the old model alerted on, so these labels are
                      not a random sample.
                    </p>
                  </>
                ) : (
                  <Empty>Too few payments after the last label to compare fairly.</Empty>
                )}
              </Card>
            );
          })
        ) : (
          <Card title="Retraining"><Empty>No model has been retrained on reviewer verdicts yet. Run `make retrain`.</Empty></Card>
        )}
      </div>
    </>
  );
}

export default function ModelPage() {
  const [tab, setTab] = useState<Tab>("performance");
  const report = useApi<Report>("/v1/model/report");
  const summary = useApi<Summary>("/v1/metrics/summary");
  const drift = useApi<Drift>(tab === "drift" ? "/v1/metrics/drift" : null);
  const registry = useApi<Registry>(tab === "versions" || tab === "feedback" ? "/v1/models" : null);
  const shadow = useApi<Shadow>(tab === "versions" ? "/v1/model/shadow" : null);
  const feedback = useApi<Feedback>(tab === "feedback" ? "/v1/feedback" : null);
  return (
    <>
      <PageHeader title="Model dashboard" sub="How well the served model finds fraud, whether live traffic still looks like its training data, and what is waiting to replace it." />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "performance", label: "Performance" },
          { id: "drift", label: "Drift" },
          { id: "versions", label: "Versions and shadow mode" },
          { id: "feedback", label: "Feedback loop" },
        ]}
      />
      {tab === "performance" && <Async state={report}>{(data) => <Performance report={data} summary={summary.data} />}</Async>}
      {tab === "drift" && (
        <Async state={drift}>
          {(data) => (data.status === "ok" ? <DriftView drift={data} backtest={report.data?.insights?.drift} /> : <Empty>Not enough live decisions yet to measure drift.</Empty>)}
        </Async>
      )}
      {tab === "versions" && <Async state={registry}>{(data) => <Versions registry={data} shadow={shadow.data} />}</Async>}
      {tab === "feedback" && <Async state={feedback}>{(data) => <FeedbackView feedback={data} registry={registry.data} />}</Async>}
    </>
  );
}
