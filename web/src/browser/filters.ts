// The browser's filter model, as the desktop's filter pane: each value cycles
// off → include (+) → exclude (−) → off; a section header cycles off → "has a
// value" → "has no value" → off. The state is the server's FilterSpecDto.
import type { Schemas } from "../api/client";

export type FilterSpec = Schemas["FilterSpecDto"];
export type FilterValue = Schemas["FilterValueDto"];
export type FilterTree = Schemas["FilterTree"];
export type Mark = "include" | "exclude" | undefined;
export type GroupMark = "has" | "no" | undefined;

export const EMPTY: FilterSpec = { include: {}, exclude: {}, has_value: [], no_value: [], ids: null };

const key = (v: FilterValue) => JSON.stringify(v);

function has(list: FilterValue[] | undefined, v: FilterValue): boolean {
  return (list ?? []).some((x) => key(x) === key(v));
}

function without(map: Record<string, FilterValue[]> | undefined, facet: string, v: FilterValue) {
  const next = { ...(map ?? {}) };
  const list = (next[facet] ?? []).filter((x) => key(x) !== key(v));
  if (list.length) next[facet] = list;
  else delete next[facet];
  return next;
}

function withValue(map: Record<string, FilterValue[]> | undefined, facet: string, v: FilterValue) {
  return { ...(map ?? {}), [facet]: [...((map ?? {})[facet] ?? []), v] };
}

export function markOf(spec: FilterSpec, facet: string, v: FilterValue): Mark {
  if (has(spec.include?.[facet], v)) return "include";
  if (has(spec.exclude?.[facet], v)) return "exclude";
  return undefined;
}

/** The spec after clicking a value: off → include → exclude → off. */
export function cycleValue(spec: FilterSpec, facet: string, v: FilterValue): FilterSpec {
  const mark = markOf(spec, facet, v);
  const include = without(spec.include, facet, v);
  const exclude = without(spec.exclude, facet, v);
  if (mark === undefined) return { ...spec, include: withValue(include, facet, v), exclude };
  if (mark === "include") return { ...spec, include, exclude: withValue(exclude, facet, v) };
  return { ...spec, include, exclude };
}

export function groupMarkOf(spec: FilterSpec, group: string): GroupMark {
  if (spec.has_value?.includes(group)) return "has";
  if (spec.no_value?.includes(group)) return "no";
  return undefined;
}

/** The spec after clicking a section header: off → has a value → no value → off. */
export function cycleGroup(spec: FilterSpec, group: string): FilterSpec {
  const mark = groupMarkOf(spec, group);
  const hasValue = (spec.has_value ?? []).filter((g) => g !== group);
  const noValue = (spec.no_value ?? []).filter((g) => g !== group);
  if (mark === undefined) return { ...spec, has_value: [...hasValue, group], no_value: noValue };
  if (mark === "has") return { ...spec, has_value: hasValue, no_value: [...noValue, group] };
  return { ...spec, has_value: hasValue, no_value: noValue };
}

export function isEmpty(spec: FilterSpec): boolean {
  return (
    !Object.keys(spec.include ?? {}).length &&
    !Object.keys(spec.exclude ?? {}).length &&
    !(spec.has_value ?? []).length &&
    !(spec.no_value ?? []).length &&
    spec.ids == null
  );
}

/** Whether a section has any value or group filter (its counts are then unscoped). */
export function sectionActive(spec: FilterSpec, facet: string): boolean {
  return facet in (spec.include ?? {}) || facet in (spec.exclude ?? {});
}

/**
 * The count shown beside a value: scoped by the other sections while a filter
 * is active. A section the server did not scope (the only active one: its own
 * selection does not narrow its own list) shows its unscoped counts.
 */
export function countOf(tree: FilterTree, kind: string, value: string): number | undefined {
  if (tree.scoped && kind in tree.scoped_counts) return tree.scoped_counts[kind]?.[value] ?? 0;
  return tree.counts[kind]?.[value];
}

export function groupCount(tree: FilterTree, group: string): number | undefined {
  const scoped = tree.scoped ? tree.scoped_counts.group?.[group] : undefined;
  return scoped ?? tree.counts.group?.[group];
}

export interface FolderNode {
  name: string;
  path: string;
  children: FolderNode[];
}

/** Folders (forward slashes) as a tree under their drive or root. */
export function folderTree(folders: string[]): FolderNode[] {
  const roots: FolderNode[] = [];
  const index = new Map<string, FolderNode>();
  for (const folder of [...folders].sort((a, b) => a.localeCompare(b))) {
    const parts = folder.replace(/\\/g, "/").split("/").filter((p, i) => p !== "" || i === 0);
    let path = "";
    let siblings = roots;
    parts.forEach((part, i) => {
      path = i === 0 ? part : `${path}/${part}`;
      let node = index.get(path);
      if (!node) {
        node = { name: part === "" ? "/" : part, path: path === "" ? "/" : path, children: [] };
        index.set(path, node);
        siblings.push(node);
      }
      siblings = node.children;
    });
  }
  return roots;
}

export function pathCount(tree: FilterTree, path: string): number | undefined {
  if (tree.scoped && tree.scoped_path_counts) return tree.scoped_path_counts[path] ?? 0;
  return tree.path_counts?.[path];
}
