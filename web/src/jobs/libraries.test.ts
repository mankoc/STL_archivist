import { libraryBadges, outerLibrary, relocationText, removalText, type LibraryInfo } from "./libraries";

const lib = (id: number, root: string, over: Partial<LibraryInfo> = {}): LibraryInfo => ({
  id, uuid: `u${id}`, name: `L${id}`, root, models: 2, monitored: false, reachable: true, ...over,
});

describe("libraries", () => {
  it("shows a library's state", () => {
    expect(libraryBadges(lib(1, "/a"))).toEqual([]);
    expect(libraryBadges(lib(1, "/a", { reachable: false, monitored: true, origin: "migration" })).map((b) => b[1])).toEqual([
      "root not found", "monitored", "made by the move to libraries",
    ]);
  });

  it("finds the library around another one", () => {
    const all = [lib(1, "D:\\STL"), lib(2, "d:/stl/Gambody"), lib(3, "D:/STLX"), lib(4, "/srv/stl")];
    expect(outerLibrary(all[1], all)?.id).toBe(1);
    expect(outerLibrary(all[2], all)).toBeUndefined();
    expect(outerLibrary(lib(5, "/srv/stl/a/b"), [...all, lib(6, "/srv/stl/a")])?.id).toBe(6);
    expect(outerLibrary(lib(7, "/SRV/stl/x"), all)).toBeUndefined();
  });

  it("explains removal and relocation", () => {
    const all = [lib(1, "/stl"), lib(2, "/stl/g")];
    expect(removalText(all[1], all)).toContain("move to the library L1");
    expect(removalText(all[0], all)).toContain("are deleted");
    expect(removalText(lib(3, "/x", { models: 0 }), all)).toContain("no records");
    const r = { library: 1, root: "/n", total: 3, found: 1, case_only: 1, missing: 1, missing_examples: [], inner: 1, applied: false };
    expect(relocationText(r)).toBe("1 of 3 archives found, 1 with another letter case, 1 missing, 1 inner library moves along");
  });
});
