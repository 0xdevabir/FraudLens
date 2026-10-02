import type { Tier } from "./types";

const grouped = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return digits ? value.toLocaleString("en-IN", { maximumFractionDigits: digits }) : grouped.format(value);
}

/** Taka with South Asian grouping. `short` uses lakh and crore, the way amounts are spoken. */
export function taka(value: number | null | undefined, short = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  if (short) {
    const abs = Math.abs(value);
    if (abs >= 1e7) return `৳${(value / 1e7).toFixed(2)} crore`;
    if (abs >= 1e5) return `৳${(value / 1e5).toFixed(2)} lakh`;
  }
  return `৳${grouped.format(value)}`;
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return `${(value * 100).toFixed(digits)}%`;
}

const dhaka = { timeZone: "Asia/Dhaka" } as const;
const dateTime = new Intl.DateTimeFormat("en-GB", {
  ...dhaka, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
});
const dateOnly = new Intl.DateTimeFormat("en-GB", { ...dhaka, day: "2-digit", month: "short", year: "numeric" });
const timeOnly = new Intl.DateTimeFormat("en-GB", {
  ...dhaka, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** All times are shown in Dhaka time, whatever the reviewer's browser is set to. */
export function when(value: string | null | undefined): string {
  const date = parse(value);
  return date ? dateTime.format(date) : "–";
}

export function day(value: string | null | undefined): string {
  const date = parse(value);
  return date ? dateOnly.format(date) : "–";
}

export function clock(value: string | null | undefined): string {
  const date = parse(value);
  return date ? timeOnly.format(date) : "–";
}

export function shortDay(value: string): string {
  const [, month, dayOfMonth] = value.slice(0, 10).split("-");
  return `${Number(dayOfMonth)}/${Number(month)}`;
}

/** "step_up" → "Step up"; "R04_RECIPIENT_LOOKS_LIKE_MULE" stays readable too. */
export function words(value: string | null | undefined): string {
  if (!value) return "–";
  const text = value.replace(/[_.]+/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export const TIERS: Tier[] = ["allow", "warn", "step_up", "hold"];

export const TIER_LABEL: Record<Tier, string> = {
  allow: "Allow",
  warn: "Warn",
  step_up: "Step-up",
  hold: "Hold",
};

/** One colour per tier everywhere: charts, badges, the phone. */
export const TIER_COLOR: Record<Tier, string> = {
  allow: "var(--color-good)",
  warn: "var(--color-warn)",
  step_up: "var(--color-alert)",
  hold: "var(--color-bad)",
};

/** Wallet, agent and handset ids are personal data: shown masked unless a reviewer asks. */
export function maskId(id: string): string {
  return id.length <= 5 ? `${id.charAt(0)}***` : `${id.charAt(0)}***${id.slice(-4)}`;
}

/** A ring is named after one of its wallets, so its name is masked the same way. */
export function ringLabel(id: string): string {
  return id.replace(/^R-(.).*(.{4})$/, "R-$1***$2");
}

export function idKind(id: string): "wallet" | "agent" | "device" {
  if (id.startsWith("A")) return "agent";
  if (id.startsWith("D")) return "device";
  return "wallet";
}
