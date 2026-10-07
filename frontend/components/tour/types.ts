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
  title: string;
  body: string;
  placement?: "top" | "bottom" | "left" | "right" | "auto";
  /** What the animated cursor does on the target: glide + click ripple, glide + hover, or nothing. */
  action?: "click" | "hover" | "none";
  /** Roles that see this step. Omit for everyone. */
  roles?: Role[];
}

export interface TourApi {
  active: boolean;
  start: (fromStepId?: string) => void;
  stop: () => void;
}

/** localStorage key that records the tour was seen or dismissed. */
export const TOUR_SEEN_KEY = "fraudlens.tour.seen.v1";
