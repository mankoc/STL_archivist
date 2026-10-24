import type { TreeNodeData } from "@mantine/core";
import { describe, expect, it } from "vitest";

import { folderTree, nodesTo, pathParts } from "./folderTree";

/** label (value) and children, for comparing. */
const shape = (nodes: TreeNodeData[]): unknown[] =>
  nodes.map((n) => (n.children?.length ? [`${n.label} (${n.value})`, shape(n.children)] : `${n.label} (${n.value})`));

describe("folder tree", () => {
  it("splits paths at their root", () => {
    expect(pathParts("F:/STL/a b")).toEqual(["F:", "STL", "a b"]);
    expect(pathParts("/mnt/stl")).toEqual(["/mnt", "stl"]);
    expect(pathParts("//server/share/x")).toEqual(["//server", "share", "x"]);
    expect(pathParts("F:/")).toEqual(["F:"]);
    expect(pathParts("")).toEqual([]);
  });

  it("nests folders, in order, single-folder chains as one row", () => {
    const t = folderTree([
      "F:/STL/10 - Kickstarters/Beta",
      "F:/STL/9 - Patreon",
      "F:/STL/10 - Kickstarters/alpha/Phase 1",
      "F:/STL/10 - Kickstarters/alpha",
      "/mnt/minis/x",
    ]);
    expect(shape(t.data)).toEqual([
      "/mnt / minis / x (/mnt/minis/x)",
      [
        "F: / STL (F:/STL)",
        [
          "9 - Patreon (F:/STL/9 - Patreon)",
          [
            "10 - Kickstarters (F:/STL/10 - Kickstarters)",
            [["alpha (F:/STL/10 - Kickstarters/alpha)", ["Phase 1 (F:/STL/10 - Kickstarters/alpha/Phase 1)"]], "Beta (F:/STL/10 - Kickstarters/Beta)"],
          ],
        ],
      ],
    ]);
    expect([...t.archives].sort()).toEqual([
      "/mnt/minis/x",
      "F:/STL/10 - Kickstarters/Beta",
      "F:/STL/10 - Kickstarters/alpha",
      "F:/STL/10 - Kickstarters/alpha/Phase 1",
      "F:/STL/9 - Patreon",
    ]);
  });

  it("keeps the server's spelling of a folder with archives", () => {
    const t = folderTree(["F:/", "F:/x"]);
    expect(shape(t.data)).toEqual([["F: (F:/)", ["x (F:/x)"]]]);
  });

  it("finds the way down to a folder", () => {
    const t = folderTree(["F:/STL/a/b", "F:/STL/c"]);
    expect(nodesTo(t.data, "F:/STL/a/b")).toEqual(["F:/STL", "F:/STL/a/b"]);
    expect(nodesTo(t.data, "F:/nope")).toEqual([]);
  });
});
