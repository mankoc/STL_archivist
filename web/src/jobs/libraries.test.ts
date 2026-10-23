import { folderAt, folderBadges, folderMoveText, folderRemovalText, libraryRemovalText, moveTargets, outerFolder, relocationText, type LibraryFolder, type LibraryInfo } from "./libraries";

const folder = (id: number, root: string, over: Partial<LibraryFolder> = {}): LibraryFolder => ({
  id, uuid: `f${id}`, name: `F${id}`, root, models: 2, monitored: false, reachable: true, ...over,
});

const library = (id: number, folders: LibraryFolder[], over: Partial<LibraryInfo> = {}): LibraryInfo => ({
  id, uuid: `u${id}`, name: `L${id}`, folders, models: folders.reduce((n, f) => n + f.models, 0), ...over,
});

describe("libraries", () => {
  it("shows a folder's state", () => {
    expect(folderBadges(folder(1, "/a"))).toEqual([]);
    expect(folderBadges(folder(1, "/a", { reachable: false, monitored: true, origin: "migration" })).map((b) => b[1])).toEqual([
      "root not found", "monitored", "made by the move to libraries",
    ]);
  });

  it("finds the folder around another one, in any library", () => {
    const libs = [library(1, [folder(1, "D:\\STL"), folder(4, "/srv/stl")]), library(2, [folder(2, "d:/stl/Gambody"), folder(3, "D:/STLX")])];
    expect(outerFolder(libs[1].folders[0], libs)?.folder.id).toBe(1);
    expect(outerFolder(libs[1].folders[0], libs)?.library.id).toBe(1);
    expect(outerFolder(libs[1].folders[1], libs)).toBeUndefined();
    expect(outerFolder(folder(5, "/srv/stl/a/b"), [...libs, library(3, [folder(6, "/srv/stl/a")])])?.folder.id).toBe(6);
    expect(outerFolder(folder(7, "/SRV/stl/x"), libs)).toBeUndefined();
  });

  it("explains removing a folder", () => {
    const outer = library(1, [folder(1, "/stl")]);
    const two = library(2, [folder(2, "/stl/g"), folder(3, "/other")]);
    const libs = [outer, two];
    expect(folderRemovalText(two.folders[0], two, libs)).toContain("move to the folder /stl of L1");
    expect(folderRemovalText(two.folders[1], two, libs)).toContain("are deleted");
    expect(folderRemovalText(two.folders[1], two, libs)).not.toContain("library goes too");
    expect(folderRemovalText(outer.folders[0], outer, libs)).toContain("only folder of L1, so the library goes too");
    expect(folderRemovalText(folder(4, "/x", { models: 0 }), two, libs)).toContain("no records");
  });

  it("explains removing a library", () => {
    const outer = library(1, [folder(1, "/stl")]);
    const two = library(2, [folder(2, "/stl/g", { models: 1 }), folder(3, "/other", { models: 3 })]);
    expect(libraryRemovalText(two, [outer, two])).toBe(
      "Its 2 folders go: 3 records are deleted from the library, with tags and images; 1 record moves to folders of other libraries around them. The archive files are not touched.",
    );
    expect(libraryRemovalText(library(3, [folder(4, "/x", { models: 0 })]), [])).toContain("no records");
  });

  it("moves a folder to another library", () => {
    const one = library(1, [folder(1, "/a", { models: 1 })]);
    const two = library(2, [folder(2, "/b"), folder(3, "/c")]);
    expect(moveTargets(two, [one, two])).toEqual([{ value: "1", label: "L1" }]);
    expect(folderMoveText(one)).toBe(
      "Its records go along with their edits, tags and thumbnails; nothing is re-read. It is the only folder of L1, so that library goes.",
    );
    expect(folderMoveText(two)).not.toContain("goes");
  });

  it("finds the folder just added", () => {
    const lib = library(1, [folder(1, "/a"), folder(2, "D:\\B")]);
    expect(folderAt(lib, "d:/b/")?.id).toBe(2);
    expect(folderAt(lib, "/elsewhere")?.id).toBe(2);
  });

  it("explains a relocation check", () => {
    const r = { library: 1, root: "/n", total: 3, found: 1, case_only: 1, missing: 1, missing_examples: [], inner: 1, applied: false };
    expect(relocationText(r)).toBe("1 of 3 archives found, 1 with another letter case, 1 missing, 1 inner folder moves along");
  });
});
