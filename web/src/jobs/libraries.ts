// Libraries (Jobs → Libraries): how a library's state, its removal and a
// relocation check are described.
import type { Schemas } from "../api/client";

export type LibraryInfo = Schemas["LibraryInfo"];
export type Relocation = Schemas["Relocation"];

/** The badges a library shows: `[colour, label]`. */
export function libraryBadges(l: LibraryInfo): [string, string][] {
  const out: [string, string][] = [];
  if (!l.reachable) out.push(["red", "root not found"]);
  if (l.monitored) out.push(["blue", "monitored"]);
  if (l.origin === "migration") out.push(["gray", "made by the move to libraries"]);
  return out;
}

/** A root compared as paths are: `/` separators, no trailing one, letter case
 * ignored for Windows paths (a drive letter or `\`). */
function pathKey(p: string): string {
  const windows = /^[a-z]:/i.test(p) || p.includes("\\");
  const s = p.replace(/\\/g, "/").replace(/\/+$/, "");
  return windows ? s.toLowerCase() : s;
}

/** The deepest other library whose root contains `l`'s (it takes `l`'s records when `l` is removed). */
export function outerLibrary(l: LibraryInfo, all: LibraryInfo[]): LibraryInfo | undefined {
  const me = pathKey(l.root);
  return all
    .filter((o) => o.id !== l.id && o.root !== "" && me.startsWith(`${pathKey(o.root)}/`))
    .sort((a, b) => pathKey(b.root).length - pathKey(a.root).length)[0];
}

/** What removing `l` does, in words. */
export function removalText(l: LibraryInfo, all: LibraryInfo[]): string {
  const n = `${l.models} record${l.models === 1 ? "" : "s"}`;
  const outer = outerLibrary(l, all);
  if (l.models === 0) return "It has no records. The folder and its files are not touched.";
  return outer
    ? `Its ${n} move to the library ${outer.name}, which contains its folder. The archive files are not touched.`
    : `Its ${n} are deleted from the library, with their tags and images. The archive files are not touched.`;
}

/** A relocation check's outcome in one line. */
export function relocationText(r: Relocation): string {
  const parts = [`${r.found} of ${r.total} archive${r.total === 1 ? "" : "s"} found`];
  if (r.case_only) parts.push(`${r.case_only} with another letter case`);
  if (r.missing) parts.push(`${r.missing} missing`);
  if (r.inner) parts.push(`${r.inner} inner librar${r.inner === 1 ? "y moves" : "ies move"} along`);
  return parts.join(", ");
}
