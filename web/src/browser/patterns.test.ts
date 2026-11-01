import { savedPatterns, withoutPattern, withPattern } from "./patterns";

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
