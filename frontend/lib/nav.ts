import type { Role } from "./types";

/** One page of the console. `short` is its name on the phone's tab bar. */
export interface NavItem {
  href: string;
  label: string;
  short: string;
  roles: Role[];
  /** An outline glyph on a 24-unit grid, drawn with a 1.8 stroke. */
  icon: string;
}

const ALL: Role[] = ["analyst", "supervisor", "admin"];
const REVIEW: Role[] = ["analyst", "supervisor"];

export const NAV: { heading: string; items: NavItem[] }[] = [
  {
    heading: "Business",
    items: [
      { href: "/", label: "Executive summary", short: "Home", roles: ALL, icon: "M3.5 10.5 12 3.5l8.5 7V20a1 1 0 0 1-1 1H15v-6H9v6H4.5a1 1 0 0 1-1-1z" },
      { href: "/impact", label: "Impact simulator", short: "Impact", roles: ALL, icon: "M4 20h16M6.5 16l4-4.5 3 3L19 8M15.5 8H19v3.5" },
    ],
  },
  {
    heading: "Investigate",
    items: [
      { href: "/alerts", label: "Alert queue", short: "Alerts", roles: REVIEW, icon: "M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 1.5h-15zM10 20.5a2.2 2.2 0 0 0 4 0" },
      { href: "/cases", label: "Cases", short: "Cases", roles: REVIEW, icon: "M3 7.5A2 2 0 0 1 5 5.5h4l2 2h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" },
      { href: "/approvals", label: "Freeze approvals", short: "Approvals", roles: REVIEW, icon: "M7.5 11V8a4.5 4.5 0 0 1 9 0v3M5.5 11h13v9.5h-13zM12 14.5v2.5" },
      { href: "/appeals", label: "Customer appeals", short: "Appeals", roles: REVIEW, icon: "M4 5.5h16v10.5H9.5L5 20v-4H4z" },
      { href: "/blocklist", label: "Blocklist", short: "Blocklist", roles: REVIEW, icon: "M3 12a9 9 0 1 0 18 0 9 9 0 1 0-18 0M5.6 5.6l12.8 12.8" },
      { href: "/refunds", label: "Victim refunds", short: "Refunds", roles: REVIEW, icon: "M9 13.5 4.5 9 9 4.5M4.5 9H14a5.5 5.5 0 0 1 0 11h-3.5" },
    ],
  },
  {
    heading: "Network",
    items: [
      { href: "/network", label: "Network explorer", short: "Network", roles: REVIEW, icon: "M10 5a2 2 0 1 0 4 0 2 2 0 1 0-4 0M3 18.5a2 2 0 1 0 4 0 2 2 0 1 0-4 0M17 18.5a2 2 0 1 0 4 0 2 2 0 1 0-4 0M12 7v4.5M12 11.5l-6 5.5M12 11.5l6 5.5" },
      { href: "/rings", label: "Mule rings", short: "Rings", roles: REVIEW, icon: "M3 12a9 9 0 1 0 18 0 9 9 0 1 0-18 0M8 12a4 4 0 1 0 8 0 4 4 0 1 0-8 0" },
      { href: "/consortium", label: "Mule consortium", short: "Consortium", roles: REVIEW, icon: "M3 12a9 9 0 1 0 18 0 9 9 0 1 0-18 0M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z" },
      { href: "/agents", label: "Agent risk", short: "Agents", roles: REVIEW, icon: "M4 10v10h16V10M3 10l2-5.5h14L21 10zM9.5 20v-5h5v5" },
    ],
  },
  {
    heading: "Model and policy",
    items: [
      { href: "/model", label: "Model monitoring", short: "Model", roles: ALL, icon: "M3 12h4l2.5-6.5 5 13L17 12h4" },
      { href: "/fairness", label: "Fairness report", short: "Fairness", roles: ALL, icon: "M12 4v16M6 20h12M5.5 8h13M5.5 8l-2.5 6a2.5 2.5 0 0 0 5 0zM18.5 8 16 14a2.5 2.5 0 0 0 5 0z" },
      { href: "/policy", label: "Decision policy", short: "Policy", roles: ALL, icon: "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6" },
      { href: "/fraud-types", label: "Fraud types", short: "Fraud types", roles: ALL, icon: "M3.5 12V3.5H12l9 9-8.5 8.5zM8 8h.01" },
      { href: "/audit", label: "Audit log", short: "Audit", roles: ["supervisor", "admin"], icon: "M12 3l7.5 3v6c0 4.6-3.2 7.7-7.5 9-4.3-1.3-7.5-4.4-7.5-9V6zM9 12l2 2 4-4" },
      { href: "/webhooks", label: "Webhooks", short: "Webhooks", roles: ["supervisor", "admin"], icon: "M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" },
      { href: "/api-keys", label: "Partner API keys", short: "API keys", roles: ["supervisor", "admin"], icon: "M8 15a4 4 0 1 0 0-8 4 4 0 1 0 0 8M11.5 11H21M18 11v3.5M15 11v2.5" },
    ],
  },
  {
    heading: "Customer side",
    items: [
      { href: "/phone", label: "Customer phone demo", short: "Phone", roles: ALL, icon: "M8 2.5h8A1.5 1.5 0 0 1 17.5 4v16a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 20V4A1.5 1.5 0 0 1 8 2.5zM10.5 18.5h3" },
    ],
  },
];

export const NAV_ITEMS = NAV.flatMap((group) => group.items);

/** The four pages each role reaches for most; everything else is under More. */
const TABS: Record<Role, string[]> = {
  analyst: ["/", "/alerts", "/cases", "/refunds"],
  supervisor: ["/", "/alerts", "/cases", "/approvals"],
  admin: ["/", "/model", "/policy", "/audit"],
  service: [],
};

export function tabsFor(role: Role): NavItem[] {
  return TABS[role].map((href) => NAV_ITEMS.find((item) => item.href === href)).filter((item): item is NavItem => !!item);
}

export function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/network") return pathname === "/network" || pathname.startsWith("/wallets");
  if (href === "/alerts") return pathname.startsWith("/alerts") || pathname.startsWith("/decisions");
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** The page the visitor is on, for the phone's title bar. */
export function current(pathname: string): NavItem | undefined {
  return NAV_ITEMS.find((item) => isActive(pathname, item.href));
}

/** "Model and policy" → "model-and-policy", "/fraud-types" → "fraud-types". */
export function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
