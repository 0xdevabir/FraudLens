"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, getToken, setToken, useApi } from "@/lib/api";
import { idKind, maskId, words } from "@/lib/format";
import type { Me, ModelInfo, Role } from "@/lib/types";

import { Badge, cx, ErrorNote, Loading } from "./ui";

interface Session {
  me: Me;
  /** analysts and supervisors work alerts and cases */
  canReview: boolean;
  /** supervisors are the second person on a freeze */
  canApprove: boolean;
  /** supervisors and admins read the audit log */
  canAudit: boolean;
  revealed: ReadonlySet<string>;
  reveal: (id: string, caseId?: number | null) => Promise<void>;
  signOut: () => void;
}

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside the console");
  return session;
}

const NAV: { heading: string; items: { href: string; label: string; roles: Role[] }[] }[] = [
  {
    heading: "Business",
    items: [
      { href: "/", label: "Executive summary", roles: ["analyst", "supervisor", "admin"] },
      { href: "/impact", label: "Impact simulator", roles: ["analyst", "supervisor", "admin"] },
    ],
  },
  {
    heading: "Investigate",
    items: [
      { href: "/alerts", label: "Alert queue", roles: ["analyst", "supervisor"] },
      { href: "/cases", label: "Cases", roles: ["analyst", "supervisor"] },
      { href: "/approvals", label: "Freeze approvals", roles: ["analyst", "supervisor"] },
    ],
  },
  {
    heading: "Network",
    items: [
      { href: "/network", label: "Network explorer", roles: ["analyst", "supervisor"] },
      { href: "/rings", label: "Mule rings", roles: ["analyst", "supervisor"] },
      { href: "/agents", label: "Agent risk", roles: ["analyst", "supervisor"] },
    ],
  },
  {
    heading: "Model and policy",
    items: [
      { href: "/model", label: "Model monitoring", roles: ["analyst", "supervisor", "admin"] },
      { href: "/fairness", label: "Fairness report", roles: ["analyst", "supervisor", "admin"] },
      { href: "/policy", label: "Decision policy", roles: ["analyst", "supervisor", "admin"] },
      { href: "/audit", label: "Audit log", roles: ["supervisor", "admin"] },
    ],
  },
  {
    heading: "Customer side",
    items: [{ href: "/phone", label: "Customer phone demo", roles: ["analyst", "supervisor", "admin"] }],
  },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/network") return pathname === "/network" || pathname.startsWith("/wallets");
  if (href === "/alerts") return pathname.startsWith("/alerts") || pathname.startsWith("/decisions");
  return pathname === href || pathname.startsWith(`${href}/`);
}

function ServingNote() {
  const model = useApi<ModelInfo>("/v1/model", 30_000);
  if (!model.data) return null;
  const fallback = model.data.mode !== "model";
  return (
    <div className={cx("mx-3 mb-3 rounded-md px-3 py-2 text-xs", fallback ? "bg-amber-400/20 text-amber-200" : "bg-white/5 text-slate-300")}>
      {fallback ? (
        <>Rules-only fallback: the model is unavailable, so decisions come from rules alone.</>
      ) : (
        <>Model {model.data.model.version} · policy {model.data.policy.version}</>
      )}
    </div>
  );
}

