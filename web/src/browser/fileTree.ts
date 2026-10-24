// An archive's flat member list (`a/b/c.stl`) as a folder tree, for the Files
// window and the model viewer's file list (Mantine's Tree).
import type { TreeNodeData } from "@mantine/core";

export interface ArchiveEntry {
  name: string;
  size?: number | null;
  crc?: string;
}

/** A node's details: a file's member name, size and CRC; a folder's files (member names, at any depth) and total size. */
export interface NodeInfo {
  kind: "file" | "folder";
  /** A file's member name in the archive. */
  name?: string;
  size: number | null;
  crc?: string;
  /** A folder's files, in tree order. */
  files?: string[];
}

export interface FileTree {
  data: TreeNodeData[];
  /** By node value. */
  info: Map<string, NodeInfo>;
}

interface Dir {
  dirs: Map<string, Dir>;
  files: ArchiveEntry[];
}

const order = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
const leaf = (name: string) => name.split("/").filter(Boolean).pop() ?? name;

/**
 * Folders first, then files, each by name without regard to case (numbers in
 * order). A folder holding nothing but one folder is shown as one row
 * (`A / B`). Directory entries (`a/`) only make their folder; a name the
 * archive holds twice stays twice (node values are made unique).
 */
export function fileTree(entries: ArchiveEntry[]): FileTree {
  const root: Dir = { dirs: new Map(), files: [] };
  for (const e of entries) {
    const parts = e.name.split("/").filter(Boolean);
    if (!parts.length) continue;
    const isDir = e.name.endsWith("/");
    let dir = root;
    for (const p of isDir ? parts : parts.slice(0, -1)) {
      if (!dir.dirs.has(p)) dir.dirs.set(p, { dirs: new Map(), files: [] });
      dir = dir.dirs.get(p)!;
    }
    if (!isDir) dir.files.push(e);
  }
  const info = new Map<string, NodeInfo>();
  const used = new Set<string>();
  const build = (dir: Dir, prefix: string): TreeNodeData[] => {
    const folders = [...dir.dirs].sort(([a], [b]) => order(a, b)).map(([name, sub]) => {
      let label = name;
      let path = `${prefix}${name}/`;
      while (sub.files.length === 0 && sub.dirs.size === 1) {
        const [[next, inner]] = [...sub.dirs];
        label = `${label} / ${next}`;
        path = `${path}${next}/`;
        sub = inner;
      }
      const children = build(sub, path);
      const below = children.map((c) => info.get(c.value)!);
      info.set(path, {
        kind: "folder",
        files: below.flatMap((i) => (i.kind === "file" ? [i.name!] : i.files!)),
        size: below.reduce((n, i) => n + (i.size ?? 0), 0),
      });
      return { value: path, label, children };
    });
    const files = [...dir.files].sort((a, b) => order(leaf(a.name), leaf(b.name))).map((e) => {
      let value = e.name;
      for (let n = 2; used.has(value); n++) value = `${e.name}#${n}`;
      used.add(value);
      info.set(value, { kind: "file", name: e.name, size: e.size ?? null, crc: e.crc });
      return { value, label: leaf(e.name) };
    });
    return [...folders, ...files];
  };
  return { data: build(root, ""), info };
}

/** Every file's member name, in tree order. */
export function treeFiles(tree: FileTree): string[] {
  return tree.data.flatMap((n) => {
    const i = tree.info.get(n.value)!;
    return i.kind === "file" ? [i.name!] : i.files!;
  });
}

/** The folders (node values) holding `name`, outermost first. */
export function foldersOf(tree: FileTree, name: string): string[] {
  const out: string[] = [];
  const walk = (nodes: TreeNodeData[]): boolean =>
    nodes.some((n) => {
      const i = tree.info.get(n.value)!;
      if (i.kind === "file") return i.name === name;
      if (!i.files!.includes(name)) return false;
      out.push(n.value);
      return walk(n.children ?? []);
    });
  walk(tree.data);
  return out;
}
