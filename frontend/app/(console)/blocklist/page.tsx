"use client";

import { useState } from "react";

import { Id, useSession } from "@/components/session";
import { Async, Badge, Button, Card, Chip, Empty, ErrorNote, inputClass, Modal, PageHeader, ReasonDialog, StatusBadge, Table, Td } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { num, when, words } from "@/lib/format";
import type { BlocklistEntry } from "@/lib/types";

const KINDS = ["wallet", "phone", "url"] as const;
const PAGE = 50;

function AddDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [kind, setKind] = useState<(typeof KINDS)[number]>("wallet");
  const [value, setValue] = useState("");
  const [days, setDays] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const ready = value.trim().length > 0 && reason.trim().length >= 10 && !busy;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      await api("/v1/blocklist", { kind, value: value.trim(), reason: reason.trim(), ...(days ? { expires_in_days: Number(days) } : {}) });
      onDone();
      onClose();
    } catch (problem) {
      setError(problem as Error);
      setBusy(false);
    }
  }

  return (
    <Modal title="Add to the blocklist" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-fg-2">
          A listed wallet makes payments to it ask the sender to verify and wait. It never blocks money by itself. A listed phone number or domain makes the message check call a message high risk.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {KINDS.map((k) => <Chip key={k} on={kind === k} onClick={() => setKind(k)}>{words(k)}</Chip>)}
        </div>
        <label className="block text-xs font-medium text-fg-2">
          {kind === "wallet" ? "Wallet number" : kind === "phone" ? "Phone number" : "Domain or link"}
          <input autoFocus className={`${inputClass} mt-1 block w-full`} maxLength={255} value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        <label className="block text-xs font-medium text-fg-2">
          Expires after (days, optional)
          <input className={`${inputClass} mt-1 block w-32`} inputMode="numeric" pattern="[0-9]*" maxLength={4} value={days} onChange={(e) => setDays(e.target.value.replace(/\D/g, ""))} />
        </label>
        <label className="block text-xs font-medium text-fg-2">
          Reason
          <textarea rows={3} maxLength={2000} className={`${inputClass} mt-1 block w-full`} placeholder="At least 10 characters. Recorded in the audit log." value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        {error && <ErrorNote error={error} />}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!ready}>{busy ? "Working…" : "Add"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<{ added: number; skipped: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const entries = text
    .split("\n")
    .map((line) => line.split(",").map((part) => part.trim()))
    .filter(([kind, value]) => value && (KINDS as readonly string[]).includes(kind))
    .map(([kind, value]) => ({ kind, value }));
  const ready = entries.length > 0 && entries.length <= 500 && reason.trim().length >= 10 && !busy;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api("/v1/blocklist/import", { reason: reason.trim(), entries }));
      onDone();
    } catch (problem) {
      setError(problem as Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Import entries" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-fg-2">One entry per line as <code>kind,value</code>, for example <code>phone,01712345678</code>. Up to 500. Invalid or already listed values are skipped.</p>
        <textarea rows={6} aria-label="Entries" className={`${inputClass} w-full font-mono text-xs`} value={text} onChange={(e) => setText(e.target.value)} />
        <p className="text-xs text-fg-3">{entries.length} valid line{entries.length === 1 ? "" : "s"} found</p>
        <label className="block text-xs font-medium text-fg-2">
          Reason (the same for all)
          <textarea rows={2} maxLength={2000} className={`${inputClass} mt-1 block w-full`} value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        {result && <p className="text-sm text-good">Added {result.added}, skipped {result.skipped}.</p>}
        {error && <ErrorNote error={error} />}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{result ? "Close" : "Cancel"}</Button>
          {!result && <Button type="submit" variant="primary" disabled={!ready}>{busy ? "Working…" : "Import"}</Button>}
        </div>
      </form>
    </Modal>
  );
}

