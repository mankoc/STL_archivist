// Path patterns: the tokens Sort files fills and path parsing reads, and the
// patterns a user saved (the `/api/me/settings` keys `SAVED_PATTERNS` and
// `PARSE_PATTERNS`).

/** Tokens and their aliases, as `path_pattern::token_field` and `sort_files::values` know them. */
export const TOKENS: { token: string; aliases: string[]; field: string }[] = [
  { token: "creator", aliases: ["author"], field: "Creator" },
  { token: "source", aliases: ["type"], field: "Type (shop or platform)" },
  { token: "title", aliases: ["name"], field: "Title" },
  { token: "date", aliases: ["month"], field: "Date: YYYY-MM, or YYYY" },
  { token: "sculptor", aliases: [], field: "Sculptor" },
  { token: "collection", aliases: ["category"], field: "Collection" },
  { token: "subcollection", aliases: ["subcategory"], field: "Subcollection" },
  { token: "release", aliases: [], field: "Release" },
  { token: "sequence", aliases: [], field: "Position in a series" },
  { token: "url", aliases: [], field: "URL" },
];

/** What Sort files does with fields without a value. */
export type MissingMode = "unknown" | "empty" | "text";
export interface MissingChoice {
  mode: MissingMode;
  text: string;
}

/** A remembered choice (anything else: the default, `unknown`). */
export function missingChoice(value: unknown): MissingChoice {
  const v = value as Partial<MissingChoice> | null | undefined;
  const mode = v?.mode === "empty" || v?.mode === "text" ? v.mode : "unknown";
  return { mode, text: typeof v?.text === "string" ? v.text : "" };
}

/** The request's `missing` (left out: the server's `_unknown`). */
export function missingValue({ mode, text }: MissingChoice): string | undefined {
  return mode === "unknown" ? undefined : mode === "empty" ? "" : text;
}

/** Sort files' patterns. */
export const SAVED_PATTERNS = "sort_patterns";
/** Patterns that parse values out of paths (mass edit). */
export const PARSE_PATTERNS = "parse_patterns";

/** The saved patterns in a settings value (anything else: none). */
export function savedPatterns(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((p): p is string => typeof p === "string" && p.trim() !== "") : [];
}

/** `list` with `pattern` first (moved there when already saved). */
export function withPattern(list: string[], pattern: string): string[] {
  const p = pattern.trim();
  return p ? [p, ...list.filter((x) => x !== p)] : list;
}

export function withoutPattern(list: string[], pattern: string): string[] {
  return list.filter((x) => x !== pattern);
}
