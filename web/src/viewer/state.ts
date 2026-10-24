// The model viewer's state as pure functions: which files are shown, and how
// each is drawn (MeshPeek's colours and materials).

/** MeshPeek's material presets. */
export const MATERIALS = ["Matte", "Plastic", "Glossy", "Metal"] as const;
export type Material = (typeof MATERIALS)[number];

/** MeshPeek's "Colour each model" colours, offered for colouring a file. */
export const COLORS = ["#4e9ae6", "#e66a4e", "#5cc27a", "#e6c24e", "#b06ae6", "#4ed6d6", "#e66ab0", "#a0a8b4"];

/** How a shown file is drawn; `color` null: MeshPeek's default (or "Colour each model"). */
export interface FileStyle {
  color: string | null;
  material: Material;
  wire: boolean;
}

export const DEFAULT_STYLE: FileStyle = { color: null, material: "Plastic", wire: false };

export const styleOf = (styles: Record<string, FileStyle>, name: string) => styles[name] ?? DEFAULT_STYLE;

/** `styles` with `patch` applied to each of `names`. */
export function restyle(styles: Record<string, FileStyle>, names: string[], patch: Partial<FileStyle>): Record<string, FileStyle> {
  const out = { ...styles };
  for (const n of names) out[n] = { ...styleOf(styles, n), ...patch };
  return out;
}

/** Add `name` to the shown files, or take it away. */
export function toggleFile(shown: string[], name: string): string[] {
  return shown.includes(name) ? shown.filter((n) => n !== name) : [...shown, name];
}

/** A folder's files: all shown already → none of them; else all of them added. */
export function toggleFiles(shown: string[], names: string[]): string[] {
  if (names.length && names.every((n) => shown.includes(n))) return shown.filter((n) => !names.includes(n));
  return [...shown, ...names.filter((n) => !shown.includes(n))];
}

/** Whether a folder's files are all shown, some, or none. */
export function folderState(shown: string[], names: string[]): "all" | "some" | "none" {
  const n = names.filter((f) => shown.includes(f)).length;
  return n === 0 ? "none" : n === names.length ? "all" : "some";
}

/** The file `delta` steps from `current` in `order`, stopping at the ends; the first without a current one. */
export function stepFile(order: string[], current: string | null, delta: number): string | null {
  if (!order.length) return null;
  const at = current == null ? -1 : order.indexOf(current);
  if (at < 0) return order[0];
  return order[Math.min(order.length - 1, Math.max(0, at + delta))];
}
