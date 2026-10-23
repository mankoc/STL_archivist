// Libraries (the Libraries page): how a library's folders, their state, their
// removal and a relocation check are described.
import type { Schemas } from "../api/client";

export type LibraryInfo = Schemas["LibraryInfo"];
export type LibraryFolder = Schemas["LibraryFolder"];
export type Relocation = Schemas["Relocation"];

/** The badges a folder shows: `[colour, label]`. */
export function folderBadges(f: LibraryFolder): [string, string][] {
  const out: [string, string][] = [];
  if (!f.reachable) out.push(["red", "root not found"]);
  if (f.monitored) out.push(["blue", "monitored"]);
  if (f.origin === "migration") out.push(["gray", "made by the move to libraries"]);
  return out;
}

/** A root compared as paths are: `/` separators, no trailing one, letter case
 * ignored for Windows paths (a drive letter or `\`). */
export function pathKey(p: string): string {
  const windows = /^[a-z]:/i.test(p) || p.includes("\\");
  const s = p.replace(/\\/g, "/").replace(/\/+$/, "");
  return windows ? s.toLowerCase() : s;
}

/** Every folder of every library, with its library. */
export function allFolders(libs: LibraryInfo[]): { folder: LibraryFolder; library: LibraryInfo }[] {
  return libs.flatMap((library) => library.folders.map((folder) => ({ folder, library })));
}

/** The deepest other folder whose root contains `f`'s (it takes `f`'s records when `f` is removed). */
export function outerFolder(f: LibraryFolder, libs: LibraryInfo[]): { folder: LibraryFolder; library: LibraryInfo } | undefined {
  const me = pathKey(f.root);
  return allFolders(libs)
    .filter(({ folder: o }) => o.id !== f.id && o.root !== "" && me.startsWith(`${pathKey(o.root)}/`))
    .sort((a, b) => pathKey(b.folder.root).length - pathKey(a.folder.root).length)[0];
}

const records = (n: number) => `${n} record${n === 1 ? "" : "s"}`;

/** What removing the folder `f` of `lib` does, in words. */
export function folderRemovalText(f: LibraryFolder, lib: LibraryInfo, libs: LibraryInfo[]): string {
  const outer = outerFolder(f, libs);
  const last = lib.folders.length === 1 ? ` It is the only folder of ${lib.name}, so the library goes too.` : "";
  const what =
    f.models === 0
      ? "It has no records."
      : outer
        ? `Its ${records(f.models)} ${f.models === 1 ? "moves" : "move"} to the folder ${outer.folder.root} of ${outer.library.name}, which contains it.`
        : `Its ${records(f.models)} ${f.models === 1 ? "is" : "are"} deleted from the library, with tags and images.`;
  return `${what}${last} The archive files are not touched.`;
}

/** What removing the whole library `lib` does, in words. */
export function libraryRemovalText(lib: LibraryInfo, libs: LibraryInfo[]): string {
  const others = libs.filter((l) => l.id !== lib.id);
  const kept = lib.folders.filter((f) => outerFolder(f, others)).reduce((n, f) => n + f.models, 0);
  const folders = `${lib.folders.length} folder${lib.folders.length === 1 ? "" : "s"}`;
  if (lib.models === 0) return `Its ${folders} go; it has no records. The archive files are not touched.`;
  const gone = lib.models - kept;
  const parts = [];
  if (gone) parts.push(`${records(gone)} ${gone === 1 ? "is" : "are"} deleted from the library, with tags and images`);
  if (kept) parts.push(`${records(kept)} ${kept === 1 ? "moves" : "move"} to folders of other libraries around them`);
  return `Its ${folders} go: ${parts.join("; ")}. The archive files are not touched.`;
}

/** Where a folder of `lib` can move: the other libraries, as `Select` data. */
export function moveTargets(lib: LibraryInfo, libs: LibraryInfo[]): { value: string; label: string }[] {
  return libs.filter((l) => l.id !== lib.id).map((l) => ({ value: String(l.id), label: l.name }));
}

/** What moving a folder out of `lib` does, in words. */
export function folderMoveText(lib: LibraryInfo): string {
  const left = lib.folders.length === 1 ? ` It is the only folder of ${lib.name}, so that library goes.` : "";
  return `Its records go along with their edits, tags and thumbnails; nothing is re-read.${left}`;
}

/** The folder of `lib` at `path` (the one just added), else its last one. */
export function folderAt(lib: LibraryInfo, path: string): LibraryFolder | undefined {
  return lib.folders.find((f) => pathKey(f.root) === pathKey(path)) ?? lib.folders[lib.folders.length - 1];
}

/** A relocation check's outcome in one line. */
export function relocationText(r: Relocation): string {
  const parts = [`${r.found} of ${r.total} archive${r.total === 1 ? "" : "s"} found`];
  if (r.case_only) parts.push(`${r.case_only} with another letter case`);
  if (r.missing) parts.push(`${r.missing} missing`);
  if (r.inner) parts.push(`${r.inner} inner folder${r.inner === 1 ? " moves" : "s move"} along`);
  return parts.join(", ");
}
