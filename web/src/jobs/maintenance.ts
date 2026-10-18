// Database Maintenance: which job's record list to show, and which records an
// action applies to.
import type { Schemas } from "../api/client";

type Job = Schemas["JobInfo"];
export type RecordRow = Schemas["RecordRow"];

/** Jobs whose result is a record list. */
export const RECORD_KINDS = ["verify_archives", "find_duplicate_entries", "find_moved", "search_folder"];

export const MISSING = ["Missing", "Flagged Missing"];
export const MOVED = "Moved (in DB)";

export const STATUS_COLOR: Record<string, string> = {
  Missing: "red",
  "Flagged Missing": "orange",
  Modified: "yellow",
  Duplicate: "grape",
  [MOVED]: "blue",
  Found: "green",
};

/** The newest job that produces a record list. */
export function latestRecordsJob(jobs: Job[] | undefined): Job | undefined {
  return (jobs ?? []).filter((j) => RECORD_KINDS.includes(j.kind)).reduce<Job | undefined>((a, j) => (!a || j.id > a.id ? j : a), undefined);
}

/** The selected rows matching `pred`, or every row matching it when none is selected. */
export function targets(rows: RecordRow[], selected: Set<number>, pred: (r: RecordRow) => boolean): RecordRow[] {
  const chosen = selected.size ? rows.filter((r) => selected.has(r.id)) : rows;
  return chosen.filter(pred);
}

export const isMissing = (r: RecordRow) => MISSING.includes(r.status);
export const isMoved = (r: RecordRow) => r.status === MOVED;
