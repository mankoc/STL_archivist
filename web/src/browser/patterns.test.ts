import { folderPattern, missingChoice, missingValue, savedPatterns, withoutPattern, withPattern } from "./patterns";

describe("folderPattern", () => {
  const libraries = [
    {
      folders: [
        { root: "D:\\STL", pattern: "{creator}/{title}", subfolder_patterns: [{ folder: "Packs", pattern: "{release}/{title}" }, { folder: "Packs/Old", pattern: "{title}" }] },
        { root: "D:/STL/Inner", pattern: null, subfolder_patterns: [] },
      ],
    },
  ];

  it("takes the deepest pattern kept, else the library folder's", () => {
    expect(folderPattern("D:/STL/Other", libraries)).toBe("{creator}/{title}");
    expect(folderPattern("D:/STL/Packs/2024", libraries)).toBe("{release}/{title}");
    expect(folderPattern("D:/STL/Packs/Old/x", libraries)).toBe("{title}");
    expect(folderPattern("D:/STL/Packs2", libraries)).toBe("{creator}/{title}");
    expect(folderPattern("D:/STL/Inner/a", libraries)).toBe("");
    expect(folderPattern("E:/elsewhere", libraries)).toBe("");
  });
});

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
