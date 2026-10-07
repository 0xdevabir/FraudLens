"use client";

import Link from "next/link";
import { useId, type ReactNode } from "react";

import { cx } from "./ui";

/** Optical lens mark: concentric rings + sage focus — FraudLens, not a letter badge. */
export function LogoMark({
  size = 32,
  className,
  animated = false,
}: {
  size?: number;
  className?: string;
  /** Soft pulse on the scan arc (intro / idle brand moments). */
  animated?: boolean;
}) {
  const uid = useId().replace(/:/g, "");
  const plate = `fl-plate-${uid}`;
  const glass = `fl-glass-${uid}`;
  const iris = `fl-iris-${uid}`;

  return (
    <svg
      viewBox="0 0 40 40"
      width={size}
      height={size}
      className={cx("shrink-0", className)}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={plate} x1="6" y1="2" x2="34" y2="38" gradientUnits="userSpaceOnUse">
          <stop stopColor="#3a4242" />
          <stop offset="0.55" stopColor="#2a3030" />
          <stop offset="1" stopColor="#222828" />
        </linearGradient>
        <radialGradient id={glass} cx="38%" cy="32%" r="70%">
          <stop stopColor="#a8bca1" stopOpacity="0.28" />
          <stop offset="0.55" stopColor="#a8bca1" stopOpacity="0.08" />
          <stop offset="1" stopColor="#a8bca1" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={iris} cx="42%" cy="38%" r="60%">
          <stop stopColor="#d2ddcd" />
          <stop offset="1" stopColor="#a8bca1" />
        </radialGradient>
      </defs>

      {/* Plate */}
      <rect x="1.25" y="1.25" width="37.5" height="37.5" rx="11.5" fill={`url(#${plate})`} />
      <rect x="1.25" y="1.25" width="37.5" height="37.5" rx="11.5" fill="none" stroke="rgb(255 255 255 / 0.12)" strokeWidth="1" />

      {/* Outer barrel */}
      <circle cx="20" cy="20" r="12.5" fill="none" stroke="rgb(255 255 255 / 0.16)" strokeWidth="1.15" />

      {/* Lens glass */}
      <circle cx="20" cy="20" r="9.6" fill={`url(#${glass})`} stroke="#a8bca1" strokeWidth="1.55" />

      {/* Aperture ring */}
      <circle cx="20" cy="20" r="5.75" fill="none" stroke="rgb(245 245 245 / 0.62)" strokeWidth="1.1" />

      {/* Active scan */}
      <path
        d="M20 7.9 A12.1 12.1 0 0 1 32.1 20"
        fill="none"
        stroke="#96eefb"
        strokeWidth="1.7"
        strokeLinecap="round"
        className={animated ? "origin-[20px_20px] animate-[lens-scan_2.4s_ease-in-out_infinite]" : undefined}
        opacity={0.9}
      />

      {/* Focus */}
      <circle cx="20" cy="20" r="2.85" fill={`url(#${iris})`} />
      <circle cx="19.2" cy="19" r="0.85" fill="rgb(255 255 255 / 0.35)" />
    </svg>
  );
}

function BrandFace({
  size,
  className,
  markClassName,
  wordmark,
  stacked,
  animated,
}: {
  size: number;
  className?: string;
  markClassName?: string;
  wordmark: boolean;
  stacked: boolean;
  animated: boolean;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center text-fg",
        stacked ? "flex-col gap-3" : "gap-2.5",
        className,
      )}
    >
      <LogoMark size={size} className={markClassName} animated={animated} />
      {wordmark && (
        <span
          className={cx(
            "font-bold tracking-tight",
            stacked ? "text-[1.65rem] leading-none" : "text-[1.05em] leading-none",
          )}
        >
          FraudLens
        </span>
      )}
    </span>
  );
}

/** Mark + wordmark. Pass `href` (usually `/`) to make the brand a home link. */
export function Brand({
  size = 28,
  className,
  markClassName,
  wordmark = true,
  stacked = false,
  animated = false,
  href,
  onClick,
}: {
  size?: number;
  className?: string;
  markClassName?: string;
  wordmark?: boolean;
  stacked?: boolean;
  animated?: boolean;
  /** When set, the brand is a link — typically `/` for the console home. */
  href?: string;
  onClick?: () => void;
}) {
  const face = (
    <BrandFace
      size={size}
      className={href ? undefined : className}
      markClassName={markClassName}
      wordmark={wordmark}
      stacked={stacked}
      animated={animated}
    />
  );

  if (!href) return face;

  return (
    <Link
      href={href}
      onClick={onClick}
      aria-label="FraudLens home"
      className={cx(
        "rounded-xl outline-offset-4 transition hover:opacity-90 active:scale-[0.98]",
        className,
      )}
    >
      {face}
    </Link>
  );
}

/** Compact mark + wordmark for the phone top bar. */
export function BrandMarkLink({
  href = "/",
  children,
  className,
}: {
  href?: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <Link
      href={href}
      aria-label="FraudLens home"
      className={cx(
        "inline-flex items-center gap-1.5 rounded-lg text-[0.8125rem] font-bold tracking-tight text-fg outline-offset-2 transition hover:opacity-90 active:scale-[0.98]",
        className,
      )}
    >
      <LogoMark size={18} />
      {children ?? "FraudLens"}
    </Link>
  );
}
