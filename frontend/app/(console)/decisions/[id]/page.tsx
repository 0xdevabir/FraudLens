"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ScoreBar } from "@/components/charts";
import { Id } from "@/components/session";
import { DecidedBy } from "@/components/tables";
import { Async, Badge, Button, Card, Chip, Empty, Facts, PageHeader, StatusBadge, Table, Td, TierBadge, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, pct, taka, when, words } from "@/lib/format";
import type { Decision, Lang, Narrative, Reason, RuleTrace, SimilarCase } from "@/lib/types";

const SCORE_LABEL: Record<string, string> = {
  risk: "Fused risk",
  txn: "Transaction model",
  mule: "Receiver mule score",
  anomaly: "Anomaly (isolation forest)",
};

function LangToggle({ lang, onChange }: { lang: Lang; onChange: (lang: Lang) => void }) {
  return (
    <span className="flex gap-1">
      <Chip on={lang === "en"} onClick={() => onChange("en")}>English</Chip>
      <Chip on={lang === "bn"} onClick={() => onChange("bn")}>বাংলা</Chip>
    </span>
  );
}

function Reasons({ reasons, lang }: { reasons: Reason[]; lang: Lang }) {
  if (!reasons.length) return <Empty>The model had nothing notable to say about this payment.</Empty>;
  const largest = Math.max(...reasons.map((reason) => Math.abs(reason.weight ?? 0)), 1e-9);
  return (
    <ol className="space-y-3" lang={lang}>
      {reasons.map((reason) => {
        const raises = reason.direction === "raises";
        return (
          <li key={reason.code} className="flex gap-3">
            <div className="w-24 shrink-0 pt-1">
              <div className="h-1.5 rounded-md bg-fill">
                <div
                  className={cx("h-1.5 rounded-md", reason.weight == null ? "bg-rule" : raises ? "bg-bad" : "bg-good")}
                  style={{ width: reason.weight == null ? "100%" : `${Math.max(6, (Math.abs(reason.weight) / largest) * 100)}%` }}
                />
              </div>
              <div className="mt-1 text-xs text-fg-3" lang="en">
                {reason.weight == null ? "rule matched" : raises ? "raises risk" : "lowers risk"}
                {reason.share != null && ` · ${pct(reason.share, 0)}`}
              </div>
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-fg">
                {lang === "bn" ? reason.title_bn : reason.title_en}
                <Badge tone={reason.source === "rule" ? "violet" : "slate"}>{reason.source}</Badge>
              </div>
              <p className="text-sm text-fg-2">{lang === "bn" ? reason.detail_bn : reason.detail_en}</p>
              {!!reason.facts?.length && (
                <ul className="mt-1 list-disc pl-5 text-xs text-fg-3">
                  {reason.facts.map((fact) => <li key={fact.feature}>{lang === "bn" ? fact.bn : fact.en}</li>)}
                </ul>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function show(value: unknown): string {
  if (value == null) return "unknown";
  if (typeof value === "number") return num(value, Number.isInteger(value) ? 0 : 3);
  return String(value);
}

function Rules({ trace }: { trace: RuleTrace[] }) {
  if (!trace.length) return <Empty>No rules were evaluated.</Empty>;
  return (
    <Table head={["Rule and what it looked at", "Result", "Effect"]}>
      {trace.map((rule) => (
        <tr key={rule.id} className={rule.status === "fired" ? "bg-bad/10" : ""}>
          <Td>
            <div className="font-mono text-xs text-fg-3">{rule.id}</div>
            <div className="text-fg">{rule.description}</div>
            <div className="mt-0.5 flex flex-wrap gap-1">
              {Object.entries(rule.inputs).map(([name, value]) => (
                <code key={name} className="rounded-md bg-fill px-1.5 py-0.5 text-xs text-fg-2">{name} = {show(value)}</code>
              ))}
            </div>
          </Td>
          <Td><StatusBadge status={rule.status} /></Td>
          <Td className="whitespace-nowrap text-fg-2">
            {words(rule.effect)} {rule.tier && <TierBadge tier={rule.tier} />}
            {rule.hard && <div className="text-xs text-fg-3">cannot be overridden by the model</div>}
          </Td>
        </tr>
      ))}
    </Table>
  );
}

function CaseSummary({ id, lang }: { id: string; lang: Lang }) {
  const narrative = useApi<Narrative>(`/v1/decisions/${id}/narrative?lang=${lang}`);
  return (
    <Async state={narrative}>
      {(data) => (
        <div lang={data.lang}>
          <div className="mb-2 flex flex-wrap items-center gap-2" lang="en">
            <Badge tone={data.source === "llm" ? "violet" : "slate"}>
              {data.source === "llm" ? "Written by the language model" : "Written from a template"}
            </Badge>
            <span className="text-xs text-fg-3">
              Built only from the structured evidence on this page. It describes the decision; it does not make it.
            </span>
          </div>
          {data.text.split("\n\n").map((paragraph, i) => (
            <p key={i} className="mb-2 whitespace-pre-line text-sm text-fg">{paragraph}</p>
          ))}
          {data.rejected.length > 0 && (
            <p className="mt-2 text-xs text-warn" lang="en">
              A language-model draft was discarded because it failed the evidence check ({data.rejected.join("; ")}), so
              the template text is shown.
            </p>
          )}
        </div>
      )}
    </Async>
  );
}

interface PastCase {
  case_id: number; typology: string; victim_id: string; mule_id: string; started_at: string; loss: number; transfers: number;
}

function PastCaseRow({ row }: { row: SimilarCase }) {
  const [open, setOpen] = useState(false);
  const detail = useApi<PastCase>(open ? `/v1/past-cases/${row.case_id}` : null);
  return (
    <>
      <tr>
        <Td>#{row.case_id}</Td>
        <Td>{words(row.typology)}</Td>
        <Td right>{pct(row.similarity, 0)}</Td>
        <Td right>{taka(row.loss)}</Td>
        <Td right>{num(row.n_txn)}</Td>
        <Td right><Button small onClick={() => setOpen(!open)}>{open ? "Hide" : "Details"}</Button></Td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="bg-wash px-3 py-2 text-sm">
            <Async state={detail}>
              {(data) => (
                <Facts
                  rows={[
                    ["Started", when(data.started_at)],
                    ["Victim", <Id key="v" value={data.victim_id} />],
                    ["Money went to", <Id key="m" value={data.mule_id} />],
                    ["Lost", `${taka(data.loss)} over ${num(data.transfers)} transfer${data.transfers === 1 ? "" : "s"}`],
                  ]}
                />
              )}
            </Async>
          </td>
        </tr>
      )}
    </>
  );
}

function Detail({ id, data }: { id: string; data: Decision }) {
  const [lang, setLang] = useState<Lang>("en");
  const txn = data.transaction;
  return (
    <>
      <PageHeader
        title={<span className="flex flex-wrap items-center gap-2">Payment #{txn.txn_id} <TierBadge tier={data.tier} /> <StatusBadge status={txn.status} /></span>}
        sub={<span lang={lang}>{data.headline[lang]}</span>}
        actions={
          <>
            <LangToggle lang={lang} onChange={setLang} />
            {data.case_id && <Link href={`/cases/${data.case_id}`} className="rounded-full bg-accent px-4 py-2 text-sm font-semibold text-accent-ink hover:brightness-110 active:scale-[0.97]">Open case #{data.case_id}</Link>}
          </>
        }
      />
      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4">
          <Card title="Payment">
            <Facts
              rows={[
                ["Type", words(txn.type)],
                ["Amount", <span key="a" className="font-semibold">{taka(txn.amount)}</span>],
                ["From", <Id key="s" value={txn.sender_id} caseId={data.case_id} />],
                ["To", <Id key="r" value={txn.receiver_id} caseId={data.case_id} />],
                ["Balance before", txn.sender_balance_before == null ? "unknown" : taka(txn.sender_balance_before)],
                ["Channel", txn.channel ?? "–"],
                ["District", txn.district ?? "–"],
                ["Sent at", when(txn.ts)],
                ["Outcome", <span key="o"><StatusBadge status={txn.status} /> {txn.status_reason && <span className="text-xs text-fg-3">{words(txn.status_reason)}</span>}</span>],
                ["Customer", data.customer_response ? `${words(data.customer_response)}${data.responded_at ? ` · ${when(data.responded_at)}` : ""}` : "no response recorded"],
              ]}
            />
          </Card>
          <Card title="Decision" hint="Scores come from the models. Rules are evaluated separately and can only raise the tier.">
            <Facts
              rows={[
                ["Risk score", <ScoreBar key="s" score={data.risk_score} />],
                ["Tier", <span key="t"><TierBadge tier={data.tier} /> <span className="text-xs text-fg-3">{words(data.action)}</span></span>],
                ["Model alone said", <TierBadge key="m" tier={data.model_tier} />],
                ["Decided by", <DecidedBy key="d" value={data.decided_by} />],
                ["Needs a reviewer", data.requires_review ? "Yes: a person must release or block it" : "No"],
                ...Object.entries(data.scores).map(([name, value]): [string, string] => [
                  SCORE_LABEL[name] ?? words(name),
                  value == null ? "not available" : num(value, 4),
                ]),
                ["Mode", data.mode === "model" ? "Model" : <Badge key="f" tone="amber">Rules-only fallback</Badge>],
                ["Versions", `model ${data.model_version} · policy ${data.policy_version}`],
                ["Decided in", `${num(data.latency_ms, 1)} ms`],
              ]}
            />
            {data.fallback_signals.length > 0 && (
              <p className="mt-3 text-xs text-fg-3">Fallback signals: {data.fallback_signals.map(words).join(", ")}</p>
            )}
          </Card>
          <Card title="What the customer saw" hint="Chosen by tier and scam type from fixed, reviewed wording.">
            {data.customer_message ? (
              <div className="space-y-2 text-sm">
                <p lang="bn" className="rounded-xl bg-warn/10 p-3 text-fg">{data.customer_message.bn}</p>
                <p className="text-fg-2">{data.customer_message.en}</p>
              </div>
            ) : (
              <Empty>Nothing: the payment went through without interruption.</Empty>
            )}
          </Card>
        </div>
        <div className="space-y-4 xl:col-span-2">
          <Card title="Case summary" hint="Plain-language account for the reviewer.">
            <CaseSummary id={id} lang={lang} />
          </Card>
          <Card title="Why it was flagged" hint="Model reasons are SHAP contributions grouped into plain statements; rule reasons come from the rule trace.">
            <Reasons reasons={data.reasons} lang={lang} />
          </Card>
          <Card title="Rule trace" hint={`Policy ${data.policy_version}: every rule that was checked, and what it saw.`} flush>
            <Rules trace={data.rule_trace} />
          </Card>
          <Card title="Suggested next steps" hint="Suggestions only. Anything that freezes a wallet needs a second person.">
            {data.recommended_actions.length ? (
              <ul className="space-y-2 text-sm" lang={lang}>
                {data.recommended_actions.map((action) => (
                  <li key={action.id} className="flex gap-2">
                    <span className="text-fg-4">→</span>
                    <span>
                      {action[lang]}{" "}
                      {action.needs_second_approver && <Badge tone="violet">needs second approver</Badge>}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No action is suggested.</Empty>
            )}
          </Card>
          <Card title="Similar past cases" hint="Closest confirmed cases by behaviour, from the labelled history." flush>
            {data.similar_cases.length ? (
              <Table head={["Case", "Scam type", "Similarity", "Lost", "Transfers", ""]}>
                {data.similar_cases.map((row) => <PastCaseRow key={row.case_id} row={row} />)}
              </Table>
            ) : (
              <Empty>No similar past case was found.</Empty>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

export default function DecisionPage() {
  const { id } = useParams<{ id: string }>();
  const decision = useApi<Decision>(/^\d+$/.test(id) ? `/v1/decisions/${id}` : null);
  if (!/^\d+$/.test(id)) return <Empty>That is not a payment number.</Empty>;
  return <Async state={decision}>{(data) => <Detail id={id} data={data} />}</Async>;
}
