import { describe, expect, it } from "vitest";

import { snapshotCrop } from "./snapshot";

describe("snapshot crop", () => {
  it("keeps the objects with a margin", () => {
    // 100 x 200 objects: 12 px (6 % of 200) around them.
    expect(snapshotCrop([400, 100, 500, 300], 1000, 600)).toEqual([388, 88, 124, 224]);
  });

  it("stays within the canvas", () => {
    expect(snapshotCrop([-0, 0, 1000, 590], 1000, 600)).toEqual([0, 0, 1000, 600]);
    expect(snapshotCrop([10.4, 5.6, 20.2, 15.1], 1000, 600, 0)).toEqual([10, 5, 11, 11]);
  });

  it("takes the whole canvas without objects", () => {
    expect(snapshotCrop([], 800, 600)).toEqual([0, 0, 800, 600]);
    expect(snapshotCrop([5, 5, 5, 9], 800, 600)).toEqual([0, 0, 800, 600]);
  });
});
