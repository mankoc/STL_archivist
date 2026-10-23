import {
  EMPTY,
  countOf,
  cycleGroup,
  cycleValue,
  folderTree,
  libraryFolderTree,
  groupMarkOf,
  isEmpty,
  markOf,
  type FilterTree,
} from "./filters";

describe("value cycling", () => {
  it("goes off → include → exclude → off", () => {
    let s = cycleValue(EMPTY, "tag", "dragon");
    expect(s.include).toEqual({ tag: ["dragon"] });
    expect(markOf(s, "tag", "dragon")).toBe("include");
    s = cycleValue(s, "tag", "dragon");
    expect(s.include).toEqual({});
    expect(s.exclude).toEqual({ tag: ["dragon"] });
    s = cycleValue(s, "tag", "dragon");
    expect(isEmpty(s)).toBe(true);
  });

  it("keeps other values and compares dates by value", () => {
    let s = cycleValue(EMPTY, "date", { year: 2024, month: 3 });
    s = cycleValue(s, "date", { year: 2023 });
    expect(markOf(s, "date", { year: 2024, month: 3 })).toBe("include");
    s = cycleValue(s, "date", { year: 2024, month: 3 });
    expect(s.include).toEqual({ date: [{ year: 2023 }] });
    expect(s.exclude).toEqual({ date: [{ year: 2024, month: 3 }] });
  });
});

describe("group cycling", () => {
  it("goes off → has → no → off", () => {
    let s = cycleGroup(EMPTY, "tag");
    expect(groupMarkOf(s, "tag")).toBe("has");
    s = cycleGroup(s, "tag");
    expect(groupMarkOf(s, "tag")).toBe("no");
    expect(s.has_value).toEqual([]);
    s = cycleGroup(s, "tag");
    expect(isEmpty(s)).toBe(true);
  });
});

describe("counts", () => {
  const tree = {
    scoped: false,
    counts: { tag: { dragon: 2 } },
    scoped_counts: { tag: {} },
  } as unknown as FilterTree;

  it("uses the unscoped counts without a filter", () => {
    expect(countOf(tree, "tag", "dragon")).toBe(2);
    expect(countOf(tree, "tag", "elf")).toBeUndefined();
  });

  it("uses scoped counts (zero when missing) with a filter", () => {
    expect(countOf({ ...tree, scoped: true }, "tag", "dragon")).toBe(0);
  });

  it("falls back to unscoped counts for a section the server did not scope", () => {
    const sole = { ...tree, scoped: true, scoped_counts: { creator: { Alice: 1 } } } as unknown as FilterTree;
    expect(countOf(sole, "tag", "dragon")).toBe(2);
    expect(countOf(sole, "creator", "Bob")).toBe(0);
  });
});

describe("folder tree", () => {
  it("nests folders under their drive", () => {
    const t = folderTree(["F:/lib/Bob", "F:/lib/Alice", "E:/x", "F:/lib/Bob/sub"]);
    expect(t.map((n) => n.name)).toEqual(["E:", "F:"]);
    const lib = t[1].children[0];
    expect(lib.path).toBe("F:/lib");
    expect(lib.children.map((n) => n.name)).toEqual(["Alice", "Bob"]);
    expect(lib.children[1].children[0].path).toBe("F:/lib/Bob/sub");
  });

  it("keeps POSIX roots", () => {
    const t = folderTree(["/srv/stl/a"]);
    expect(t[0].path).toBe("/");
    expect(t[0].children[0].path).toBe("/srv");
  });

  it("groups folders as library, then folder, then sub-folders", () => {
    const libs = [
      { id: 1, name: "Minis", folders: [{ root: "D:\\STL" }, { root: "/srv/more/" }, { root: "E:\\empty" }] },
      { id: 2, name: "Gambody", folders: [{ root: "D:\\STL\\Gambody" }] },
      { id: 3, name: "Unused", folders: [{ root: "/nowhere" }] },
    ];
    const folders = ["D:/STL", "D:/STL/Studio/A", "D:/STL/Gambody/Dragon", "/srv/more/x", "F:/loose/y", "D:/STLX/z"];
    const t = libraryFolderTree(folders, libs);
    expect(t.libraries.map((l) => l.name)).toEqual(["Minis", "Gambody"]);
    const [stl, more] = t.libraries[0].roots;
    expect([stl.name, stl.path, more.name, more.path]).toEqual(["D:\\STL", "D:/STL", "/srv/more/", "/srv/more"]);
    expect(stl.children.map((n) => n.path)).toEqual(["D:/STL/Studio"]);
    expect(stl.children[0].children[0].path).toBe("D:/STL/Studio/A");
    expect(more.children[0].path).toBe("/srv/more/x");
    // The deepest root wins.
    expect(t.libraries[1].roots[0].children.map((n) => n.path)).toEqual(["D:/STL/Gambody/Dragon"]);
    // Folders under no root, and look-alike prefixes, stay plain.
    expect(t.other.map((n) => n.name)).toEqual(["D:", "F:"]);
  });
});
