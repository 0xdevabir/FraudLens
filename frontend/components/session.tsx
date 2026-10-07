"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, getToken, setToken, useApi } from "@/lib/api";
import { idKind, maskId, words } from "@/lib/format";
import { isActive, NAV, NAV_ITEMS, slug } from "@/lib/nav";
import type { Me, ModelInfo } from "@/lib/types";

import { TOUR_STEPS } from "./tour/steps";
import { Glyph, MoreSheet, TabBar, TopBar } from "./mobile-nav";
import { TourButton, TourProvider } from "./tour/tour";
import { Brand } from "./brand";
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

function ServingNote() {
  const model = useApi<ModelInfo>("/v1/model", 30_000);
  if (!model.data) return null;
  const fallback = model.data.mode !== "model";
  return (
    <div data-tour="serving" className={cx("mx-3 mb-3 rounded-xl px-3 py-2 text-xs", fallback ? "bg-warn/15 text-warn" : "bg-white/6 text-fg-3")}>
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
  const [more, setMore] = useState(false);

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

  const signOut = useCallback(async () => {
    // Revoke the token on the server first; if that fails, still sign out here.
    await api("/v1/auth/logout", {}).catch(() => undefined);
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

  const allowed = NAV_ITEMS.find((item) => isActive(pathname, item.href));
  const denied = allowed && !allowed.roles.includes(session.me.role);

  return (
    <SessionContext.Provider value={session}>
      <TourProvider steps={TOUR_STEPS} role={session.me.role}>
      <div className="flex min-h-dvh">
        {/* A sidebar from a laptop up; on a phone or a tablet, the title bar and the tab bar below. */}
        <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-r border-line bg-base/80 text-fg-2 backdrop-blur-2xl backdrop-saturate-150 lg:flex">
          <div data-tour="brand" className="px-5 pt-6 pb-2">
            <Brand size={28} className="text-lg" />
            <div className="mt-1.5 text-xs text-fg-4">Real-time fraud decisions for mobile money</div>
          </div>
          <div data-tour="tour-button" className="px-3 pt-2">
            <TourButton />
          </div>
          <nav aria-label="Console" className="flex-1 overflow-y-auto px-3 pb-4">
            {NAV.map((group) => {
              const items = group.items.filter((item) => item.roles.includes(session.me.role));
              if (!items.length) return null;
              return (
                <div key={group.heading} data-tour={`nav-group-${slug(group.heading)}`} className="mt-4">
                  <div className="px-3 pb-1.5 text-xs font-medium text-fg-4">{group.heading}</div>
                  {items.map((item) => (
                    <Link
                      key={item.href}
                      href={item.href}
                      data-tour={`nav-${item.href === "/" ? "home" : slug(item.href)}`}
                      aria-current={isActive(pathname, item.href) ? "page" : undefined}
                      className={cx(
                        "flex items-center gap-2.5 rounded-xl px-3 py-1.5 text-[0.9375rem] active:scale-[0.98]",
                        isActive(pathname, item.href) ? "bg-accent/15 font-medium text-accent" : "text-fg-2 hover:bg-white/6",
                      )}
                    >
                      <Glyph d={item.icon} className="size-[1.125rem] shrink-0 opacity-80" />
                      {item.label}
                    </Link>
                  ))}
                </div>
              );
            })}
          </nav>
          <ServingNote />
          <div data-tour="user" className="border-t border-line px-5 py-4 text-sm">
            <div className="truncate font-medium text-fg">{session.me.display_name}</div>
            <div className="mt-1.5 flex items-center justify-between">
              <Badge tone="slate">{words(session.me.role)}</Badge>
              <button type="button" onClick={signOut} className="text-xs text-info underline-offset-2 hover:underline">
                Sign out
              </button>
            </div>
          </div>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar pathname={pathname} />
          {/* Keyed by route, so each page arrives with the same short rise. */}
          <main
            key={pathname}
            className="min-w-0 flex-1 animate-rise px-4 pt-5 pb-[calc(5.5rem+env(safe-area-inset-bottom))] sm:px-6 lg:px-10 lg:py-8"
          >
            {denied ? (
              <ErrorNote error={`This page is not available to the ${session.me.role} role.`} />
            ) : (
              children
            )}
          </main>
        </div>
      </div>
      <TabBar me={session.me} pathname={pathname} moreOpen={more} onMore={() => setMore(true)} />
      {more && <MoreSheet me={session.me} pathname={pathname} serving={<ServingNote />} onSignOut={signOut} onClose={() => setMore(false)} />}
      </TourProvider>
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
  if (!value) return <span className="text-fg-4">–</span>;
  const shown = revealed.has(value);
  const kind = idKind(value);
  const text = <span className="font-mono text-[0.92em]">{shown ? value : maskId(value)}</span>;
  const href = kind === "wallet" ? `/wallets/${value}` : kind === "agent" ? `/agents/${value}` : null;
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap">
      {link && href && canReview ? (
        <Link href={href} className="text-info underline-offset-2 hover:underline">{text}</Link>
      ) : (
        text
      )}
      {!shown && canReview && (
        <button
          type="button"
          aria-label={`Reveal ${kind} id ${maskId(value)}`}
          title={failed ?? "Reveal the full id. This is recorded in the audit log."}
          onClick={() => reveal(value, caseId).catch((problem: Error) => setFailed(problem.message))}
          className={cx("rounded-md p-0.5 hover:bg-white/10", failed ? "text-bad" : "text-fg-4 hover:text-fg")}
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


