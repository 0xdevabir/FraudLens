"use client";

import { useState } from "react";

import { Async, Badge, Button, Card, Chip, Empty, ErrorNote, inputClass, Modal, PageHeader, StatusBadge, Table, Td } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { num, when, words } from "@/lib/format";
import type { DeliveryRow, WebhookEndpoint } from "@/lib/types";

function CreateDialog({ events, onClose, onDone }: { events: string[]; onClose: () => void; onDone: () => void }) {
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [chosen, setChosen] = useState<string[]>(["decision.hold", "case.verdict"]);
  const [created, setCreated] = useState<WebhookEndpoint | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const ready = url.trim().length >= 8 && chosen.length > 0 && !busy;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      setCreated(await api<WebhookEndpoint>("/v1/webhooks", { url: url.trim(), description: description.trim(), events: chosen }));
      onDone();
    } catch (problem) {
      setError(problem as Error);
    } finally {
      setBusy(false);
    }
  }

  if (created) {
    return (
      <Modal title="Endpoint created" onClose={onClose}>
        <div className="space-y-3 text-sm">
          <p className="text-fg-2">This is the only time the signing secret is shown. Store it where the receiver can read it, and use it to check the <code>X-FraudLens-Signature</code> header on every delivery.</p>
          <code className="block break-all rounded-xl bg-white/8 p-3 text-xs text-fg select-all">{created.secret}</code>
          <div className="flex justify-end"><Button variant="primary" onClick={onClose}>I have stored it</Button></div>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title="Add a webhook endpoint" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-fg-2">Deliveries carry identifiers only, are signed, and are retried with the same event id. The address must be public https.</p>
        <label className="block text-xs font-medium text-fg-2">
          URL
          <input autoFocus type="url" maxLength={500} placeholder="https://" className={`${inputClass} mt-1 block w-full`} value={url} onChange={(e) => setUrl(e.target.value)} />
        </label>
        <label className="block text-xs font-medium text-fg-2">
          Description (optional)
          <input maxLength={200} className={`${inputClass} mt-1 block w-full`} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <div>
          <div className="mb-1 text-xs font-medium text-fg-2">Events</div>
          <div className="flex flex-wrap gap-1.5">
            {events.map((name) => (
              <Chip key={name} on={chosen.includes(name)} onClick={() => setChosen(chosen.includes(name) ? chosen.filter((c) => c !== name) : [...chosen, name])}>{name}</Chip>
            ))}
          </div>
        </div>
        {error && <ErrorNote error={error} />}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!ready}>{busy ? "Working…" : "Create"}</Button>
        </div>
      </form>
    </Modal>
  );
}

