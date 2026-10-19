// Which table columns are shown: the user's hidden list (a per-user setting),
// never hiding the title.

export const HIDDEN_COLUMNS_KEY = "table_hidden_columns";
export const ALWAYS_SHOWN = "title";

/** The hidden column ids from a stored setting value (anything else: none). */
export function hiddenFrom(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string" && v !== ALWAYS_SHOWN) : [];
}

/** `columns` without the hidden ones, in their order. */
export function visible<T extends { id: string }>(columns: T[], hidden: string[]): T[] {
  return columns.filter((c) => c.id === ALWAYS_SHOWN || !hidden.includes(c.id));
}

/** `hidden` with `id` shown or hidden. */
export function toggle(hidden: string[], id: string): string[] {
  if (id === ALWAYS_SHOWN) return hidden;
  return hidden.includes(id) ? hidden.filter((h) => h !== id) : [...hidden, id];
}
