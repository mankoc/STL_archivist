import { describe, expect, it } from "vitest";

import { applyPrefix, backupName, type ImportFolder, importFolderText, rootsToSend, shownRoot, upToDate, withRule } from "./transfer";

const folder = (over: Partial<ImportFolder>): ImportFolder => ({
  uuid: "u1",
  name: "Minis",
  library: "Minis",
  root: "D:\\Minis",
  new_root: "D:\\Minis",
  root_exists: false,
  models: 3,
  inner: 0,
  found: 0,
  case_only: 0,
  missing: 3,
  missing_examples: [],
  ...over,
});

describe("applyPrefix", () => {
  it("moves Windows roots below a POSIX folder, ignoring letter case", () => {
    expect(applyPrefix("D:\\Minis\\Studio", "d:\\minis", "/library")).toBe("/library/Studio");
    expect(applyPrefix("D:\\Minis", "D:\\Minis\\", "/library/")).toBe("/library");
    expect(applyPrefix("D:\\Minis2", "D:\\Minis", "/library")).toBeNull();
  });

  it("moves POSIX roots to Windows ones, keeping letter case", () => {
    expect(applyPrefix("/srv/STL/a/b", "/srv/STL", "E:\\Archive")).toBe("E:\\Archive\\a\\b");
    expect(applyPrefix("/srv/stl/a", "/srv/STL", "E:\\Archive")).toBeNull();
    expect(applyPrefix("/srv/STL/a", "/", "/mnt")).toBe("/mnt/srv/STL/a");
  });

  it("needs both sides", () => {
    expect(applyPrefix("/srv/STL", "", "/x")).toBeNull();
    expect(applyPrefix("/srv/STL", "/srv", " ")).toBeNull();
  });
});

describe("roots", () => {
  const here = folder({ uuid: "u2", root: "/srv/Other", new_root: "/srv/Other", root_exists: true });
  const away = folder({});

  it("shows a typed root, else the old one only when it exists here", () => {
    expect(shownRoot(here, {})).toBe("/srv/Other");
    expect(shownRoot(away, {})).toBe("");
    expect(shownRoot(away, { u1: "/library" })).toBe("/library");
  });

  it("knows when the roots shown are the ones checked", () => {
    expect(upToDate([away, here], {})).toBe(true);
    expect(upToDate([away], { u1: "/library" })).toBe(false);
    expect(upToDate([folder({ new_root: "/library", root_exists: true })], { u1: "/library/" })).toBe(true);
  });

  it("fills the folders a rule matches and sends what is shown", () => {
    const roots = withRule([away, here], {}, "D:\\", "/library");
    expect(roots).toEqual({ u1: "/library/Minis" });
    expect(rootsToSend([away, here], roots)).toEqual({ u1: "/library/Minis", u2: "/srv/Other" });
    expect(rootsToSend([away], { u1: "  " })).toEqual({});
  });
});

describe("texts", () => {
  it("describes a check", () => {
    expect(importFolderText(folder({ root_exists: true, found: 1, case_only: 1, missing: 1, inner: 2 }))).toBe(
      "2 of 3 archives found, 1 with another letter case, 1 missing, 2 inner folders move along",
    );
    expect(importFolderText(folder({}))).toBe("D:\\Minis is not a folder here: its 3 records would show as missing");
  });

  it("reads the file a backup job wrote", () => {
    expect(backupName("stlLibrary_backup_20261026_101500.7z: 12.5 MB")).toBe("stlLibrary_backup_20261026_101500.7z");
    expect(backupName("could not back up")).toBeNull();
    expect(backupName(undefined)).toBeNull();
  });
});
