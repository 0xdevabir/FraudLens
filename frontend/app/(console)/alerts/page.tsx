"use client";

import { useEffect, useRef, useState } from "react";

import { AlertTable } from "@/components/tables";
import { Async, Badge, Button, Card, Chip, PageHeader } from "@/components/ui";
import { API_URL, authHeaders, qs, useApi } from "@/lib/api";
import { num, TIER_LABEL, words } from "@/lib/format";
import type { Alert, Tier } from "@/lib/types";

const TIERS: Tier[] = ["hold", "step_up", "warn"];
const STATUSES = ["held", "pending_customer", "blocked", "cancelled", "completed", "rejected"];
const PAGE = 50;

/**
 * The alert stream is server-sent events behind a bearer token, which EventSource cannot send,
 * so it is read with fetch. An event is only a signal to refetch: the queue itself always comes
 * from the API, in the API's order.
 */
function useAlertStream(onAlert: () => void): boolean {
  const [live, setLive] = useState(false);
  const callback = useRef(onAlert);
  useEffect(() => {
    callback.current = onAlert;
  });
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    (async () => {
      while (!stopped) {
        try {
          const response = await fetch(`${API_URL}/v1/stream/alerts`, { headers: authHeaders(), signal: controller.signal });
          if (response.status === 401 || response.status === 403) return;
          if (!response.ok || !response.body) throw new Error("stream unavailable");
          setLive(true);
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const events = buffer.split("\n\n");
            buffer = events.pop() ?? "";
            if (events.some((event) => event.includes("event: alert"))) callback.current();
          }
        } catch {
          // dropped or aborted: fall through and retry unless the page is gone
        }
        setLive(false);
        if (!stopped) await new Promise((resolve) => setTimeout(resolve, 3000));
      }
    })();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, []);
  return live;
}

export default function AlertsPage() {
  const [tiers, setTiers] = useState<Tier[]>([]);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [page, setPage] = useState(0);
  const [fresh, setFresh] = useState(0);
  const alerts = useApi<{ total: number; alerts: Alert[] }>(
    `/v1/alerts${qs({ tier: tiers, status: statuses, limit: PAGE, offset: page * PAGE })}`,
  );
  const live = useAlertStream(() => {
    setFresh((n) => n + 1);
    if (page === 0) alerts.reload();
  });

  function toggle<T>(list: T[], value: T): T[] {
    return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
  }
  const total = alerts.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <>
      <PageHeader
        title="Alert queue"
        sub="Every payment the decision engine interrupted, newest first. Open one to see why it was flagged and what the customer was told."
        actions={
          <>
            {fresh > 0 && <Badge tone="blue">{fresh} new since you opened this page</Badge>}
            <Badge tone={live ? "green" : "slate"} title="Server-sent events from the scoring service">{live ? "● Live" : "○ Reconnecting"}</Badge>
          </>
        }
      />
      <Card flush>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-slate-100 px-4 py-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-xs font-medium text-slate-500">Tier</span>
            {TIERS.map((tier) => (
              <Chip key={tier} on={tiers.includes(tier)} onClick={() => { setTiers(toggle(tiers, tier)); setPage(0); }}>
                {TIER_LABEL[tier]}
              </Chip>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-xs font-medium text-slate-500">Outcome</span>
            {STATUSES.map((status) => (
              <Chip key={status} on={statuses.includes(status)} onClick={() => { setStatuses(toggle(statuses, status)); setPage(0); }}>
                {words(status)}
              </Chip>
            ))}
          </div>
          <span className="ml-auto text-xs text-slate-500">{alerts.data ? `${num(total)} alerts` : ""}</span>
        </div>
        <Async state={alerts}>{(data) => <AlertTable alerts={data.alerts} />}</Async>
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
          <span>Page {page + 1} of {num(pages)}</span>
          <span className="flex gap-2">
            <Button small disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</Button>
            <Button small disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Older</Button>
          </span>
        </div>
      </Card>
    </>
  );
}
