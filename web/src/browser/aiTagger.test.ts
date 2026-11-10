import type { Schemas } from "../api/client";
import { addTag, fieldsOf, needsSuggestion, removeTag, suggestJob } from "./aiTagger";

const job = (id: number, kind: string, request: object): Schemas["JobInfo"] =>
  ({ id, kind, request: { kind, ...request }, state: "done", user: "ed", done: 0, total: 0, created_at: id }) as Schemas["JobInfo"];

describe("aiTagger", () => {
  it("edits tags without duplicates", () => {
    expect(addTag(["Batman"], " cape ")).toEqual(["Batman", "cape"]);
    expect(addTag(["Batman"], "batman")).toEqual(["Batman"]);
    expect(addTag(["Batman"], "  ")).toEqual(["Batman"]);
    expect(removeTag(["a", "b"], "a")).toEqual(["b"]);
  });

  it("fills every field", () => {
    expect(fieldsOf(null).title).toBe("");
    const f = fieldsOf({ model_id: 1, title: "t", archive_path: "p", image_ids: [], tags: [], fields: { title: "X", year: 2023 } });
    expect([f.title, f.year, f.source]).toEqual(["X", 2023, ""]);
  });

  it("finds the newest suggestion job of a model", () => {
    const jobs = [job(1, "ai_suggest", { id: 5 }), job(3, "ai_suggest", { id: 5 }), job(4, "ai_suggest", { id: 6 }), job(5, "crawl", {})];
    expect(suggestJob(jobs, 5)?.id).toBe(3);
    expect(suggestJob(jobs, 7)).toBeUndefined();
  });

  it("asks again after Save & next only when no answer is coming", () => {
    const at = (state: string) => ({ ...job(1, "ai_suggest", { id: 5 }), state }) as Schemas["JobInfo"];
    expect(needsSuggestion(undefined)).toBe(true);
    expect(needsSuggestion(at("failed"))).toBe(true);
    expect(needsSuggestion(at("cancelled"))).toBe(true);
    expect(needsSuggestion(at("running"))).toBe(false);
    expect(needsSuggestion(at("queued"))).toBe(false);
    expect(needsSuggestion(at("done"))).toBe(false);
  });
});
