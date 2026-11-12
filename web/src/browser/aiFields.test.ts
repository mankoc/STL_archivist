import { describe, expect, it } from "vitest";

import { type AiEditorFields, applyIssues, canApply, type EditorForm, mergeAiFields } from "./aiFields";

const form: EditorForm = {
  title: "Dragon", creator: "", source: "", date: "", presupports: "", url: "", description: "", sequence: 0,
  release: "", sculptor: "", collection: "", subcollection: "", category: "", scales: ["32 mm"], tags: ["Dragon"],
};
const ai: AiEditorFields = {
  model_id: 1, title: "Red Dragon", creator: "Studio", source: "", date: "2024-03", sculptor: "", release: "",
  collection: "", subcollection: "", category: " Monster ", description: "", scales: ["32 mm"], tags: ["dragon", "wings"],
};

describe("mergeAiFields", () => {
  it("fills empty fields only and adds new scales and tags", () => {
    const { form: f, filled } = mergeAiFields(form, ai);
    expect(f.title).toBe("Dragon");
    expect([f.creator, f.date, f.category]).toEqual(["Studio", "2024-03", "Monster"]);
    expect(f.scales).toEqual(["32 mm"]);
    expect(f.tags).toEqual(["Dragon", "wings"]);
    expect(filled).toEqual(["Creator", "Date", "Category", "Tags"]);
  });
});

describe("applyIssues", () => {
  const issue = (field: string, suggestion: string) => ({ model_id: 1, title: "", archive_path: "", field, value: "", problem: "", suggestion });
  it("writes the suggestions into the form", () => {
    const f = applyIssues({ ...form, date: "2020-05" }, [issue("Scale", "28 mm, 1/10"), issue("Year", "2023"), issue("Creator", " Studio "), issue("Title", "")]);
    expect(f.scales).toEqual(["28 mm", "1/10"]);
    expect(f.date).toBe("2023-05");
    expect(f.creator).toBe("Studio");
    expect(f.title).toBe("Dragon");
  });
  it("only applies known fields with a value", () => {
    expect(canApply(issue("Year", "soon"))).toBe(false);
    expect(canApply(issue("Tags", "x"))).toBe(false);
    expect(canApply(issue("Sculptor", "Alice"))).toBe(true);
  });
});
