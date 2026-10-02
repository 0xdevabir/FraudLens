"use client";

import { Async, Badge, Card, PageHeader, Table, Td, TierBadge } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, pct, TIERS, words } from "@/lib/format";
import type { Report, Text2, Tier } from "@/lib/types";

interface Condition { field: string; op: string; value: number | string }
interface Policy {
  version: string;
  description: string;
  mode: string;
  thresholds: { source: string; overrides: Record<string, number> };
  resolved_thresholds: Record<"warn" | "step_up" | "hold", number>;
  tiers: Record<Tier, { action: string; human_review: boolean; cooling_off_minutes: number | null; review_sla_minutes: number | null }>;
  rules: {
    id: string; description: string; description_bn: string; applies_to: string[]; when: Condition[];
    effect: string; tier: Tier; hard: boolean; reason: string; scenario: string | null;
  }[];
  fallback: { signals: { id: string; when: Condition[] }[]; points: Record<string, number>; max_tier: Tier };
  takeover_when: Condition[];
  messages: Record<string, Partial<Record<Tier, Text2>>>;
  recommendations: { id: string; min_tier: Tier; applies_to: string[]; when: Condition[]; en: string; bn: string; needs_second_approver: boolean }[];
}

const ACTION: Record<string, string> = {
  proceed: "The payment goes through",
  show_warning: "The customer sees a warning and chooses",
  step_up_auth: "The customer verifies again, then waits",
  hold_for_review: "Paused until a reviewer decides",
};
const SCENARIO: Record<string, string> = { scam: "Paying a scammer", takeover: "Account taken over", unusual_access: "Unusual place or network", cash_out: "Cash-out" };

function When({ conditions }: { conditions: Condition[] }) {
  if (!conditions.length) return <span className="text-fg-4">always</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {conditions.map((c, i) => (
        <code key={i} className="rounded-md bg-fill px-1.5 py-0.5 text-xs text-fg">{c.field} {c.op} {String(c.value)}</code>
      ))}
    </span>
  );
}

