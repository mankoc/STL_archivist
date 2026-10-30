import { addJob, changesText, countsText, describe as label, duration, elapsed, percent, progressText, remaining, sortJobs, type Job } from "./jobs";

const job = (over: Partial<Job>): Job =>
  ({
    id: 1, kind: "crawl", request: { kind: "crawl", folder: "D:/STL" }, state: "running", user: "ed",
    done: 0, total: 0, created_at: 100, ...over,
  }) as Job;

describe("jobs", () => {
  it("computes progress", () => {
    expect(percent(job({ done: 5, total: 20 }))).toBe(25);
    expect(percent(job({}))).toBeNull();
    expect(percent(job({ state: "done" }))).toBe(100);
    expect(percent(job({ done: 30, total: 20 }))).toBe(100);
  });

  it("describes jobs", () => {
    expect(label(job({}))).toBe("Crawl D:/STL");
    expect(label(job({ kind: "prune_images", request: { kind: "prune_images", keep: 3 } }))).toBe("Prune images (keep 3)");
    expect(label(job({ kind: "recompute_dhashes", request: { kind: "recompute_dhashes", all: true } }))).toBe("Recompute image hashes (all)");
    expect(label(job({ kind: "verify_archives", request: { kind: "verify_archives" } }))).toBe("Verify archives");
    expect(label(job({ kind: "recheck_warnings", request: { kind: "recheck_warnings", warning: "no_images" } }))).toBe("Re-check warnings (images)");
    expect(label(job({ kind: "search_folder", request: { kind: "search_folder", folder: "D:/old", ids: [1] } }))).toBe("Search in folder D:/old");
  });

  it("shows the count and the time left", () => {
    const running = (o: Partial<Job>) => job({ state: "running", started_at: 100, ...o });
    expect(progressText(running({ done: 0, total: 0 }), 200)).toBeNull();
    expect(progressText(running({ done: 0, total: 8000 }), 101)).toBe(`0 / ${(8000).toLocaleString()} (0%)`);
    expect(remaining(running({ done: 1000, total: 8000 }), 102)).toBeNull(); // too early to tell
    expect(remaining(running({ done: 1000, total: 8000 }), 200)).toBe(700);
    expect(progressText(running({ done: 1000, total: 8000 }), 200)).toBe(`${(1000).toLocaleString()} / ${(8000).toLocaleString()} (13%) · about 11 min 40 s left`);
    expect(progressText(job({ state: "done", done: 5, total: 5, started_at: 100 }), 200)).toBeNull();
  });

  it("shows a crawl's counts", () => {
    expect(countsText(job({}))).toBeNull();
    expect(countsText(job({ counts: { new: 3, updated: 12, deleted: 0 } }))).toBe("New: 3 · Deleted: 0 · Updated: 12");
  });

  it("lists a crawl's changes", () => {
    expect(changesText(undefined, undefined)).toBe("");
    expect(changesText({ new: 0, updated: 0, deleted: 0 }, { new: [], updated: [], deleted: [] })).toBe("No archives added, deleted or updated.");
    expect(changesText({ new: 1, updated: 3, deleted: 0 }, { new: ["D:/a.zip"], updated: ["D:/b.zip"], deleted: [] })).toBe(
      "New (1)\n  D:/a.zip\n\nUpdated (3)\n  D:/b.zip\n… and 2 more",
    );
  });

  it("formats durations", () => {
    expect(duration(45)).toBe("45 s");
    expect(duration(185)).toBe("3 min 05 s");
    expect(duration(7620)).toBe("2 h 07 min");
    expect(elapsed(job({}), 200)).toBeNull();
    expect(elapsed(job({ started_at: 100 }), 160)).toBe("1 min 00 s");
    expect(elapsed(job({ started_at: 100, finished_at: 130 }), 999)).toBe("30 s");
  });

  it("sorts active jobs first, then newest", () => {
    const sorted = sortJobs([job({ id: 1, state: "done" }), job({ id: 2, state: "queued" }), job({ id: 3, state: "failed" })]);
    expect(sorted.map((j) => j.id)).toEqual([2, 3, 1]);
  });

  it("adds a submitted job unless its events came first", () => {
    const queued = job({ id: 7, state: "queued" });
    expect(addJob(undefined, queued)).toEqual([queued]);
    const done = job({ id: 7, state: "done" });
    expect(addJob([done], queued)).toEqual([done]);
  });
});
