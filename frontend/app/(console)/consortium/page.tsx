"use client";

import { useState } from "react";

import { useSession } from "@/components/session";
import { Async, Badge, Button, Card, Empty, Facts, PageHeader, Stat, Table, Tabs, Td, inputClass } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { maskId, num, pct, taka, when, words } from "@/lib/format";
import type { ConsortiumAuditEntry, ConsortiumLookup, ConsortiumMatch, ConsortiumOverview, SeedStat } from "@/lib/types";

type Tab = "feeds" | "matches" | "results" | "audit";

const ARM_LABEL: Record<string, string> = {
  mule_model_only: "Mule model alone (today)",
  device: "+ partner handsets, tuned on val_b",
  msisdn: "+ partner wallet numbers, tuned on val_b",
  msisdn_and_device: "+ both, tuned on val_b",
  msisdn_confirmed_rule: "+ rule: partner-confirmed number",
  msisdn_and_device_confirmed_rule: "+ rule: partner-confirmed number or handset",
};

function mean(s: SeedStat | undefined, digits = 0, asPct = false): string {
  if (!s) return "–";
  const fmt = (v: number) => (asPct ? pct(v, digits) : num(v, digits));
  return s.min === s.max ? fmt(s.mean) : `${fmt(s.mean)} (${fmt(s.min)}–${fmt(s.max)})`;
}

export default function ConsortiumPage() {
  const overview = useApi<ConsortiumOverview>("/v1/consortium");
  const [tab, setTab] = useState<Tab>("feeds");
  return (
    <>
      <PageHeader
        title="Mule consortium"
        sub="Four simulated providers share confirmed and suspected mule wallets as keyed tokens. No wallet number or handset id leaves a provider. Simulation on the FraudLens dataset; provider names are illustrative."
      />
      <Async state={overview}>
        {(o) => (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Members" value={num(o.members.length)} sub={`key epoch ${o.key_epoch}`} />
              <Stat label="Confirmed listings shared" value={num(o.feeds.reduce((a, f) => a + f.confirmed_listings, 0))} sub="as tokens, signed per provider" />
              <Stat label="Suspected, in filters only" value={num(o.feeds.reduce((a, f) => a + f.suspected_in_filter, 0))} sub="testable, not listable" />
              <Stat label="Hub audit chain" value={o.audit.chain_ok ? "intact" : "broken"} tone={o.audit.chain_ok ? "good" : "bad"} sub={`${num(o.audit.entries)} entries`} />
            </div>
            <Tabs<Tab>
              tabs={[
                { id: "feeds", label: "Partner feeds" },
                { id: "matches", label: "Matches" },
                { id: "results", label: "Measured effect" },
                { id: "audit", label: "Audit and disputes" },
              ]}
              value={tab}
              onChange={setTab}
            />
            {tab === "feeds" && <Feeds o={o} />}
            {tab === "matches" && <Matches />}
            {tab === "results" && <Results o={o} />}
            {tab === "audit" && <AuditTab o={o} reload={overview.reload} />}
          </div>
        )}
      </Async>
    </>
  );
}

