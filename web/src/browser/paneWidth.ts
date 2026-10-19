// The detail pane's width: dragged by its left edge, kept within limits, and
// remembered in this browser (it depends on the screen, not the account).

export const DEFAULT_WIDTH = 320;
export const MIN_WIDTH = 240;
const KEY = "stl.detailWidth";

/** `width` kept between the minimum and `maxShare` of the page's width. */
export function clampWidth(width: number, pageWidth: number, maxShare = 0.6): number {
  const max = Math.max(MIN_WIDTH, Math.floor(pageWidth * maxShare));
  return Math.round(Math.min(Math.max(width, MIN_WIDTH), max));
}

export function loadWidth(): number {
  try {
    const v = Number(localStorage.getItem(KEY));
    return Number.isFinite(v) && v >= MIN_WIDTH ? v : DEFAULT_WIDTH;
  } catch {
    return DEFAULT_WIDTH;
  }
}

export function saveWidth(width: number): void {
  try {
    localStorage.setItem(KEY, String(width));
  } catch {
    /* a convenience only */
  }
}
