// The search box's options: the fields searched and fuzzy matching (kept in
// this browser's localStorage).

/** The fields the server can search, in menu order, with their labels. */
export const SEARCH_FIELDS: { value: string; label: string }[] = [
  { value: "title", label: "Title" },
  { value: "creator", label: "Creator" },
  { value: "source", label: "Source" },
  { value: "sculptor", label: "Sculptor" },
  { value: "collection", label: "Collection" },
  { value: "subcollection", label: "Subcollection" },
  { value: "category", label: "Category" },
  { value: "release", label: "Release" },
  { value: "path", label: "Path" },
  { value: "tags", label: "Tags" },
  { value: "description", label: "Description" },
  { value: "url", label: "URL" },
];

/** What the server searches when given no fields. */
export const DEFAULT_FIELDS = ["title", "creator", "sculptor", "collection", "path", "tags"];

export interface SearchOptions {
  fields: string[];
  fuzzy: boolean;
}

export const DEFAULT_OPTIONS: SearchOptions = { fields: DEFAULT_FIELDS, fuzzy: false };

const KEY = "stl.search";

/** Stored options, keeping only known fields (none left: the defaults). */
export function parseOptions(raw: string | null): SearchOptions {
  try {
    const v = JSON.parse(raw ?? "") as Partial<SearchOptions>;
    const known = new Set(SEARCH_FIELDS.map((f) => f.value));
    const fields = Array.isArray(v.fields) ? SEARCH_FIELDS.map((f) => f.value).filter((f) => known.has(f) && v.fields!.includes(f)) : [];
    return { fields: fields.length ? fields : DEFAULT_FIELDS, fuzzy: v.fuzzy === true };
  } catch {
    return DEFAULT_OPTIONS;
  }
}

export function loadOptions(): SearchOptions {
  try {
    return parseOptions(localStorage.getItem(KEY));
  } catch {
    return DEFAULT_OPTIONS;
  }
}

export function saveOptions(o: SearchOptions) {
  try {
    localStorage.setItem(KEY, JSON.stringify(o));
  } catch {
    // Only a convenience.
  }
}

/** The search box's placeholder: "Search title, creator…" or "Fuzzy search …". */
export function placeholder(o: SearchOptions): string {
  const labels = SEARCH_FIELDS.filter((f) => o.fields.includes(f.value)).map((f) => f.label.toLowerCase());
  const shown = labels.length > 4 ? `${labels.slice(0, 4).join(", ")} +${labels.length - 4}` : labels.join(", ");
  return `${o.fuzzy ? "Fuzzy search" : "Search"} ${shown}`;
}

/** Whether the options differ from the defaults (the button is highlighted then). */
export function customised(o: SearchOptions): boolean {
  return o.fuzzy || o.fields.length !== DEFAULT_FIELDS.length || o.fields.some((f) => !DEFAULT_FIELDS.includes(f));
}
