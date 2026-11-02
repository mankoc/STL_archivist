// Suggestions for text boxes (SuggestInput): the database's values for a field,
// filtered by what has been typed. A list field (tags, scales) is
// comma-separated: the suggestions are for its last item.

/** How many suggestions are shown at most. */
export const SUGGEST_LIMIT = 50;

/** The `/api/editor/choices` list a table cell suggests from, and whether the field is a list. */
export const CELL_CHOICES: Record<string, { key: string; list?: boolean } | { values: string[] }> = {
  title: { key: "titles" },
  creator: { key: "creators" },
  source: { key: "sources" },
  date: { key: "dates" },
  sculptor: { key: "sculptors" },
  collection: { key: "collections" },
  subcollection: { key: "subcollections" },
  category: { key: "categories" },
  release: { key: "releases" },
  scale: { key: "scales", list: true },
  tags: { key: "tags", list: true },
  presupports: { values: ["No", "Presupported", "Lychee", "Chitubox"] },
};

/** The text being completed: the whole value, or a list's last item (trimmed). */
export function typedPart(value: string, list = false): string {
  return (list ? value.slice(value.lastIndexOf(",") + 1) : value).trim();
}

/**
 * The options matching what was typed (case-insensitively anywhere; those starting
 * with it first), without duplicates and, in a list, without the items already in
 * it. Nothing when the only match is exactly what was typed.
 */
export function suggestions(options: readonly string[], value: string, list = false, limit = SUGGEST_LIMIT): string[] {
  const typed = typedPart(value, list).toLowerCase();
  const taken = new Set(
    list
      ? value
          .split(",")
          .slice(0, -1)
          .map((t) => t.trim().toLowerCase())
      : [],
  );
  const starts: string[] = [];
  const contains: string[] = [];
  const seen = new Set<string>();
  for (const o of options) {
    const lower = o.toLowerCase();
    if (!o || seen.has(o) || taken.has(lower)) continue;
    seen.add(o);
    if (lower.startsWith(typed)) starts.push(o);
    else if (lower.includes(typed)) contains.push(o);
  }
  const out = [...starts, ...contains].slice(0, limit);
  return out.length === 1 && out[0].toLowerCase() === typed ? [] : out;
}

/** The value with `option` picked: it replaces the whole value, or a list's last item. */
export function applySuggestion(value: string, option: string, list = false): string {
  if (!list) return option;
  const comma = value.lastIndexOf(",");
  return comma < 0 ? option : `${value.slice(0, comma + 1)} ${option}`;
}