export default function WebhooksPage() {
  const endpoints = useApi<WebhookEndpoint[]>("/v1/webhooks", 20_000);
  const eventTypes = useApi<{ events: string[] }>("/v1/webhooks/events");
  const [status, setStatus] = useState<"" | "pending" | "delivered" | "dead">("");
  const [kind, setKind] = useState<"" | "webhook" | "sms">("");
  const [page, setPage] = useState(0);
  const deliveries = useApi<{ total: number; deliveries: DeliveryRow[] }>(`/v1/webhooks/deliveries${qs({ status, kind, limit: 25, offset: page * 25 })}`, 20_000);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<WebhookEndpoint | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);

  async function act<T>(run: () => Promise<T>, then?: (result: T) => void) {
    setError(null);
    setNote(null);
    try {
      const result = await run();
      then?.(result);
      endpoints.reload();
      deliveries.reload();
    } catch (problem) {
      setError(problem as Error);
    }
  }

  const total = deliveries.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / 25));
  return (
    <>
      <PageHeader tour="webhooks-header"
        title="Webhooks"
        sub="Tell other systems when a payment is warned, stepped up or held, when a case gets a verdict, and when a wallet is frozen. Customers are texted the fixed policy wording when SMS is configured."
        actions={<Button small variant="primary" onClick={() => setCreating(true)}>Add endpoint</Button>}
      />
      {error && <div className="mb-3"><ErrorNote error={error} /></div>}
      {note && <p className="mb-3 text-sm text-fg-2">{note}</p>}
      <Card tour="webhooks-endpoints" title="Endpoints" hint="An endpoint that fails 30 times in a row is switched off." flush>
        <Async state={endpoints}>
          {(rows) =>
            rows.length ? (
              <Table head={["Endpoint", "Events", "State", "Waiting", "Dead", "Failing", ""]}>
                {rows.map((e) => (
                  <tr key={e.id}>
                    <Td className="max-w-xs whitespace-normal break-all"><div className="font-mono text-xs">{e.url}</div>{e.description && <div className="text-xs text-fg-3">{e.description}</div>}</Td>
                    <Td className="max-w-xs whitespace-normal text-xs text-fg-2">{e.events.join(", ")}</Td>
                    <Td><Badge tone={e.active ? "green" : "slate"}>{e.active ? "active" : "off"}</Badge></Td>
                    <Td right>{num(e.pending ?? 0)}</Td>
                    <Td right>{e.dead ? <Badge tone="red">{e.dead}</Badge> : <span className="text-fg-4">0</span>}</Td>
                    <Td right>{e.consecutive_failures || <span className="text-fg-4">0</span>}</Td>
                    <Td>
                      <span className="flex flex-wrap gap-1.5">
                        <Button small onClick={() => act(() => api<DeliveryRow>(`/v1/webhooks/${e.id}/test`, {}), (d) => setNote(d.status === "delivered" ? `Test delivered (the receiver answered ${d.last_status_code}).` : `Test not delivered: ${d.last_error ?? "no answer"}.`))}>Send test</Button>
                        <Button small onClick={() => act(() => api(`/v1/webhooks/${e.id}/${e.active ? "disable" : "enable"}`, {}))}>{e.active ? "Turn off" : "Turn on"}</Button>
                        <Button small onClick={() => act(() => api<WebhookEndpoint>(`/v1/webhooks/${e.id}/rotate-secret`, {}), setSecret)}>New secret</Button>
                      </span>
                    </Td>
                  </tr>
                ))}
              </Table>
            ) : (
              <Empty>No endpoints yet.</Empty>
            )
          }
        </Async>
      </Card>

      <div className="mt-4">
        <Card title="Delivery log" hint="Every message the platform tried to send. Dead ones ran out of attempts; replay sends the same event again." flush>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-4 py-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-xs font-medium text-fg-3">Status</span>
              {(["", "pending", "delivered", "dead"] as const).map((s) => <Chip key={s || "all"} on={status === s} onClick={() => { setStatus(s); setPage(0); }}>{s ? words(s) : "All"}</Chip>)}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-xs font-medium text-fg-3">Type</span>
              {(["", "webhook", "sms"] as const).map((k) => <Chip key={k || "all"} on={kind === k} onClick={() => { setKind(k); setPage(0); }}>{k ? (k === "sms" ? "SMS" : "Webhook") : "All"}</Chip>)}
            </div>
            <span className="ml-auto text-xs text-fg-3">{deliveries.data ? `${num(total)} deliveries` : ""}</span>
          </div>
          <Async state={deliveries}>
            {(data) =>
              data.deliveries.length ? (
                <Table head={["When", "Event", "To", "Status", "Attempts", "Answer", ""]}>
                  {data.deliveries.map((d) => (
                    <tr key={d.id}>
                      <Td className="whitespace-nowrap text-fg-2">{when(d.created_at)}</Td>
                      <Td className="font-mono text-xs">{d.event_type}</Td>
                      <Td>{d.kind === "sms" ? "Customer SMS" : `Endpoint #${d.endpoint_id}`}</Td>
                      <Td><StatusBadge status={d.status} /></Td>
                      <Td right>{d.attempts}</Td>
                      <Td className="max-w-xs whitespace-normal text-xs text-fg-3">{d.last_error ?? (d.last_status_code ? `HTTP ${d.last_status_code}` : "–")}{d.status === "pending" && d.attempts > 0 ? ` · next ${when(d.next_attempt_at)}` : ""}</Td>
                      <Td>{d.status !== "pending" && <Button small onClick={() => act(() => api(`/v1/webhooks/deliveries/${d.id}/replay`, {}))}>Send again</Button>}</Td>
                    </tr>
                  ))}
                </Table>
              ) : (
                <Empty>Nothing has been sent.</Empty>
              )
            }
          </Async>
          <div className="flex items-center justify-between border-t border-line px-4 py-2 text-xs text-fg-3">
            <span>Page {page + 1} of {num(pages)}</span>
            <span className="flex gap-2">
              <Button small disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</Button>
              <Button small disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Older</Button>
            </span>
          </div>
        </Card>
      </div>

      {creating && eventTypes.data && <CreateDialog events={eventTypes.data.events} onClose={() => setCreating(false)} onDone={endpoints.reload} />}
      {secret && (
        <Modal title="New signing secret" onClose={() => setSecret(null)}>
          <div className="space-y-3 text-sm">
            <p className="text-fg-2">The old secret has stopped working. This is the only time the new one is shown.</p>
            <code className="block break-all rounded-xl bg-white/8 p-3 text-xs text-fg select-all">{secret.secret}</code>
            <div className="flex justify-end"><Button variant="primary" onClick={() => setSecret(null)}>I have stored it</Button></div>
          </div>
        </Modal>
      )}
    </>
  );
}
