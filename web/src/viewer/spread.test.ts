import { describe, expect, it } from "vitest";

import { type Footprint, spreadOffsets } from "./spread";

const box = (name: string, x: number, y: number, w: number, d: number): Footprint => ({ name, lo: [x, y], hi: [x + w, y + d] });

/** Footprints after the offsets, as [x0, y0, x1, y1]. */
const placed = (files: Footprint[]) => {
  const off = spreadOffsets(files);
  return files.map((f) => [f.lo[0] + off[f.name][0], f.lo[1] + off[f.name][1], f.hi[0] + off[f.name][0], f.hi[1] + off[f.name][1]]);
};

describe("spreading files out", () => {
  it("leaves a single file where it is", () => {
    expect(spreadOffsets([box("a", 3, 4, 10, 10)])).toEqual({ a: [0, 0] });
  });

  it("puts two files side by side, centred where they were", () => {
    // Two 10 x 10 files on top of each other; a gap of 1 (10 % of 10).
    expect(spreadOffsets([box("a", 0, 0, 10, 10), box("b", 0, 0, 10, 10)])).toEqual({ a: [-5.5, 0], b: [5.5, 0] });
  });

  it("makes rows of about √n files that never overlap", () => {
    const files = ["a", "b", "c", "d", "e"].map((n, i) => box(n, 0, 0, 10 + i, 20 - i));
    const rects = placed(files);
    // 5 files: rows of 3, the second row in front of (below) the first.
    expect(rects[3][3]).toBeLessThan(Math.min(rects[0][1], rects[1][1], rects[2][1]));
    for (let i = 0; i < rects.length; i++)
      for (let j = i + 1; j < rects.length; j++) {
        const [a, b] = [rects[i], rects[j]];
        expect(a[2] <= b[0] || b[2] <= a[0] || a[3] <= b[1] || b[3] <= a[1]).toBe(true);
      }
  });
});
