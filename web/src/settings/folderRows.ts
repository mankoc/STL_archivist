// Settings → Patterns: which folder rows to show (PatternsTab.tsx).

/** At most this many folder rows at once: a library can hold thousands of folders. */
export const MAX_ROWS = 200;

/** The rows matching `filter` (any part of the folder, ignoring case), those with a pattern
 * of their own first, at most `MAX_ROWS`, and how many matching rows that leaves out. */
export function folderRows<T extends { folder: string; pattern?: string | null }>(rows: T[], filter: string): { rows: T[]; hidden: number } {
  const f = filter.trim().toLowerCase();
  const matching = rows.filter((r) => !f || r.folder.toLowerCase().includes(f));
  const ordered = [...matching.filter((r) => r.pattern), ...matching.filter((r) => !r.pattern)];
  return { rows: ordered.slice(0, MAX_ROWS), hidden: Math.max(0, ordered.length - MAX_ROWS) };
}
