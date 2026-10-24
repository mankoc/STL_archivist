// The model viewer's controls, as MeshPeek's: left drag orbits (turntable),
// middle or Shift + left drag pans, right drag or the wheel zooms; F frames
// everything and 0–6 pick the standard views. Pixels are canvas pixels.

/** Model files the viewer shows (MeshPeek's `SUPPORTED_EXTENSIONS`). */
export const VIEWABLE_EXTENSIONS = ["stl", "obj", "ply", "3mf", "lys", "chitubox", "ctp", "ctb"];

export function isViewable(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && VIEWABLE_EXTENSIONS.includes(name.slice(dot + 1).toLowerCase());
}

export type DragAction = "orbit" | "pan" | "zoom";

/** What a drag with these `PointerEvent.buttons` does. */
export function dragAction(buttons: number, shift: boolean): DragAction | null {
  if (buttons & 4 || (buttons & 1 && shift)) return "pan";
  if (buttons & 1) return "orbit";
  if (buttons & 2) return "zoom";
  return null;
}

/** Turntable orbit for a drag: a full-width drag turns 200 degrees, like VTK. */
export function orbitDegrees(dx: number, dy: number, width: number, height: number): [number, number] {
  return [(-200 * dx) / Math.max(width, 1), (-200 * dy) / Math.max(height, 1)];
}

/** Zoom factor of a right drag (up zooms in). */
export const dragZoom = (dy: number) => 1.01 ** -dy;

/** Zoom factor of a wheel turn (`WheelEvent.deltaY` in its `deltaMode`; up zooms in). */
export function wheelZoom(deltaY: number, deltaMode: number): number {
  const pixels = deltaY * (deltaMode === 1 ? 16 : deltaMode === 2 ? 400 : 1);
  return 1.1 ** (-pixels / 50);
}

export const VIEWS = [
  ["isometric", "Isometric"],
  ["front", "Front"],
  ["back", "Back"],
  ["left", "Left"],
  ["right", "Right"],
  ["top", "Top"],
  ["bottom", "Bottom"],
] as const;

export type ViewName = (typeof VIEWS)[number][0];

/** The key's action: frame everything, or a standard view (its number). */
export function keyAction(key: string): "fit" | ViewName | null {
  if (key === "f" || key === "F") return "fit";
  const n = key.length === 1 ? "0123456".indexOf(key) : -1;
  return n >= 0 ? VIEWS[n][0] : null;
}
