"use client";

import { useState } from "react";

import { useSession } from "@/components/session";
import { Async, Badge, Button, Card, ErrorNote, inputClass, Modal, PageHeader, Table, Td, type Tone, TierBadge } from "@/components/ui";
import { api, download, useApi } from "@/lib/api";
import { num, pct, TIERS, when, words } from "@/lib/format";
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
    <Card
      title="Bangla review"
      hint="The texts were written by the developers. A translator signs each one off here; a sign-off covers the exact wording, so changing a text makes it unreviewed again."
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
      <div className="mt-4"><TranslationReview /></div>
    </>
  );
}
