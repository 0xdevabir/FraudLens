"use client";

import { useId } from "react";

import { cx } from "./ui";

/** Optical lens mark: concentric rings + focus point — FraudLens, not a letter badge. */
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
  const iris = `fl-iris-${uid}`;

  return (
    <svg
      viewBox="0 0 40 40"
      width={size}
      height={size}
      className={cx("shrink-0 text-fg", className)}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={plate} x1="8" y1="4" x2="34" y2="38" gradientUnits="userSpaceOnUse">
          <stop stopColor="#323838" />
          <stop offset="1" stopColor="#252525" />
        </linearGradient>
        <radialGradient id={iris} cx="50%" cy="45%" r="55%">
          <stop stopColor="#c5d4bf" />
          <stop offset="1" stopColor="#a8bca1" />
        </radialGradient>
      </defs>
      <rect x="1.5" y="1.5" width="37" height="37" rx="11" fill={`url(#${plate})`} stroke="rgb(255 255 255 / 0.1)" strokeWidth="1" />
      <circle cx="20" cy="20" r="12.2" fill="none" stroke="rgb(255 255 255 / 0.14)" strokeWidth="1.25" />
      <circle cx="20" cy="20" r="9.4" fill="rgb(168 188 161 / 0.16)" stroke="#a8bca1" strokeWidth="1.7" />
      <circle cx="20" cy="20" r="5.6" fill="none" stroke="rgb(245 245 245 / 0.55)" strokeWidth="1.15" />
      <path
        d="M20 8.2 A11.8 11.8 0 0 1 31.8 20"
        fill="none"
        stroke="#96eefb"
        strokeWidth="1.6"
        strokeLinecap="round"
        className={animated ? "origin-center animate-[lens-scan_2.4s_ease-in-out_infinite]" : undefined}
        opacity={0.85}
      />
      <circle cx="20" cy="20" r="2.7" fill={`url(#${iris})`} />
      <circle cx="20" cy="20" r="0.9" fill="#1b1b1b" opacity="0.35" />
    </svg>
  );
}

export function Brand({
  size = 28,
  className,
  markClassName,
  wordmark = true,
  stacked = false,
  animated = false,
}: {
  size?: number;
  className?: string;
  markClassName?: string;
  wordmark?: boolean;
  stacked?: boolean;
  animated?: boolean;
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
