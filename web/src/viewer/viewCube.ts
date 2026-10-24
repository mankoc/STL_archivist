// The view cube: a cube that turns with the camera, its faces named after the
// standard views. Clicking a face looks from that side; dragging the cube
// orbits the view. Pure projection and gesture rules here; the viewer draws it
// as SVG.
import type { ViewName } from "./input";

type Vec3 = [number, number, number];

/** Each face: the view it stands for, its outward normal, and its axis (for the tint). */
const FACES: { view: ViewName; label: string; normal: Vec3; axis: "x" | "y" | "z" }[] = [
  { view: "top", label: "Top", normal: [0, 0, 1], axis: "z" },
  { view: "bottom", label: "Bottom", normal: [0, 0, -1], axis: "z" },
  { view: "front", label: "Front", normal: [0, -1, 0], axis: "y" },
  { view: "back", label: "Back", normal: [0, 1, 0], axis: "y" },
  { view: "right", label: "Right", normal: [1, 0, 0], axis: "x" },
  { view: "left", label: "Left", normal: [-1, 0, 0], axis: "x" },
];

export interface CubeFace {
  view: ViewName;
  label: string;
  axis: "x" | "y" | "z";
  /** Corners on screen (y down), around the cube's centre. */
  points: [number, number][];
  centre: [number, number];
  /** How squarely the face looks at the camera, 0–1. */
  facing: number;
}

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/**
 * The faces turned toward the camera, least facing first, for a cube of
 * half-size `half` seen along `basis` (right, up and viewing directions,
 * 9 numbers, from `Viewer.camera_basis`).
 */
export function cubeFaces(basis: ArrayLike<number>, half: number): CubeFace[] {
  const right: Vec3 = [basis[0], basis[1], basis[2]];
  const up: Vec3 = [basis[3], basis[4], basis[5]];
  const forward: Vec3 = [basis[6], basis[7], basis[8]];
  const project = (p: Vec3): [number, number] => [dot(p, right) * half, -dot(p, up) * half];
  return FACES.map((f) => ({ f, facing: -dot(f.normal, forward) }))
    .filter(({ facing }) => facing > 1e-6)
    .sort((a, b) => a.facing - b.facing)
    .map(({ f, facing }) => {
      const n = f.normal;
      // The two axes along the face.
      const [a, b] = ([[1, 0, 0], [0, 1, 0], [0, 0, 1]] as Vec3[]).filter((e) => dot(e, n) === 0);
      const corner = (s: number, t: number): Vec3 => [0, 1, 2].map((k) => n[k] + s * a[k] + t * b[k]) as Vec3;
      return {
        view: f.view,
        label: f.label,
        axis: f.axis,
        points: [corner(-1, -1), corner(1, -1), corner(1, 1), corner(-1, 1)].map(project),
        centre: project(n),
        facing,
      };
    });
}

/** Pointer travel (CSS pixels) under which a press on the cube is a click on a face, not a drag. */
export const CLICK_SLOP = 4;

/** Whether a press that ended `dx`, `dy` from where it started was a click. */
export const isClick = (dx: number, dy: number) => Math.hypot(dx, dy) < CLICK_SLOP;
