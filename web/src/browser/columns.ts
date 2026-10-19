// Which table columns are shown, from two per-user settings: the standard
// columns the user hid, and the optional (off by default) columns the user
// turned on. The title is always shown.

export const HIDDEN_COLUMNS_KEY = "table_hidden_columns";
export const SHOWN_COLUMNS_KEY = "table_shown_columns";
export const ALWAYS_SHOWN = "title";

export interface ColumnChoice {
  /** Standard columns the user hid. */
  hidden: string[];
  /** Optional columns the user turned on. */
  shown: string[];
}

interface Col {
  id: string;
  optional?: boolean;
}

/** A stored setting value as a list of column ids (anything else: none). */
export function idsFrom(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string" && v !== ALWAYS_SHOWN) : [];
}

export function choiceFrom(settings: Record<string, unknown> | undefined): ColumnChoice {
  return { hidden: idsFrom(settings?.[HIDDEN_COLUMNS_KEY]), shown: idsFrom(settings?.[SHOWN_COLUMNS_KEY]) };
}

export function isShown(c: Col, choice: ColumnChoice): boolean {
  if (c.id === ALWAYS_SHOWN) return true;
  return c.optional ? choice.shown.includes(c.id) : !choice.hidden.includes(c.id);
}

/** `columns` that are shown, in their order. */
export function visible<T extends Col>(columns: T[], choice: ColumnChoice): T[] {
  return columns.filter((c) => isShown(c, choice));
}

function flip(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

/** The choice with column `c` shown or hidden. */
export function toggle(choice: ColumnChoice, c: Col): ColumnChoice {
  if (c.id === ALWAYS_SHOWN) return choice;
  return c.optional ? { ...choice, shown: flip(choice.shown, c.id) } : { ...choice, hidden: flip(choice.hidden, c.id) };
}