/** Signs the visitor in or sends them to the login page, then draws the console around `children`. */
export function Console({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [revealed, setRevealed] = useState<ReadonlySet<string>>(new Set());
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace(`/login?next=${encodeURIComponent(window.location.pathname)}`);
      return;
    }
    api<Me>("/v1/auth/me").then(setMe, setError);
  }, [router]);

  const reveal = useCallback(async (id: string, caseId?: number | null) => {
    // The server writes the audit row first; only then is the identifier shown.
    await api("/v1/audit/reveals", { object_type: idKind(id), object_id: id, ...(caseId ? { case_id: caseId } : {}) });
    setRevealed((old) => new Set(old).add(id));
  }, []);

  const signOut = useCallback(() => {
    setToken(null);
    // A full page load, on purpose: it drops revealed identifiers and every cached response from memory.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }, []);

  const session = useMemo<Session | null>(() => {
    if (!me) return null;
    const canReview = me.role === "analyst" || me.role === "supervisor";
    return {
      me,
      canReview,
      canApprove: me.role === "supervisor",
      canAudit: me.role === "supervisor" || me.role === "admin",
      revealed,
      reveal,
      signOut,
    };
  }, [me, revealed, reveal, signOut]);

  if (error) return <div className="mx-auto max-w-md p-8"><ErrorNote error={error} retry={() => window.location.reload()} /></div>;
  if (!session) return <Loading label="Signing in…" />;

  const allowed = NAV.flatMap((group) => group.items).find((item) => isActive(pathname, item.href));
  const denied = allowed && !allowed.roles.includes(session.me.role);

  return (
    <SessionContext.Provider value={session}>
      <div className="flex min-h-screen">
        <aside
          className={cx(
            "fixed inset-y-0 left-0 z-20 flex w-60 flex-col bg-slate-900 text-slate-200 transition-transform lg:sticky lg:top-0 lg:h-screen lg:shrink-0 lg:translate-x-0",
            menu ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <div className="px-5 py-4">
            <div className="text-base font-semibold tracking-tight text-white">FraudLens</div>
            <div className="text-xs text-slate-400">Real-time fraud decisions for mobile money</div>
          </div>
          <nav aria-label="Console" className="flex-1 overflow-y-auto px-3 pb-4">
            {NAV.map((group) => {
              const items = group.items.filter((item) => item.roles.includes(session.me.role));
              if (!items.length) return null;
              return (
                <div key={group.heading} className="mt-4">
                  <div className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{group.heading}</div>
                  {items.map((item) => (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setMenu(false)}
                      aria-current={isActive(pathname, item.href) ? "page" : undefined}
                      className={cx(
                        "block rounded-md px-2 py-1.5 text-sm",
                        isActive(pathname, item.href) ? "bg-white/10 font-medium text-white" : "text-slate-300 hover:bg-white/5",
                      )}
                    >
                      {item.label}
                    </Link>
                  ))}
                </div>
              );
            })}
          </nav>
          <ServingNote />
          <div className="border-t border-white/10 px-4 py-3 text-sm">
            <div className="truncate font-medium text-white">{session.me.display_name}</div>
            <div className="mt-1 flex items-center justify-between">
              <Badge tone="slate">{words(session.me.role)}</Badge>
              <button type="button" onClick={signOut} className="text-xs text-slate-300 underline-offset-2 hover:underline">
                Sign out
              </button>
            </div>
          </div>
        </aside>
        {menu && <button type="button" aria-label="Close menu" className="fixed inset-0 z-10 bg-slate-900/40 lg:hidden" onClick={() => setMenu(false)} />}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-2 lg:hidden">
            <button type="button" onClick={() => setMenu(true)} className="rounded border border-slate-300 px-2 py-1 text-sm">Menu</button>
            <span className="text-sm font-semibold">FraudLens</span>
          </div>
          <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8">
            {denied ? (
              <ErrorNote error={`This page is not available to the ${session.me.role} role.`} />
            ) : (
              children
            )}
          </main>
        </div>
      </div>
    </SessionContext.Provider>
  );
}

/**
 * A wallet, agent or handset id. Masked until the reviewer asks to see it, and asking is audited.
 * `caseId` ties the reveal to the investigation it was needed for.
 */
export function Id({ value, link = true, caseId }: { value: string | null | undefined; link?: boolean; caseId?: number | null }) {
  const { revealed, reveal, canReview } = useSession();
  const [failed, setFailed] = useState<string | null>(null);
  if (!value) return <span className="text-slate-400">–</span>;
  const shown = revealed.has(value);
  const kind = idKind(value);
  const text = <span className="font-mono text-[0.92em]">{shown ? value : maskId(value)}</span>;
  const href = kind === "wallet" ? `/wallets/${value}` : kind === "agent" ? `/agents/${value}` : null;
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap">
      {link && href && canReview ? (
        <Link href={href} className="text-sky-700 underline-offset-2 hover:underline">{text}</Link>
      ) : (
        text
      )}
      {!shown && canReview && (
        <button
          type="button"
          aria-label={`Reveal ${kind} id ${maskId(value)}`}
          title={failed ?? "Reveal the full id. This is recorded in the audit log."}
          onClick={() => reveal(value, caseId).catch((problem: Error) => setFailed(problem.message))}
          className={cx("rounded p-0.5 hover:bg-slate-100", failed ? "text-red-500" : "text-slate-400 hover:text-slate-700")}
        >
          <svg viewBox="0 0 20 20" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
            <path d="M1.5 10S4.5 4 10 4s8.5 6 8.5 6-3 6-8.5 6-8.5-6-8.5-6Z" />
            <circle cx="10" cy="10" r="2.5" />
          </svg>
        </button>
      )}
    </span>
  );
}
