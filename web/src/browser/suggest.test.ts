import { describe, expect, it } from "vitest";

import { applySuggestion, suggestions, typedPart } from "./suggest";

const SCULPTORS = ["Bestiarum", "Artisan Guild", "Ghamak", "Mammoth Factory", "Artisan Guild", ""];

describe("suggestions", () => {
  it("lists every option before anything is typed", () => {
    expect(suggestions(SCULPTORS, "")).toEqual(["Bestiarum", "Artisan Guild", "Ghamak", "Mammoth Factory"]);
  });

  it("filters by what was typed, those starting with it first", () => {
    expect(suggestions(SCULPTORS, "am")).toEqual(["Ghamak", "Mammoth Factory"]);
    expect(suggestions(SCULPTORS, "  MA")).toEqual(["Mammoth Factory", "Ghamak"]);
    expect(suggestions(SCULPTORS, "zz")).toEqual([]);
  });

  it("offers nothing when the value is already the only match", () => {
    expect(suggestions(SCULPTORS, "ghamak")).toEqual([]);
  });

  it("keeps to the limit", () => {
    expect(suggestions(SCULPTORS, "", false, 2)).toEqual(["Bestiarum", "Artisan Guild"]);
  });

  it("completes a list's last item, without the items already in it", () => {
    const tags = ["dragon", "dwarf", "knight", "undead"];
    expect(typedPart("knight, dw", true)).toBe("dw");
    expect(suggestions(tags, "knight, d", true)).toEqual(["dragon", "dwarf", "undead"]);
    expect(suggestions(tags, "Knight,", true)).toEqual(["dragon", "dwarf", "undead"]);
    expect(suggestions(tags, "kn", true)).toEqual(["knight"]);
  });
});

describe("applySuggestion", () => {
  it("replaces the value, or a list's last item", () => {
    expect(applySuggestion("gha", "Ghamak")).toBe("Ghamak");
    expect(applySuggestion("kn", "knight", true)).toBe("knight");
    expect(applySuggestion("knight,dw", "dwarf", true)).toBe("knight, dwarf");
    expect(applySuggestion("knight, ", "dwarf", true)).toBe("knight, dwarf");
  });
});
