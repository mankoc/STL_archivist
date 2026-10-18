import { latestJob, matches, pageCount, pageOf, percentLabel, scoreLabel, type Job } from "./duplicates";

const job = (id: number, kind: string): Job =>
  ({ id, kind, request: { kind }, state: "done", user: "ed", done: 0, total: 0, created_at: id }) as Job;

describe("duplicates", () => {
  it("picks the newest job of a finder", () => {
    const jobs = [job(1, "find_image_duplicates"), job(4, "crawl"), job(3, "find_image_duplicates"), job(2, "find_content_duplicates")];
    expect(latestJob(jobs, "images")?.id).toBe(3);
    expect(latestJob(jobs, "content")?.id).toBe(2);
    expect(latestJob(jobs, "metadata")).toBeUndefined();
    expect(latestJob(undefined, "images")).toBeUndefined();
  });

  it("labels scores", () => {
    expect(scoreLabel("dhash", 0)).toBe("identical");
    expect(scoreLabel("dhash", 1)).toBe("1 bit differs");
    expect(scoreLabel("dhash", 7)).toBe("7 bits differ");
    expect(scoreLabel("clip", 0.96234)).toBe("similarity 0.962");
    expect(percentLabel(0.8)).toBe("80 %");
    expect(percentLabel(2 / 3)).toBe("66.7 %");
  });

  it("filters by words", () => {
    expect(matches("", "a")).toBe(true);
    expect(matches("red drag", "Red Dragon", null)).toBe(true);
    expect(matches("red knight", "Red Dragon", "D:/x/knight.zip")).toBe(true);
    expect(matches("blue", "Red Dragon", undefined)).toBe(false);
  });

  it("pages rows", () => {
    const rows = Array.from({ length: 250 }, (_, i) => i);
    expect(pageCount(0)).toBe(1);
    expect(pageCount(250)).toBe(3);
    expect(pageOf(rows, 1)).toHaveLength(100);
    expect(pageOf(rows, 3)).toEqual(rows.slice(200));
    expect(pageOf(rows, 9)).toEqual(rows.slice(200));
    expect(pageOf(rows, 0)[0]).toBe(0);
  });
});
