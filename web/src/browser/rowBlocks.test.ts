import { BLOCK, blockIds, blocksFor } from "./rowBlocks";

describe("row blocks", () => {
  it("covers the visible range", () => {
    expect(blocksFor(0, 20, 1000)).toEqual([0]);
    expect(blocksFor(95, 105, 1000)).toEqual([0, 1]);
    expect(blocksFor(250, 420, 1000)).toEqual([2, 3, 4]);
  });

  it("clamps to the row count", () => {
    expect(blocksFor(0, 500, 150)).toEqual([0, 1]);
    expect(blocksFor(0, 10, 0)).toEqual([]);
    expect(blocksFor(10, 5, 100)).toEqual([]);
  });

  it("slices ids per block", () => {
    const ids = Array.from({ length: 250 }, (_, i) => i + 1);
    expect(blockIds(ids, 0)).toHaveLength(BLOCK);
    expect(blockIds(ids, 2)).toEqual(ids.slice(200));
  });
});
