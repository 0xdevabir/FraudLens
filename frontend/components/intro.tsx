"use client";

import { useEffect, useState } from "react";

import { LogoMark } from "./brand";

/**
 * Full-viewport opening beat (WebNest / ShopZen style): brand holds, then the curtain lifts.
 * Plays on every full page load. Skipped only when the user prefers reduced motion.
 */
export function Intro() {
  const [phase, setPhase] = useState<"show" | "exit" | "done">("show");

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setPhase("done");
      return;
    }

    document.documentElement.classList.add("fl-intro-active");

    // Hold the fully-visible brand, then lift the curtain.
    const exitAt = window.setTimeout(() => setPhase("exit"), 2000);
    const doneAt = window.setTimeout(() => {
      setPhase("done");
      document.documentElement.classList.remove("fl-intro-active");
    }, 2900);

    return () => {
      window.clearTimeout(exitAt);
      window.clearTimeout(doneAt);
      document.documentElement.classList.remove("fl-intro-active");
    };
  }, []);

  if (phase === "done") return null;

  return (
    <div
      className={phase === "exit" ? "fl-intro fl-intro--exit" : "fl-intro"}
      role="presentation"
      aria-hidden="true"
    >
      <div className="fl-intro__glow" />
      <div className="fl-intro__brand">
        <LogoMark size={84} animated className="fl-intro__mark" />
        <div className="fl-intro__word">FraudLens</div>
        <p className="fl-intro__tag">Real-time fraud decisions for mobile money</p>
      </div>
    </div>
  );
}
