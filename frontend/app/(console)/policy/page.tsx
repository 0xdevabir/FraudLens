"use client";

import { useState } from "react";

import { Details, DocSection, H3, KeyPoints, Note, Quote } from "@/components/doc";
import { useSession } from "@/components/session";
import { Async, Badge, Button, Card, ErrorNote, inputClass, Modal, PageHeader, Table, Td, type Tone, TierBadge } from "@/components/ui";
import { api, download, useApi } from "@/lib/api";
import { num, pct, TIER_LABEL, TIERS, when, words } from "@/lib/format";
import type { Report, Text2, Tier, TranslationText, Translations } from "@/lib/types";

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
  proceed: "The payment goes through.",
  show_warning: "The customer sees a warning, then chooses whether to go on.",
  step_up_auth: "The customer must confirm again, then wait a short time.",
  hold_for_review: "The payment is paused until a reviewer decides.",
};
const SCENARIO: Record<string, string> = {
  scam: "Paying a scammer",
  takeover: "Someone else has taken over the account",
  unusual_access: "Payment from an unusual place or network",
  cash_out: "Cash-out",
};

function tierName(tier: string): string {
  return TIER_LABEL[tier as Tier] ?? words(tier);
}

/** A rule's exact condition, as the engine checks it. */
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
  const overrides = Object.keys(policy.thresholds.overrides);
  const fallbackPoints = Object.entries(policy.fallback.points).map(([tier, points]) => `${points} points means ${tierName(tier)}`);
  return (
    <div className="space-y-10">
      <KeyPoints
        points={[
          "The model gives every payment a fraud score. The score alone does not decide anything.",
          <>This policy turns the score into one of four levels: <TierBadge tier="allow" />, <TierBadge tier="warn" />, <TierBadge tier="step_up" /> or <TierBadge tier="hold" />.</>,
          "Extra rules can make the level stricter. They can never make it softer.",
          "No money is blocked or frozen without a person deciding.",
          "Customers only see fixed messages that were written and approved in advance.",
        ]}
      />

      <DocSection
        number={1}
        title="The four levels"
        sub={`Policy ${policy.version}`}
        aside={<Badge tone={policy.mode === "model" ? "green" : "amber"}>{policy.mode === "model" ? "Running: model and rules" : "Running: rules only (backup)"}</Badge>}
        intro={
          <>
            <p>Each payment gets a fraud score. The higher the score, the stronger the action.</p>
            <p><span className="font-medium text-fg">What this version adds:</span> {policy.description}</p>
          </>
        }
      >
        <Card tour="policy-tiers" flush>
          <Table head={["Level", "Starts at fraud score", "What happens", "Does a person decide?", "Waiting time", "Time limit for review"]}>
            {TIERS.map((tier) => {
              const row = policy.tiers[tier];
              return (
                <tr key={tier}>
                  <Td><TierBadge tier={tier} /></Td>
                  <Td right>{tier === "allow" ? `below ${tierName("warn")}` : pct(policy.resolved_thresholds[tier], 2)}</Td>
                  <Td className="whitespace-normal">{ACTION[row.action] ?? words(row.action)}</Td>
                  <Td>{row.human_review ? <Badge tone="red">yes</Badge> : "no"}</Td>
                  <Td right>{row.cooling_off_minutes ? `${row.cooling_off_minutes} min` : "–"}</Td>
                  <Td right>{row.review_sla_minutes ? `${row.review_sla_minutes} min` : "–"}</Td>
                </tr>
              );
            })}
          </Table>
          <Note>
            {policy.thresholds.source === "model"
              ? "The starting scores were picked when the current model was tested, so that the number of alerts stays within a set limit."
              : `The starting scores come from: ${words(policy.thresholds.source).toLowerCase()}.`}
            {overrides.length > 0 && ` Some were set by hand: ${overrides.map(tierName).join(", ")}.`}{" "}
            The model only gives a score. This policy decides the action, and it has its own version and its own review.
          </Note>
        </Card>
      </DocSection>

      <DocSection
        number={2}
        title="Extra rules"
        tour="policy-rules"
        intro={
          <>
            <p>Rules run next to the model. A rule can only make the level stricter.</p>
            <p>A rule marked <Badge tone="red">always applies</Badge> works whatever the fraud score is.</p>
          </>
        }
      >
        <Card flush>
          <Table
            head={[
              "Rule",
              "Raises to",
              <span key="f" title="How many test payments this rule caught">Times used in testing</span>,
              <span key="p" title="Of the payments this rule caught, the share that were really fraud">Was really fraud</span>,
              <span key="d" title="How many times this rule set the final level">Changed the decision</span>,
            ]}
          >
            {policy.rules.map((rule) => {
              const m = measured[rule.id];
              return (
                <tr key={rule.id}>
                  <Td className="whitespace-normal">
                    <div className="flex flex-wrap items-center gap-1.5 text-fg">
                      {rule.description}
                      {rule.hard && <Badge tone="red">always applies</Badge>}
                    </div>
                    <div lang="bn" className="mt-0.5 text-[0.8125rem] text-fg-3">{rule.description_bn}</div>
                    <div className="mt-1 text-xs text-fg-3">
                      Checks: {rule.applies_to.map((type) => words(type).toLowerCase()).join(" and ")}
                    </div>
                    <Details>
                      <div className="font-mono text-fg-3">{rule.id}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">Applies when <When conditions={rule.when} /></div>
                    </Details>
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
            <Note>
              In testing, the rules stopped {num((counts.model_only.allow ?? 0) - (counts.policy.allow ?? 0))} payments from going straight
              through that the model alone would have allowed. The rules about wallets confirmed as fraud only start working after reviewers
              confirm a wallet. The test data has no confirmed wallets, so those rules could not be tested here.
            </Note>
          )}
        </Card>
      </DocSection>

      <DocSection
        number={3}
        title="If the model stops working"
        intro={
          <>
            <p>Simple checks take over. Each check that is true adds one point.</p>
            <p>{fallbackPoints.join(". ")}. It never goes higher than {tierName(policy.fallback.max_tier)}.</p>
          </>
        }
      >
        <Card flush>
          <Table head={["Check"]}>
            {policy.fallback.signals.map((signal) => (
              <tr key={signal.id}>
                <Td className="whitespace-normal">
                  {words(signal.id)}
                  <Details><When conditions={signal.when} /></Details>
                </Td>
              </tr>
            ))}
          </Table>
          {counts?.rules_only_fallback && (
            <Note>
              Tried on the test data, this backup would have warned {num(counts.rules_only_fallback.warn)} payments and asked{" "}
              {num(counts.rules_only_fallback.step_up)} to confirm again. It never holds a payment. Without the model, no score is strong enough
              to stop someone’s money.
            </Note>
          )}
        </Card>
      </DocSection>

      <DocSection
        number={4}
        title="Next steps suggested to reviewers"
        intro={<p>A suggestion shows on a decision when the level and the conditions match. These are only suggestions.</p>}
      >
        <Card flush>
          <Table head={["From level", "Suggestion"]}>
            {policy.recommendations.map((item) => (
              <tr key={item.id}>
                <Td><TierBadge tier={item.min_tier} /></Td>
                <Td className="whitespace-normal">
                  <div className="text-fg">
                    {item.en} {item.needs_second_approver && <Badge tone="violet">needs a second person</Badge>}
                  </div>
                  <div lang="bn" className="mt-0.5 text-[0.8125rem] text-fg-3">{item.bn}</div>
                  {item.when.length > 0 && <Details label="When it shows"><When conditions={item.when} /></Details>}
                </Td>
              </tr>
            ))}
          </Table>
        </Card>
      </DocSection>

      <DocSection
        number={5}
        title="What the customer is told"
        intro={
          <>
            <p>These messages were written and approved in advance. They are shown word for word. The language model never writes to customers.</p>
            <Details label={`When the “${SCENARIO.takeover}” wording is used`}><When conditions={policy.takeover_when} /></Details>
          </>
        }
      >
        <div className="space-y-6">
          {Object.entries(policy.messages).map(([scenario, byTier]) => (
            <div key={scenario}>
              <H3>{SCENARIO[scenario] ?? words(scenario)}</H3>
              <div className="space-y-2">
                {TIERS.filter((tier) => byTier[tier]).map((tier) => (
                  <Quote key={tier} label={<TierBadge tier={tier} />} bn={byTier[tier]?.bn} en={byTier[tier]?.en} />
                ))}
              </div>
            </div>
          ))}
        </div>
      </DocSection>
    </div>
  );
}

const REVIEW_TONE: Record<TranslationText["status"], Tone> = { approved: "green", changes_requested: "amber", unreviewed: "slate", outdated: "orange" };
const REVIEW_LABEL: Record<TranslationText["status"], string> = {
  approved: "Signed off", changes_requested: "Changes asked", unreviewed: "Not reviewed", outdated: "Wording changed",
};

function SignOff({ item, onClose, onDone }: { item: TranslationText; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [status, setStatus] = useState<"approved" | "changes_requested">("approved");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim().length < 2 || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/v1/policy/translations/${encodeURIComponent(item.key).replace(/%3A/g, ":")}/review`, {
        status, reviewer_name: name.trim(), ...(note.trim() ? { note: note.trim() } : {}),
      });
      onDone();
      onClose();
    } catch (problem) {
      setError(problem as Error);
      setBusy(false);
    }
  }
  return (
    <Modal title={`Sign off: ${item.label}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <p lang="bn" className="rounded-xl bg-white/6 p-3 text-sm text-fg">{item.bn}</p>
        <p className="text-xs text-fg-3">{item.en}</p>
        <label className="block text-xs font-medium text-fg-2">
          Translator&apos;s name
          <input autoFocus maxLength={120} className={`${inputClass} mt-1 block w-full`} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="block text-xs font-medium text-fg-2">
          Verdict
          <select className={`${inputClass} mt-1 block w-full`} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
            <option value="approved">The Bangla is right</option>
            <option value="changes_requested">Changes needed</option>
          </select>
        </label>
        <label className="block text-xs font-medium text-fg-2">
          Note (optional)
          <textarea rows={2} maxLength={1000} className={`${inputClass} mt-1 block w-full`} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        {error && <ErrorNote error={error} />}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={name.trim().length < 2 || busy}>{busy ? "Working…" : "Record"}</Button>
        </div>
      </form>
    </Modal>
  );
}

/** Which Bangla texts a translator has read. A sign-off covers the exact wording, so an edit un-signs it. */
function TranslationReview() {
  const { canAudit } = useSession();
  const sheet = useApi<Translations>("/v1/policy/translations");
  const [open, setOpen] = useState<TranslationText | null>(null);
  const [error, setError] = useState<Error | null>(null);
  return (
    <DocSection
      number={6}
      title="Bangla translation check"
      intro={
        <>
          <p>The developers wrote these Bangla texts. A translator checks each one and signs it off here.</p>
          <p>A sign-off is for the exact wording. If a text is changed, it needs a new sign-off.</p>
        </>
      }
    >
      <Card
        title="Bangla texts"
        actions={
          <Button small onClick={() => download("/v1/policy/translations.csv", "fraudlens-bangla-texts.csv").catch(setError)}>
            Sheet for the translator
          </Button>
        }
        flush
      >
        {error && <div className="px-4 pt-3"><ErrorNote error={error} /></div>}
        <Async state={sheet}>
          {(data) => (
            <>
              <div className="flex flex-wrap gap-2 border-b border-line px-4 py-3 text-xs text-fg-2">
                {(Object.keys(REVIEW_LABEL) as TranslationText["status"][]).map((s) => (
                  <Badge key={s} tone={REVIEW_TONE[s]}>{REVIEW_LABEL[s]}: {num(data.counts[s] ?? 0)}</Badge>
                ))}
                <span className="ml-auto text-fg-3">Policy {data.policy_version}</span>
              </div>
              <Table head={["Text", "বাংলা", "Review", ""]}>
                {data.texts.map((item) => (
                  <tr key={item.key}>
                    <Td className="whitespace-nowrap font-mono text-xs text-fg-3">{item.label}</Td>
                    <Td className="max-w-xl whitespace-normal"><span lang="bn">{item.bn}</span></Td>
                    <Td className="whitespace-normal">
                      <Badge tone={REVIEW_TONE[item.status]}>{REVIEW_LABEL[item.status]}</Badge>
                      {item.reviewed_by && <div className="mt-1 text-xs text-fg-3">{item.reviewed_by} · {when(item.reviewed_at)}</div>}
                      {item.note && <div className="text-xs text-fg-3">{item.note}</div>}
                    </Td>
                    <Td>{canAudit && <Button small onClick={() => setOpen(item)}>Sign off</Button>}</Td>
                  </tr>
                ))}
              </Table>
              {open && <SignOff item={open} onClose={() => setOpen(null)} onDone={sheet.reload} />}
            </>
          )}
        </Async>
      </Card>
    </DocSection>
  );
}

export default function PolicyPage() {
  const policy = useApi<Policy>("/v1/policy");
  const report = useApi<Report>("/v1/model/report");
  return (
    <>
      <PageHeader tour="policy-header"
        title="Decision policy"
        sub="How FraudLens decides what to do with each payment, and what the customer is told."
      />
      <Async state={policy}>{(data) => <View policy={data} report={report.data} />}</Async>
      <div className="mt-10"><TranslationReview /></div>
    </>
  );
}
