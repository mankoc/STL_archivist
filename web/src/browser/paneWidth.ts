// Pane widths: the detail pane's and the mass editor's, dragged by their left
// edge, kept within limits, and remembered in this browser (they depend on the
// screen, not the account).

export const DEFAULT_WIDTH = 320;
export const MIN_WIDTH = 240;
const KEY = "stl.detailWidth";

/** The mass edit drawer (Mantine's `xl` drawer to begin with). */
export const MASS_EDIT = { key: "stl.massEditWidth", fallback: 780, min: 440, maxShare: 0.95 };

/** `width` kept between `min` and `maxShare` of the page's width. */
export function clampWidth(width: number, pageWidth: number, maxShare = 0.6, min = MIN_WIDTH): number {
  const max = Math.max(min, Math.floor(pageWidth * maxShare));
  return Math.round(Math.min(Math.max(width, min), max));
}

export function loadWidth(key = KEY, fallback = DEFAULT_WIDTH, min = MIN_WIDTH): number {
  try {
    const v = Number(localStorage.getItem(key));
    return Number.isFinite(v) && v >= min ? v : fallback;
  } catch {
    return fallback;
  }
}

export function saveWidth(width: number, key = KEY): void {
  try {
    localStorage.setItem(key, String(width));
  } catch {
    /* a convenience only */
  }
}
