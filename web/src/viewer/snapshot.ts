// The part of the canvas "Add this view" keeps: the shown objects with a margin
// around them, not the empty sides of the view.

/**
 * `[x, y, width, height]` in whole canvas pixels: `rect` (`x0, y0, x1, y1`, from
 * the viewer's `shown_rect`) grown by `margin` of its larger side on every side,
 * within the canvas; the whole canvas when `rect` is empty or has no area.
 */
export function snapshotCrop(rect: ArrayLike<number>, width: number, height: number, margin = 0.06): [number, number, number, number] {
  const [x0, y0, x1, y1] = Array.from(rect);
  if (rect.length < 4 || !(x1 > x0 && y1 > y0)) return [0, 0, width, height];
  const pad = Math.max(x1 - x0, y1 - y0) * margin;
  const left = Math.max(0, Math.floor(x0 - pad));
  const top = Math.max(0, Math.floor(y0 - pad));
  const right = Math.min(width, Math.ceil(x1 + pad));
  const bottom = Math.min(height, Math.ceil(y1 + pad));
  return [left, top, right - left, bottom - top];
}
