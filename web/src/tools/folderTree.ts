// The Folders page's folders as a tree (Mantine's Tree): the folders holding
// archives (`/api/folders`: absolute, `/` separators) under the folders above
// them.
import type { TreeNodeData } from "@mantine/core";

export interface FolderTree {
  /** Node values are folder paths, as `/api/views/folder` takes them. */
  data: TreeNodeData[];
  /** The nodes with archives of their own (the others only have folders with archives below them). */
  archives: Set<string>;
}

interface Dir {
  dirs: Map<string, Dir>;
  /** The folder's spelling from the server, when archives are in it. */
  folder: string | null;
}

const order = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
const join = (a: string, b: string) => (a.endsWith("/") ? a + b : `${a}/${b}`);

/** A path's root (`F:`, `/mnt`, `//server`) and then each folder name. */
export function pathParts(path: string): string[] {
  const root = /^\/*[^/]*/.exec(path)![0];
  return [root, ...path.slice(root.length).split("/")].filter(Boolean);
}

/**
 * Folders by name without regard to case (numbers in order). A folder with no
 * archives of its own and a single folder in it is one row with that folder
 * (`F: / STL`), as in the Files window.
 */
export function folderTree(folders: string[]): FolderTree {
  const root: Dir = { dirs: new Map(), folder: null };
  for (const f of folders) {
    const parts = pathParts(f);
    if (!parts.length) continue;
    let dir = root;
    for (const p of parts) {
      if (!dir.dirs.has(p)) dir.dirs.set(p, { dirs: new Map(), folder: null });
      dir = dir.dirs.get(p)!;
    }
    dir.folder = f;
  }
  const archives = new Set<string>();
  const build = (dir: Dir, prefix: string | null): TreeNodeData[] =>
    [...dir.dirs].sort(([a], [b]) => order(a, b)).map(([name, sub]) => {
      let label = name;
      let path = prefix == null ? name : join(prefix, name);
      while (sub.folder == null && sub.dirs.size === 1) {
        const [[next, inner]] = [...sub.dirs];
        label = `${label} / ${next}`;
        path = join(path, next);
        sub = inner;
      }
      const value = sub.folder ?? path;
      if (sub.folder != null) archives.add(value);
      return { value, label, children: build(sub, path) };
    });
  return { data: build(root, null), archives };
}

/** The node values from the top down to `value`, itself included; empty when it is not in the tree. */
export function nodesTo(nodes: TreeNodeData[], value: string): string[] {
  for (const n of nodes) {
    if (n.value === value) return [value];
    const below = nodesTo(n.children ?? [], value);
    if (below.length) return [n.value, ...below];
  }
  return [];
}
