"use client";

import { useState } from "react";

import { Legend, StackedBars } from "@/components/charts";
import { Async, Badge, Button, Card, Chip, Empty, ErrorNote, inputClass, Modal, PageHeader, Table, Td } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { num, shortDay, when } from "@/lib/format";
import type { ApiKeyRow } from "@/lib/types";

const SCOPES = [
  ["score", "Score payments (/v1/score)"],
  ["events", "Send events (/v1/events)"],
] as const;

function CreateDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["score"]);
  const [sandbox, setSandbox] = useState(true);
  const [rate, setRate] = useState("600");
  const [quota, setQuota] = useState("");
  const [created, setCreated] = useState<ApiKeyRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const ready = name.trim().length >= 2 && scopes.length > 0 && Number(rate) >= 1 && !busy;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      setCreated(await api<ApiKeyRow>("/v1/api-keys", { name: name.trim(), scopes, sandbox, rate_per_minute: Number(rate), ...(quota ? { daily_quota: Number(quota) } : {}) }));
      onDone();
    } catch (problem) {
      setError(problem as Error);
    } finally {
      setBusy(false);
    }
  }

  if (created) {
    return (
      <Modal title="Key created" onClose={onClose}>
        <div className="space-y-3 text-sm">
          <p className="text-fg-2">This is the only time the key is shown. Send it in the <code>X-API-Key</code> header. Only a hash is kept here.</p>
          <code className="block break-all rounded-xl bg-white/8 p-3 text-xs text-fg select-all">{created.key}</code>
          <div className="flex justify-end"><Button variant="primary" onClick={onClose}>I have stored it</Button></div>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title="Make a partner API key" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-xs font-medium text-fg-2">
          Partner or system
          <input autoFocus maxLength={80} className={`${inputClass} mt-1 block w-full`} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <div>
          <div className="mb-1 text-xs font-medium text-fg-2">What it may call</div>
          <div className="flex flex-wrap gap-1.5">
            {SCOPES.map(([id, label]) => (
              <Chip key={id} on={scopes.includes(id)} onClick={() => setScopes(scopes.includes(id) ? scopes.filter((s) => s !== id) : [...scopes, id])}>{label}</Chip>
            ))}
          </div>
        </div>
        <label className="flex items-start gap-2 text-sm text-fg-2">
          <input type="checkbox" className="mt-1" checked={sandbox} onChange={(e) => setSandbox(e.target.checked)} />
          <span>Sandbox key: canned decisions by the cents of the amount (.01 allow, .02 warn, .03 step-up, .04 hold). Nothing real is stored or sent.</span>
        </label>
        <div className="flex flex-wrap gap-3">
          <label className="block text-xs font-medium text-fg-2">
            Requests per minute
            <input inputMode="numeric" pattern="[0-9]*" maxLength={6} className={`${inputClass} mt-1 block w-32`} value={rate} onChange={(e) => setRate(e.target.value.replace(/\D/g, ""))} />
          </label>
          <label className="block text-xs font-medium text-fg-2">
            Daily quota (optional)
            <input inputMode="numeric" pattern="[0-9]*" maxLength={9} className={`${inputClass} mt-1 block w-36`} value={quota} onChange={(e) => setQuota(e.target.value.replace(/\D/g, ""))} />
          </label>
        </div>
        {error && <ErrorNote error={error} />}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!ready}>{busy ? "Working…" : "Make key"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function RevokeDialog({ row, onClose, onDone }: { row: ApiKeyRow; onClose: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  async function revoke() {
    setBusy(true);
    setError(null);
    try {
      await api(`/v1/api-keys/${row.id}/revoke`, {});
      onDone();
      onClose();
    } catch (problem) {
      setError(problem as Error);
      setBusy(false);
    }
  }
  return (
    <Modal title={`Revoke ${row.name}`} onClose={onClose}>
      <div className="space-y-3 text-sm">
        <p className="text-fg-2">The key stops working at once. This cannot be undone: make a new key to replace it.</p>
        {error && <ErrorNote error={error} />}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" onClick={revoke} disabled={busy}>{busy ? "Working…" : "Revoke"}</Button>
        </div>
      </div>
    </Modal>
  );
}

function Usage({ id }: { id: number }) {
  const usage = useApi<{ days: { day: string; requests: number; errors: number }[] }>(`/v1/api-keys/${id}/usage?days=14`);
  return (
    <Async state={usage}>
      {(data) => (
        <div className="space-y-2 p-4">
          <Legend items={[{ name: "Requests", color: "var(--color-accent)" }, { name: "Errors and refusals", color: "var(--color-bad)" }]} />
          <StackedBars
            labels={data.days.map((d) => shortDay(d.day))}
            series={[
              { name: "Requests", color: "var(--color-accent)", values: data.days.map((d) => d.requests) },
              { name: "Errors and refusals", color: "var(--color-bad)", values: data.days.map((d) => d.errors) },
            ]}
          />
        </div>
      )}
    </Async>
  );
}

export default function ApiKeysPage() {
  const keys = useApi<ApiKeyRow[]>("/v1/api-keys", 30_000);
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<ApiKeyRow | null>(null);
  const [watching, setWatching] = useState<ApiKeyRow | null>(null);

  return (
    <>
      <PageHeader
        title="Partner API keys"
        sub="Let a wallet app call the scoring endpoints with its own key: limited per minute and per day, counted, and revocable. Sandbox keys give canned answers and touch nothing."
        actions={<Button small variant="primary" onClick={() => setCreating(true)}>Make a key</Button>}
      />
      <Card flush>
        <Async state={keys}>
          {(rows) =>
            rows.length ? (
              <Table head={["Name", "Key", "Scopes", "Limits", "Today", "Last used", "State", ""]}>
                {rows.map((k) => (
                  <tr key={k.id}>
                    <Td className="font-medium">{k.name}{k.sandbox && <Badge tone="blue"> sandbox</Badge>}</Td>
                    <Td className="font-mono text-xs">{k.prefix}_…</Td>
                    <Td className="text-xs text-fg-2">{k.scopes.join(", ")}</Td>
                    <Td className="whitespace-nowrap text-xs text-fg-2">{num(k.rate_per_minute)}/min{k.daily_quota ? ` · ${num(k.daily_quota)}/day` : ""}</Td>
                    <Td right>{num(k.requests_today ?? 0)}</Td>
                    <Td className="whitespace-nowrap text-fg-2">{k.last_used_at ? when(k.last_used_at) : "never"}</Td>
                    <Td>{k.revoked_at ? <Badge tone="slate">revoked</Badge> : <Badge tone="green">active</Badge>}</Td>
                    <Td>
                      <span className="flex gap-1.5">
                        <Button small onClick={() => setWatching(watching?.id === k.id ? null : k)}>Usage</Button>
                        {!k.revoked_at && <Button small variant="danger" onClick={() => setRevoking(k)}>Revoke</Button>}
                      </span>
                    </Td>
                  </tr>
                ))}
              </Table>
            ) : (
              <Empty>No keys yet. The service account&apos;s token still works for the payment switch.</Empty>
            )
          }
        </Async>
      </Card>
      {watching && (
        <div className="mt-4">
          <Card title={`Last 14 days: ${watching.name}`} hint="Requests that went through, and the ones that failed or were refused by a limit."><Usage id={watching.id} /></Card>
        </div>
      )}
      {creating && <CreateDialog onClose={() => setCreating(false)} onDone={keys.reload} />}
      {revoking && <RevokeDialog row={revoking} onClose={() => setRevoking(null)} onDone={keys.reload} />}
    </>
  );
}
