import type { Role } from "@/lib/types";

/** One stop on the guided tour. */
export interface TourStep {
  id: string;
  /** Chapter label shown in the progress rail, e.g. "Overview", "Operations". */
  chapter: string;
  /** Route to navigate to before this step runs. Omit to stay on the current page. */
  route?: string;
  /** `data-tour` attribute value of the element to spotlight. Omit for a centred card. */
  target?: string;
  /** Target used below the lg breakpoint, where the sidebar is hidden (e.g. a bottom-nav tab). */
  mobileTarget?: string;
  title: string;
  body: string;
  /** Bangla title and body, in plain everyday words. */
  titleBn: string;
  bodyBn: string;
  placement?: "top" | "bottom" | "left" | "right" | "auto";
  /** What the animated cursor does on the target: glide + click ripple, glide + hover, or nothing. */
  action?: "click" | "hover" | "none";
  /** Roles that see this step. Omit for everyone. */
  roles?: Role[];
}

export interface TourApi {
  active: boolean;
  /** Starts the tour; "auto" (the default) plays it by itself, "manual" waits for Next. */
  start: (fromStepId?: string, mode?: "auto" | "manual") => void;
  stop: () => void;
}

export type TourLang = "en" | "bn";

/** localStorage key for the tour's language. */
export const TOUR_LANG_KEY = "fraudlens.tour.lang.v1";

/** localStorage key that records the tour was seen or dismissed. */
export const TOUR_SEEN_KEY = "fraudlens.tour.seen.v1";
