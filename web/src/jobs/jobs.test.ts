import { addJob, describe as label, duration, elapsed, percent, sortJobs, type Job } from "./jobs";

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
