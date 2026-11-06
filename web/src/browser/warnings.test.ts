import { rowTone, warningsByFile } from "./warnings";

describe("rowTone", () => {
  it("colours rows by their warnings", () => {
    expect(rowTone(null)).toBeNull();
    expect(rowTone(" \n")).toBeNull();
    expect(rowTone("Archive without images")).toBe("warning");
    expect(rowTone("Archive without images\nUnreadable archive")).toBe("unreadable");
  });
});

describe("warningsByFile", () => {
  it("gathers each file's warnings", () => {
    const map = warningsByFile([
      { warning: "Nested archives", members: [{ name: "a/inner.zip" }] },
      { warning: "Archive without images", members: [] },
      { warning: "Models with presupported stl", members: [{ name: "a/inner.zip" }, { name: "b.stl" }] },
    ]);
    expect(map.get("a/inner.zip")).toEqual(["Nested archives", "Models with presupported stl"]);
    expect(map.get("b.stl")).toEqual(["Models with presupported stl"]);
    expect(map.size).toBe(2);
  });
});
