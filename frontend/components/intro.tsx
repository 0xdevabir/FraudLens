"use client";

import { useEffect, useState } from "react";

import { Brand } from "./brand";

const KEY = "fraudlens-intro";

/**
 * Full-viewport opening beat (WebNest / ShopZen style): brand holds, then the curtain lifts.
 * Once per tab session; skipped when the user prefers reduced motion.
 */
export function Intro() {
  // Render on the first paint so the curtain is already up (no content flash underneath).
  const [phase, setPhase] = useState<"show" | "exit" | "done">("show");

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    try {
      if (sessionStorage.getItem(KEY) === "1" || reduce) {
        setPhase("done");
        return;
      }
    } catch {
      /* private mode — still play once this mount */
    }

    const exitAt = window.setTimeout(() => setPhase("exit"), 1650);
    const doneAt = window.setTimeout(() => {
      setPhase("done");
      try {
        sessionStorage.setItem(KEY, "1");
      } catch {
        /* ignore */
      }
    }, 2400);
    return () => {
      window.clearTimeout(exitAt);
      window.clearTimeout(doneAt);
    };
  }, []);

  useEffect(() => {
    if (phase === "show" || phase === "exit") {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [phase]);

  if (phase === "done") return null;

  return (
    <div
      className={phase === "exit" ? "fl-intro fl-intro--exit" : "fl-intro"}
      role="presentation"
      aria-hidden="true"
    >
      <div className="fl-intro__glow" />
      <div className="fl-intro__brand">
        <Brand size={72} stacked animated />
        <p className="fl-intro__tag">Real-time fraud decisions for mobile money</p>
      </div>
    </div>
  );
}
