// Thumbnail grid layout: columns for a width, and which id pages a range of
// visible rows needs.

export const CARD_WIDTH = 180;
export const CARD_GAP = 10;
export const GRID_PAGE = 120;

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
