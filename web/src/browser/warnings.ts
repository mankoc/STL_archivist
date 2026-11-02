// Highlighting rows by their stored warnings (Settings → Appearance).

/** The per-user setting (`/api/me/settings`): highlight rows with warnings. */
export const HIGHLIGHT_WARNINGS = "highlight_warnings";

/** `stl_core::storage::crawl::WARNING_UNREADABLE`. */
export const UNREADABLE = "Unreadable archive";

export type Tone = "unreadable" | "warning";

/** A row's colour from its warnings (one per line): red for an unreadable archive, else yellow. */
export function rowTone(warning: string | null | undefined): Tone | null {
  const lines = (warning ?? "").split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length === 0) return null;
  return lines.some((l) => l.startsWith(UNREADABLE)) ? "unreadable" : "warning";
}
