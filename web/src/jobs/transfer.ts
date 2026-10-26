// Moving a library to another server (Settings → Backups): exporting is a
// backup downloaded once written; importing maps the package's folders to
// roots on this server, by hand or with a "replace path start" rule
// (`D:\Minis` → `/library`), whichever system each side runs.
import type { Schemas } from "../api/client";

export type ImportPreview = Schemas["ImportPreview"];
export type ImportFolder = Schemas["ImportFolder"];

/** A Windows path: a drive letter or `\`. */
function isWindows(p: string): boolean {
  return /^[A-Za-z]:/.test(p) || p.includes("\\");
}

/**
 * `root` with its start `from` replaced by `to`, or `null` when `root` is not
 * `from` or below it. Windows paths compare without letter case; the rest
 * takes `to`'s separators.
 */
export function applyPrefix(root: string, from: string, to: string): string | null {
  if (!from.trim() || !to.trim()) return null;
  const norm = (p: string) => p.trim().replace(/\\/g, "/").replace(/\/+$/, "");
  const [r, f] = [norm(root), norm(from)];
  const same = (a: string, b: string) => (isWindows(root) ? a.toLowerCase() === b.toLowerCase() : a === b);
  let rest: string;
  if (same(r, f)) rest = "";
  else if (r.length > f.length + 1 && same(r.slice(0, f.length + 1), `${f}/`)) rest = r.slice(f.length + 1);
  else return null;
  const base = to.trim().replace(/[\\/]+$/, "");
  // A drive's or the file system's root keeps its separator.
  if (!rest) return base === "" || /^[A-Za-z]:$/.test(base) ? to.trim() : base;
  const sep = isWindows(to) ? "\\" : "/";
  return `${base}${sep}${rest.split("/").join(sep)}`;
}

/** The root a folder is shown with: the one typed, else its root here when that is a folder here. */
export function shownRoot(f: ImportFolder, roots: Record<string, string>): string {
  return roots[f.uuid] ?? (f.root_exists ? f.new_root : "");
}

/** `roots` with every folder below `from` moved below `to`. */
export function withRule(folders: ImportFolder[], roots: Record<string, string>, from: string, to: string): Record<string, string> {
  const out = { ...roots };
  for (const f of folders) {
    const mapped = applyPrefix(f.root, from, to);
    if (mapped !== null) out[f.uuid] = mapped;
  }
  return out;
}

/** What the import job is given: each folder's root here, when there is one. */
export function rootsToSend(folders: ImportFolder[], roots: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of folders) {
    const r = shownRoot(f, roots).trim();
    if (r) out[f.uuid] = r;
  }
  return out;
}

/** Whether the last check was of the roots shown now (else check again before importing). */
export function upToDate(folders: ImportFolder[], roots: Record<string, string>): boolean {
  const trim = (p: string) => p.trim().replace(/(.)[\\/]+$/, "$1");
  return folders.every((f) => trim(shownRoot(f, roots) || f.root) === trim(f.new_root));
}

/** What a check found for one folder. */
export function importFolderText(f: ImportFolder): string {
  if (!f.root_exists) return `${f.new_root || f.root} is not a folder here: its ${f.models} record${f.models === 1 ? "" : "s"} would show as missing`;
  const parts = [`${f.found + f.case_only} of ${f.models} archive${f.models === 1 ? "" : "s"} found`];
  if (f.case_only) parts.push(`${f.case_only} with another letter case`);
  if (f.missing) parts.push(`${f.missing} missing`);
  if (f.inner) parts.push(`${f.inner} inner folder${f.inner === 1 ? " moves" : "s move"} along`);
  return parts.join(", ");
}

/** The file a finished backup job wrote (its message is `<name>: <size> MB`). */
export function backupName(message: string | null | undefined): string | null {
  const m = /^(stlLibrary_backup_[\w.]+\.7z): /.exec(message ?? "");
  return m ? m[1] : null;
}