export default function BlocklistPage() {
  const { canApprove } = useSession();
  const [kind, setKind] = useState<"" | (typeof KINDS)[number]>("");
  const [state, setState] = useState<"active" | "removed" | "all">("active");
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [page, setPage] = useState(0);
  const [dialog, setDialog] = useState<"add" | "import" | BlocklistEntry | null>(null);
  const list = useApi<{ total: number; entries: BlocklistEntry[] }>(`/v1/blocklist${qs({ kind, state, q: applied, limit: PAGE, offset: page * PAGE })}`);
  const total = list.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <>
      <PageHeader
        title="Blocklist"
        sub="Wallets, phone numbers and domains known to be used for fraud. A listed wallet asks the sender to verify and wait; nothing here blocks money by itself."
        actions={
          canApprove && (
            <>
              <Button small onClick={() => setDialog("import")}>Import</Button>
              <Button small variant="primary" onClick={() => setDialog("add")}>Add entry</Button>
            </>
          )
        }
      />
      <Card flush>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-4 py-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-xs font-medium text-fg-3">Type</span>
            <Chip on={kind === ""} onClick={() => { setKind(""); setPage(0); }}>All</Chip>
            {KINDS.map((k) => <Chip key={k} on={kind === k} onClick={() => { setKind(k); setPage(0); }}>{words(k)}</Chip>)}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-xs font-medium text-fg-3">Show</span>
            {(["active", "removed", "all"] as const).map((s) => <Chip key={s} on={state === s} onClick={() => { setState(s); setPage(0); }}>{words(s)}</Chip>)}
          </div>
          <form className="flex items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); setApplied(q.trim()); setPage(0); }}>
            <input aria-label="Search by value" placeholder="Starts with…" value={q} maxLength={64} pattern="[A-Za-z0-9_.@\-]*" onChange={(e) => setQ(e.target.value)} className={`${inputClass} w-44 py-1 text-xs`} />
            <Button small type="submit">Search</Button>
          </form>
          <span className="ml-auto text-xs text-fg-3">{list.data ? `${num(total)} entries` : ""}</span>
        </div>
        <Async state={list}>
          {(data) =>
            data.entries.length ? (
              <Table head={["Type", "Value", "State", "Source", "Reason", "Added", "Expires", ""]}>
                {data.entries.map((e) => (
                  <tr key={e.id}>
                    <Td>{words(e.kind)}</Td>
                    <Td className="font-mono text-xs">{e.kind === "wallet" ? <Id value={e.value} caseId={e.case_id} /> : e.value}</Td>
                    <Td><StatusBadge status={e.state} /></Td>
                    <Td>
                      {e.source === "verdict" && e.case_id ? <Badge tone="blue" title={`Listed by the verdict on case #${e.case_id}`}>verdict · #{e.case_id}</Badge> : <Badge>{e.source}</Badge>}
                    </Td>
                    <Td className="max-w-xs whitespace-normal text-fg-2">{e.reason}{e.remove_reason ? <span className="block text-xs text-fg-3">Removed: {e.remove_reason}</span> : null}</Td>
                    <Td className="whitespace-nowrap text-fg-2">{when(e.created_at)} · {e.created_by}</Td>
                    <Td className="whitespace-nowrap text-fg-2">{e.expires_at ? when(e.expires_at) : "never"}</Td>
                    <Td>{canApprove && e.state !== "removed" && <Button small onClick={() => setDialog(e)}>Remove</Button>}</Td>
                  </tr>
                ))}
              </Table>
            ) : (
              <Empty>Nothing listed.</Empty>
            )
          }
        </Async>
        <div className="flex items-center justify-between border-t border-line px-4 py-2 text-xs text-fg-3">
          <span>Page {page + 1} of {num(pages)}</span>
          <span className="flex gap-2">
            <Button small disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
            <Button small disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</Button>
          </span>
        </div>
      </Card>
      {dialog === "add" && <AddDialog onClose={() => setDialog(null)} onDone={list.reload} />}
      {dialog === "import" && <ImportDialog onClose={() => setDialog(null)} onDone={list.reload} />}
      {dialog && typeof dialog === "object" && (
        <ReasonDialog
          title={`Remove ${dialog.kind} from the blocklist`}
          intro="The entry stops applying at once and stays in the history, with your reason."
          confirm="Remove"
          variant="danger"
          onClose={() => setDialog(null)}
          onSubmit={async (reason) => {
            await api(`/v1/blocklist/${dialog.id}/remove`, { reason });
            list.reload();
          }}
        />
      )}
    </>
  );
}
