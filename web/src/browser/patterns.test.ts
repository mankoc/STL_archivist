import { missingChoice, missingValue, savedPatterns, withoutPattern, withPattern } from "./patterns";

describe("saved sort patterns", () => {
  it("reads only text patterns", () => {
    expect(savedPatterns(undefined)).toEqual([]);
    expect(savedPatterns({ a: 1 })).toEqual([]);
    expect(savedPatterns(["{creator}/{title}", 3, " ", "{type}/{title}"])).toEqual(["{creator}/{title}", "{type}/{title}"]);
  });

  it("puts a saved pattern first, once", () => {
    expect(withPattern(["a", "b"], " b ")).toEqual(["b", "a"]);
    expect(withPattern(["a"], "c")).toEqual(["c", "a"]);
    expect(withPattern(["a"], "  ")).toEqual(["a"]);
    expect(withoutPattern(["a", "b"], "a")).toEqual(["b"]);
  });
});

describe("missing fields in Sort files", () => {
  it("reads a remembered choice", () => {
    expect(missingChoice({ mode: "text", text: "none" })).toEqual({ mode: "text", text: "none" });
    expect(missingChoice({ mode: "nope", text: 1 })).toEqual({ mode: "unknown", text: "" });
    expect(missingChoice(null)).toEqual({ mode: "unknown", text: "" });
  });

  it("gives the request's value", () => {
    expect(missingValue({ mode: "unknown", text: "x" })).toBeUndefined();
    expect(missingValue({ mode: "empty", text: "x" })).toBe("");
    expect(missingValue({ mode: "text", text: "x" })).toBe("x");
  });
});
