import { fileTree, foldersOf, treeFiles } from "./fileTree";

const shape = (nodes: ReturnType<typeof fileTree>["data"]): unknown =>
  nodes.map((n) => (n.children ? { [String(n.label)]: shape(n.children) } : n.label));

describe("file tree", () => {
  const files = [
    { name: "readme.txt", size: 10, crc: "00000001" },
    { name: "STL/part10.stl", size: 100, crc: "00000002" },
    { name: "STL/part2.stl", size: 200, crc: "00000003" },
    { name: "STL/supported/part2.stl", size: 300, crc: "00000004" },
    { name: "images/", size: 0, crc: "00000000" },
    { name: "a.png", size: null, crc: "00000005" },
  ];

  it("puts folders first, then files, by name", () => {
    expect(shape(fileTree(files).data)).toEqual([
      { images: [] },
      { STL: [{ supported: ["part2.stl"] }, "part2.stl", "part10.stl"] },
      "a.png",
      "readme.txt",
    ]);
  });

  it("totals folders and keeps each file's member name", () => {
    const tree = fileTree(files);
    expect(tree.info.get("STL/")).toEqual({
      kind: "folder",
      files: ["STL/supported/part2.stl", "STL/part2.stl", "STL/part10.stl"],
      size: 600,
    });
    expect(tree.info.get("STL/supported/part2.stl")).toEqual({ kind: "file", name: "STL/supported/part2.stl", size: 300, crc: "00000004" });
    expect(treeFiles(tree)).toEqual(["STL/supported/part2.stl", "STL/part2.stl", "STL/part10.stl", "a.png", "readme.txt"]);
  });

  it("shows a chain of single folders as one row", () => {
    const tree = fileTree([{ name: "A/B_32mm/x.stl" }, { name: "A/B_32mm/y.stl" }]);
    expect(shape(tree.data)).toEqual([{ "A / B_32mm": ["x.stl", "y.stl"] }]);
    expect(tree.data[0].value).toBe("A/B_32mm/");
    expect(foldersOf(tree, "A/B_32mm/y.stl")).toEqual(["A/B_32mm/"]);
    expect(foldersOf(fileTree(files), "STL/supported/part2.stl")).toEqual(["STL/", "STL/supported/"]);
  });

  it("keeps a name the archive holds twice", () => {
    const twice = fileTree([{ name: "a.stl" }, { name: "a.stl" }]);
    expect(twice.data.map((n) => n.value)).toEqual(["a.stl", "a.stl#2"]);
  });
});
