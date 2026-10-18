// The table knows every matching id (in display order) but fetches rows in
// blocks, only for what is on screen (plus a little overscan).

export const BLOCK = 100;

/** The block numbers covering rows `start..=end` (clamped to `total`). */
export function blocksFor(start: number, end: number, total: number): number[] {
  if (total <= 0 || end < start) return [];
  const first = Math.max(0, Math.floor(start / BLOCK));
  const last = Math.floor(Math.min(end, total - 1) / BLOCK);
  const out: number[] = [];
  for (let b = first; b <= last; b++) out.push(b);
  return out;
}

/** The ids of block `b`. */
export function blockIds(ids: number[], b: number): number[] {
  return ids.slice(b * BLOCK, (b + 1) * BLOCK);
}
