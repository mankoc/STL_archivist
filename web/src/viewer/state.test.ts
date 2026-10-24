import { currentOf, DEFAULT_STYLE, folderState, restyle, stepFile, toggleFile, toggleFiles } from "./state";

describe("viewer state", () => {
  it("adds and takes away shown files", () => {
    expect(toggleFile(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleFile(["a", "b"], "a")).toEqual(["b"]);
  });

  it("toggles a folder's files together", () => {
    expect(toggleFiles(["x"], ["a", "b"])).toEqual(["x", "a", "b"]);
    expect(toggleFiles(["a", "x"], ["a", "b"])).toEqual(["a", "x", "b"]);
    expect(toggleFiles(["a", "x", "b"], ["a", "b"])).toEqual(["x"]);
    expect(folderState(["a"], ["a", "b"])).toBe("some");
    expect(folderState(["a", "b"], ["a", "b"])).toBe("all");
    expect(folderState([], ["a"])).toBe("none");
  });

  it("styles files from the default", () => {
    const s = restyle({}, ["a", "b"], { wire: true });
    expect(s.a).toEqual({ ...DEFAULT_STYLE, wire: true });
    expect(restyle(s, ["a"], { color: "#4e9ae6" }).a).toEqual({ ...DEFAULT_STYLE, wire: true, color: "#4e9ae6" });
  });

  it("keeps the current file one that is shown", () => {
    expect(currentOf(["a", "b"], "a")).toBe("a");
    expect(currentOf(["a"], "b")).toBe("a"); // b was taken out of the view
    expect(currentOf([], "b")).toBe(null);
    expect(currentOf(["a", "c"], null)).toBe("c");
  });

  it("steps through the files, stopping at the ends", () => {
    const order = ["a", "b", "c"];
    expect(stepFile(order, "b", 1)).toBe("c");
    expect(stepFile(order, "c", 1)).toBe("c");
    expect(stepFile(order, "a", -1)).toBe("a");
    expect(stepFile(order, null, 1)).toBe("a");
    expect(stepFile([], "a", 1)).toBe(null);
  });
});