function View({ policy, report }: { policy: Policy; report?: Report }) {
  const measured = report?.policy.rules ?? {};
  const counts = report?.policy.tier_counts;
  return (
    <div className="space-y-4">
      <Card
        title={`Policy ${policy.version}`}
        hint={policy.description}
        actions={<Badge tone={policy.mode === "model" ? "green" : "amber"}>{policy.mode === "model" ? "model and rules" : "rules-only fallback"}</Badge>}
        flush
      >
        <Table head={["Tier", "From fraud probability", "What happens", "A person decides", "Cooling-off", "Review deadline"]}>
          {TIERS.map((tier) => {
            const row = policy.tiers[tier];
            return (
              <tr key={tier}>
                <Td><TierBadge tier={tier} /></Td>
                <Td right>{tier === "allow" ? "below warn" : pct(policy.resolved_thresholds[tier], 2)}</Td>
                <Td>{ACTION[row.action] ?? words(row.action)}</Td>
                <Td>{row.human_review ? <Badge tone="red">yes</Badge> : "no"}</Td>
                <Td right>{row.cooling_off_minutes ? `${row.cooling_off_minutes} min` : "–"}</Td>
                <Td right>{row.review_sla_minutes ? `${row.review_sla_minutes} min` : "–"}</Td>
              </tr>
            );
          })}
        </Table>
        <p className="border-t border-line px-4 py-2 text-xs text-fg-3">
          Thresholds come from {policy.thresholds.source === "model" ? "the served model’s validation run, set to an alert budget" : words(policy.thresholds.source).toLowerCase()}
          {Object.keys(policy.thresholds.overrides).length ? `, with overrides for ${Object.keys(policy.thresholds.overrides).join(", ")}` : ""}. The
          model only produces a score; this policy, which is versioned and reviewed separately, turns it into an action. Nothing is blocked or frozen
          without a person.
        </p>
      </Card>

      <Card title="Rules" hint="Rules run beside the model and can only raise a tier. A hard rule applies whatever the score is." flush>
        <Table head={["Rule", "Raises to", "Fired in test", "Right when fired", "Changed the outcome"]}>
          {policy.rules.map((rule) => {
            const m = measured[rule.id];
            return (
              <tr key={rule.id}>
                <Td className="whitespace-normal">
                  <div className="flex items-center gap-1.5 font-mono text-xs text-fg-3">{rule.id}{rule.hard && <Badge tone="red">hard</Badge>}</div>
                  <div className="text-fg">{rule.description}</div>
                  <div lang="bn" className="text-xs text-fg-3">{rule.description_bn}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-fg-3">
                    On {rule.applies_to.map((type) => words(type).toLowerCase()).join(" and ")}, when <When conditions={rule.when} />
                  </div>
                </Td>
                <Td><TierBadge tier={rule.tier} /></Td>
                <Td right>{m ? num(m.fired) : "–"}</Td>
                <Td right>{m ? pct(m.fired_precision, 0) : "–"}</Td>
                <Td right>{m ? num(m.decisive) : "–"}</Td>
              </tr>
            );
          })}
        </Table>
        {counts?.policy && counts.model_only && (
          <p className="border-t border-line px-4 py-2 text-xs text-fg-3">
            On the test period the rules moved {num((counts.model_only.allow ?? 0) - (counts.policy.allow ?? 0))} payments out of “allow” that the
            model alone would have let through. The rules that hold payments to confirmed-fraud wallets fire only once reviewers have confirmed
            wallets, which the offline test does not have.
          </p>
        )}
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card
          title="If the model is unavailable"
          hint={`Decisions fall back to rules alone: each signal below scores one point. ${Object.entries(policy.fallback.points).map(([tier, points]) => `${points} points → ${words(tier).toLowerCase()}`).join(", ")}; never above ${words(policy.fallback.max_tier).toLowerCase()}.`}
          flush
        >
          <Table head={["Signal", "When"]}>
            {policy.fallback.signals.map((signal) => (
              <tr key={signal.id}>
                <Td>{words(signal.id)}</Td>
                <Td><When conditions={signal.when} /></Td>
              </tr>
            ))}
          </Table>
          {counts?.rules_only_fallback && (
            <p className="border-t border-line px-4 py-2 text-xs text-fg-3">
              Replayed on the test period, the fallback would have warned {num(counts.rules_only_fallback.warn)} payments and asked{" "}
              {num(counts.rules_only_fallback.step_up)} to verify again. It holds nothing, because without the model there is no score strong enough
              to stop someone’s money.
            </p>
          )}
        </Card>
        <Card title="Next steps suggested to reviewers" hint="Shown on a decision when its tier and conditions match. Suggestions only." flush>
          <Table head={["From tier", "Suggestion"]}>
            {policy.recommendations.map((item) => (
              <tr key={item.id}>
                <Td><TierBadge tier={item.min_tier} /></Td>
                <Td className="whitespace-normal">
                  {item.en} {item.needs_second_approver && <Badge tone="violet">needs a second person</Badge>}
                  <div lang="bn" className="text-xs text-fg-3">{item.bn}</div>
                  {item.when.length > 0 && <div className="mt-1"><When conditions={item.when} /></div>}
                </Td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>

      <Card
        title="What the customer is told"
        hint="Fixed texts, written and approved in advance. The language model never writes to customers."
        actions={<span className="text-xs text-fg-3">Takeover wording is used when <When conditions={policy.takeover_when} /></span>}
        flush
      >
        <Table head={["Situation", "Tier", "বাংলা", "English"]}>
          {Object.entries(policy.messages).flatMap(([scenario, byTier]) =>
            TIERS.filter((tier) => byTier[tier]).map((tier) => (
              <tr key={`${scenario}-${tier}`}>
                <Td>{SCENARIO[scenario] ?? words(scenario)}</Td>
                <Td><TierBadge tier={tier} /></Td>
                <Td className="max-w-md whitespace-normal"><span lang="bn">{byTier[tier]?.bn}</span></Td>
                <Td className="max-w-md whitespace-normal text-fg-2">{byTier[tier]?.en}</Td>
              </tr>
            )),
          )}
        </Table>
      </Card>
    </div>
  );
}

export default function PolicyPage() {
  const policy = useApi<Policy>("/v1/policy");
  const report = useApi<Report>("/v1/model/report");
  return (
    <>
      <PageHeader
        title="Decision policy"
        sub="How a risk score becomes an action: the tier thresholds, the rules that sit beside the model, the rules-only fallback, and the words customers see."
      />
      <Async state={policy}>{(data) => <View policy={data} report={report.data} />}</Async>
    </>
  );
}
