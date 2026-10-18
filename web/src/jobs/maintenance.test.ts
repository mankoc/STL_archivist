import { isMissing, isMoved, latestRecordsJob, targets, type RecordRow } from "./maintenance";
import type { Job } from "./jobs";

const job = (id: number, kind: string): Job =>
  ({ id, kind, request: { kind }, state: "done", user: "ed", done: 0, total: 0, created_at: id }) as Job;
const row = (id: number, status: string): RecordRow => ({ id, status, title: "", path: "", hash: "", size: null, match_path: "" });

describe("maintenance", () => {
  it("picks the newest record-list job", () => {
    expect(latestRecordsJob([job(1, "verify_archives"), job(3, "find_moved"), job(4, "crawl")])?.id).toBe(3);
    expect(latestRecordsJob([job(4, "crawl")])).toBeUndefined();
  });

  it("applies actions to the selection, else to every matching row", () => {
    const rows = [row(1, "Missing"), row(2, "Modified"), row(3, "Flagged Missing"), row(4, "Moved (in DB)")];
    expect(targets(rows, new Set(), isMissing).map((r) => r.id)).toEqual([1, 3]);
    expect(targets(rows, new Set([3, 2]), isMissing).map((r) => r.id)).toEqual([3]);
    expect(targets(rows, new Set(), isMoved).map((r) => r.id)).toEqual([4]);
    expect(targets(rows, new Set([2]), () => true).map((r) => r.id)).toEqual([2]);
  });
});
