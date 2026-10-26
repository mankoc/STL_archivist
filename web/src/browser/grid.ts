// Thumbnail grid layout: columns for a width, card sizes, and which id pages a
// range of visible rows needs.

export const CARD_WIDTH = 180;
export const CARD_GAP = 10;
export const GRID_PAGE = 120;
/** The card widths the size slider offers (thumbnails are made up to 512 px). */
export const CARD_MIN = 120;
export const CARD_MAX = 480;
const CARD_KEY = "stl.folderCardWidth";

/** A card's image height and row height for a card `width` (180 → 150 and 220). */
export function cardLayout(width: number): { image: number; row: number } {
  const image = Math.round((width * 5) / 6);
  return { image, row: image + 70 };
}

/** `width` within the slider's range. */
export function clampCard(width: number): number {
  return Math.round(Math.min(Math.max(width, CARD_MIN), CARD_MAX));
}

/** The card width last chosen in this browser (it depends on the screen, not the account). */
export function loadCardWidth(): number {
  try {
    const v = Number(localStorage.getItem(CARD_KEY));
    return v ? clampCard(v) : CARD_WIDTH;
  } catch {
    return CARD_WIDTH;
  }
}

export function saveCardWidth(width: number): void {
  try {
    localStorage.setItem(CARD_KEY, String(clampCard(width)));
  } catch {
    /* a convenience only */
  }
}

/** Columns that fit `width` (at least one). */
export function columnsFor(width: number, card = CARD_WIDTH, gap = CARD_GAP): number {
  return Math.max(1, Math.floor((width + gap) / (card + gap)));
}

/** The page numbers (of `pageSize` items) covering rows `first`..`last` of `cols` columns. */
export function pagesFor(first: number, last: number, cols: number, total: number, pageSize = GRID_PAGE): number[] {
  if (total <= 0 || last < first) return [];
  const from = Math.max(0, first * cols);
  const to = Math.min(total - 1, (last + 1) * cols - 1);
  if (to < from) return [];
  const out: number[] = [];
  for (let p = Math.floor(from / pageSize); p <= Math.floor(to / pageSize); p++) out.push(p);
  return out;
}
