// Table cells that show more than a field's text: the path and the URL.

/** The per-user setting (`/api/me/settings`): the Path column shows paths below their library folder. */
export const RELATIVE_PATHS = "relative_paths";

/** The Path column's text: the absolute path, or the path below the library folder
 * (`rel_path`, `/` form) spelled with the absolute path's separator. */
export function shownPath(archivePath: string | null | undefined, relPath: string, relative: boolean): string {
  if (!relative) return archivePath ?? "";
  return archivePath?.includes("\\") ? relPath.split("/").join("\\") : relPath;
}

/** Where a URL cell links to: web addresses only (anything else stays plain text). */
export function webHref(url: string | null | undefined): string | null {
  const u = url?.trim();
  if (!u) return null;
  try {
    const parsed = new URL(u);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}