function Feeds({ o }: { o: ConsortiumOverview }) {
  return (
    <>
      <Card title="Latest signed bundle from each provider" hint={`As of ${when(o.clock)}. Every bundle is re-verified against its provider's key when loaded.`} flush>
        <Table head={["Provider", "Bundle", "Confirmed", "By kind", "Suspected (filter)", "Withdrawn", "Size", "Signature", "Valid until"]}>
          {o.feeds.map((f) => (
            <tr key={f.provider} className="hover:bg-wash">
              <Td>{f.display}</Td>
              <Td className="font-mono text-xs">#{f.seq}</Td>
              <Td right>{num(f.confirmed_listings)}</Td>
              <Td>{Object.entries(f.by_kind).map(([k, v]) => `${words(k)} ${v}`).join(", ") || "–"}</Td>
              <Td right>{num(f.suspected_in_filter)} <span className="text-fg-4">in {num(f.filter_bits)} bits</span></Td>
              <Td right>{num(f.withdrawn)}</Td>
              <Td right>{num(f.bytes / 1024, 1)} KB</Td>
              <Td>{f.signature_ok ? <Badge tone="green" title={`key ${f.key_fingerprint}`}>valid</Badge> : <Badge tone="red">invalid</Badge>}</Td>
              <Td>{when(f.valid_until)}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Privacy guarantee">
        <ul className="list-disc space-y-1 pl-5 text-sm text-fg-2">
          {o.guarantees.map((g) => (
            <li key={g}>{g}</li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-fg-3">OPRF key fingerprint {o.oprf_key_fingerprint}. See docs/CONSORTIUM.md for the threat model.</p>
      </Card>
      <LookupCard />
    </>
  );
}

function LookupCard() {
  const [wallet, setWallet] = useState("");
  const [out, setOut] = useState<ConsortiumLookup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setError(null);
    try {
      setOut(await api<ConsortiumLookup>("/v1/consortium/lookup", { wallet_id: wallet.trim() }));
    } catch (e) {
      setOut(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <Card title="Receiver lookup" hint="What the wallet's own provider learns from partners: its number and handsets are tokenised through the hub, then checked against partner bundles. Recorded in the audit log.">
      <div className="flex flex-wrap gap-2">
        <input className={inputClass} placeholder="Wallet id, e.g. W0001234" value={wallet} onChange={(e) => setWallet(e.target.value)} />
        <Button variant="primary" disabled={!wallet.trim()} onClick={run}>Look up</Button>
      </div>
      {error && <p className="mt-2 text-sm text-bad">{error}</p>}
      {out && (
        <div className="mt-3">
          <Facts
            rows={[
              ["Home provider", out.home],
              ["Identifiers checked", `${out.identifiers_checked.msisdn} number, ${out.identifiers_checked.device} handset(s)`],
              ["Consortium signal", num(out.signal, 3)],
            ]}
          />
          {out.matches.length ? (
            <ul className="mt-2 space-y-1 text-sm">
              {out.matches.map((m, i) => (
                <li key={i}>
                  <Badge tone={m.status === "confirmed" ? "red" : "amber"}>{m.status}</Badge> {m.provider}, {words(m.kind)}, confidence {num(m.confidence, 3)}
                  {m.listing_id && <span className="font-mono text-xs text-fg-3"> {m.listing_id} · {words(m.typology)}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-fg-3">No partner knows this wallet.</p>
          )}
        </div>
      )}
    </Card>
  );
}

function Matches() {
  const matches = useApi<ConsortiumMatch[]>("/v1/consortium/matches?limit=300");
  return (
    <Card title="Test-period receivers that matched a partner listing" hint="One row per wallet, at its first match. 'Truth' is the simulation's label, shown so the demo can be judged; a provider would not have it." flush>
      <Async state={matches}>
        {(rows) =>
          rows.length ? (
            <Table head={["Wallet", "First match", "Home", "Listed by", "Via", "Status", "Confidence", "Mule score", "Model alone alerts", "Truth"]}>
              {rows.map((m) => (
                <tr key={m.wallet_id} className="hover:bg-wash">
                  <Td className="font-mono text-xs">{maskId(m.wallet_id)}</Td>
                  <Td>{when(m.at)}</Td>
                  <Td>{m.home}</Td>
                  <Td>{m.partners.join(", ")}</Td>
                  <Td>{m.kinds.map(words).join(", ")}</Td>
                  <Td><Badge tone={m.status === "confirmed" ? "red" : "amber"}>{m.status}</Badge></Td>
                  <Td right>{num(m.confidence, 3)}</Td>
                  <Td right>{num(m.mule_score, 3)}</Td>
                  <Td>{m.mule_model_alone_would_alert ? "yes" : <Badge tone="blue">no: consortium only</Badge>}</Td>
                  <Td><Badge tone={m.simulation_truth === "mule" ? "green" : "slate"}>{m.simulation_truth}</Badge></Td>
                </tr>
              ))}
            </Table>
          ) : (
            <Empty>No receiver matched a partner listing in the test period.</Empty>
          )
        }
      </Async>
    </Card>
  );
}

function Results({ o }: { o: ConsortiumOverview }) {
  const r = o.results;
  const head = r.results_by_shared_sim_rate[r.headline_share.toFixed(2)]?.summary ?? {};
  const arms = Object.keys(head);
  return (
    <>
      <Card
        title="Receiver alerts on the test period, with and without partner signals"
        hint={`Model ${r.model_version}; ${r.seeds.length} market seeds (mean, with min–max); ${pct(r.headline_share, 0)} of people hold a second wallet on the same SIM. Tuned arms pick their bars on val_b so no more innocent wallets are alerted there than today.`}
        flush
      >
        <Table head={["Arm", "Recall", "False alerts", "FPR", "Caught only via consortium", "Caught before any victim paid", "Taka paid to detected mules first"]}>
          {arms.map((a) => (
            <tr key={a} className="hover:bg-wash">
              <Td>{ARM_LABEL[a] ?? a}</Td>
              <Td right>{mean(head[a]?.recall, 1, true)}</Td>
              <Td right>{mean(head[a]?.false_alerts, 1)}</Td>
              <Td right>{mean(head[a]?.fpr, 2, true)}</Td>
              <Td right>{mean(head[a]?.mules_found_only_with_consortium, 1)}</Td>
              <Td right>{mean(head[a]?.["timing.detected_before_any_victim_paid"], 1)}</Td>
              <Td right>{head[a]?.["timing.taka_paid_to_detected_mules_before_detection"] ? taka(head[a]?.["timing.taka_paid_to_detected_mules_before_detection"]?.mean) : "–"}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Poisoning: a dishonest member lists innocent customers" hint={`${r.poisoning.by} lists ${num(r.poisoning.listings)} innocent numbers as confirmed mules at the start of the test period.`} flush>
        <Table head={["Arm", "False alerts, clean", "False alerts, attacked", "Recall, attacked"]}>
          {Object.keys(r.poisoning.clean).map((a) => (
            <tr key={a} className="hover:bg-wash">
              <Td>{ARM_LABEL[a] ?? a}</Td>
              <Td right>{num(r.poisoning.clean[a].false_alerts)}</Td>
              <Td right>{num(r.poisoning.attacked[a].false_alerts)}</Td>
              <Td right>{pct(r.poisoning.attacked[a].recall, 1)}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Where the numbers come from">
        <Facts
          rows={[
            ["Confirmed listing confidence", `${num(r.listing_confidence.confirmed, 3)}: ${r.listing_confidence.confirmed_basis}`],
            ["Suspected listing confidence", `${num(r.listing_confidence.suspected, 3)}: ${r.listing_confidence.suspected_basis}`],
            ["Token cost (measured)", `${num(r.cost.oprf.client_ms_per_token, 1)} ms client + ${num(r.cost.oprf.hub_ms_per_token, 1)} ms hub per token; ${num(r.cost.tokens_made)} tokens made`],
          ]}
        />
      </Card>
    </>
  );
}

function AuditTab({ o, reload }: { o: ConsortiumOverview; reload: () => void }) {
  const { me } = useSession();
  const entries = useApi<ConsortiumAuditEntry[]>("/v1/consortium/audit?limit=150");
  const [listing, setListing] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const raisedBy = o.members[0]?.name ?? "";
  const act = async (path: string, body: unknown) => {
    setError(null);
    try {
      await api(path, body);
      reload();
      entries.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <>
      <Card title="Disputes" hint="A disputed listing stops counting for every member at once. Only the listing member decides; if it does not answer in 5 days the dispute is decided for the customer.">
        <div className="flex flex-wrap gap-2">
          <input className={inputClass} placeholder="Listing id, e.g. nagad:12" value={listing} onChange={(e) => setListing(e.target.value)} />
          <input className={`${inputClass} min-w-64 flex-1`} placeholder="Reason (customer appeal, evidence…)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button variant="primary" disabled={!listing.trim() || reason.trim().length < 3} onClick={() => act("/v1/consortium/disputes", { listing_id: listing.trim(), raised_by: raisedBy, reason })}>
            Dispute as {raisedBy}
          </Button>
        </div>
        {error && <p className="mt-2 text-sm text-bad">{error}</p>}
        {o.disputes.length ? (
          <div className="mt-3">
            <Table head={["Dispute", "Listing", "Raised by", "Reason", "Opened", "Status", ""]}>
              {o.disputes.map((d) => (
                <tr key={d.dispute_id}>
                  <Td className="font-mono text-xs">{d.dispute_id}</Td>
                  <Td className="font-mono text-xs">{d.listing_id}</Td>
                  <Td>{d.raised_by}</Td>
                  <Td>{d.reason}</Td>
                  <Td>{when(d.opened_at)}</Td>
                  <Td><Badge tone={d.status === "open" ? "amber" : d.status === "withdrawn" ? "green" : "slate"}>{d.status}</Badge></Td>
                  <Td>
                    {d.status === "open" && me.role === "supervisor" && (
                      <span className="flex gap-1">
                        <Button small onClick={() => act(`/v1/consortium/disputes/${d.dispute_id}/resolve`, { outcome: "upheld" })}>Keep listing</Button>
                        <Button small variant="good" onClick={() => act(`/v1/consortium/disputes/${d.dispute_id}/resolve`, { outcome: "withdrawn" })}>Withdraw</Button>
                      </span>
                    )}
                  </Td>
                </tr>
              ))}
            </Table>
          </div>
        ) : (
          <p className="mt-3 text-sm text-fg-3">No disputes yet.</p>
        )}
      </Card>
      <Card title="Hub audit chain" hint={`Hash-chained, newest first. Head ${o.audit.head.slice(0, 16)}…. Counts and listing ids only, never a number or handset.`} flush>
        <Async state={entries}>
          {(rows) => (
            <Table head={["#", "When", "Event", "Detail", "Hash"]}>
              {rows.map((e) => (
                <tr key={e.n}>
                  <Td right>{e.n}</Td>
                  <Td>{when(e.at)}</Td>
                  <Td className="font-mono text-xs">{e.event}</Td>
                  <Td className="max-w-md truncate text-xs text-fg-3" title={JSON.stringify(e.detail)}>{JSON.stringify(e.detail)}</Td>
                  <Td className="font-mono text-xs text-fg-4">{e.hash.slice(0, 12)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Card>
    </>
  );
}
