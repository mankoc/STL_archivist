import { describe, expect, it } from "vitest";

import { type AiEditorFields, type EditorForm, mergeAiFields } from "./aiFields";

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
