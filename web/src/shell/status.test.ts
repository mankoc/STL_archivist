import type { Job } from "../jobs/jobs";
import { status, totalsText } from "./status";

const job = (over: Partial<Job>): Job =>
  ({
    id: 1, kind: "crawl", request: { kind: "crawl", folder: "D:/STL" }, state: "running", user: "ed",
    done: 0, total: 0, created_at: 100, ...over,
  }) as Job;

describe("status bar", () => {
  it("shows the running job and counts the queued ones", () => {
    const jobs = [
      job({ id: 1, state: "done" }),
      job({ id: 2, state: "running", phase: "Extracting new/changed archives…", done: 25, total: 100, started_at: 100 }),
      job({ id: 3, state: "queued" }),
      job({ id: 4, state: "queued" }),
    ];
    const s = status(jobs, [], 102);
    expect(s.job).toEqual({ label: "Crawl D:/STL", detail: "Extracting new/changed archives… · 25 / 100 (25%)", percent: 25 });
    expect(s.queued).toBe(2);
  });

  it("is quiet without a running job", () => {
    const s = status([job({ state: "failed" }), job({ id: 2, state: "queued" })], undefined, 0);
    expect(s.job).toBeNull();
    expect(s.queued).toBe(1);
    expect(totalsText(s)).toBe("");
  });

  it("totals the archives of every library", () => {
    expect(totalsText(status([], [{ models: 1200 }, { models: 34 }, { models: 0 }], 0))).toBe(
      `${(1234).toLocaleString()} archives in 3 libraries`,
    );
    expect(totalsText(status([], [{ models: 1 }], 0))).toBe("1 archive in 1 library");
    expect(totalsText(status([], [], 0))).toBe("0 archives in 0 libraries");
  });
});
