// "Spread out": the shown files side by side on the XY plane, in rows of about
// √n files, centred where they were together. Each file moves as a whole (its
// supports with it); heights do not change.

/** Where a file places itself, seen from above: its lowest and highest X and Y. */
export interface Footprint {
  name: string;
  lo: [number, number];
  hi: [number, number];
}

/** Each file's offset (dx, dy) from where it places itself; none for a single file. */
export function spreadOffsets(files: Footprint[]): Record<string, [number, number]> {
  if (files.length < 2) return Object.fromEntries(files.map((f) => [f.name, [0, 0]]));
  const sizes = files.map((f) => [f.hi[0] - f.lo[0], f.hi[1] - f.lo[1]] as const);
  const gap = Math.max(1, 0.1 * Math.max(...sizes.flat()));
  const perRow = Math.ceil(Math.sqrt(files.length));
  // Rows run along X from the back (higher Y, the top of a top view) to the front.
  const corners: [number, number][] = [];
  let [x, y, depth] = [0, 0, 0];
  sizes.forEach(([w, d], i) => {
    if (i > 0 && i % perRow === 0) [x, y, depth] = [0, y - depth - gap, 0];
    corners.push([x, y - d]);
    x += w + gap;
    depth = Math.max(depth, d);
  });
  const centre = (lo: number[][], hi: number[][], axis: 0 | 1) =>
    (Math.min(...lo.map((p) => p[axis])) + Math.max(...hi.map((p) => p[axis]))) / 2;
  const placedHi = corners.map(([cx, cy], i) => [cx + sizes[i][0], cy + sizes[i][1]]);
  const shift = ([0, 1] as const).map(
    (a) => centre(files.map((f) => f.lo), files.map((f) => f.hi), a) - centre(corners, placedHi, a),
  );
  return Object.fromEntries(
    files.map((f, i) => [f.name, [corners[i][0] + shift[0] - f.lo[0], corners[i][1] + shift[1] - f.lo[1]]]),
  );
}
