// Which table columns are shown, and in what order, from three per-user
// settings: the standard columns the user hid, the optional (off by default)
// columns the user turned on, and the order the user dragged them into. The
// title is always shown.

export const HIDDEN_COLUMNS_KEY = "table_hidden_columns";
export const SHOWN_COLUMNS_KEY = "table_shown_columns";
export const COLUMN_ORDER_KEY = "table_column_order";
export const ALWAYS_SHOWN = "title";

export interface ColumnChoice {
  /** Standard columns the user hid. */
  hidden: string[];
  /** Optional columns the user turned on. */
  shown: string[];
  /** Column ids in the user's order (none: the default order). */
  order: string[];
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
  const order = settings?.[COLUMN_ORDER_KEY];
  return {
    hidden: idsFrom(settings?.[HIDDEN_COLUMNS_KEY]),
    shown: idsFrom(settings?.[SHOWN_COLUMNS_KEY]),
    order: Array.isArray(order) ? order.filter((v): v is string => typeof v === "string") : [],
  };
}

/**
 * `columns` in the user's `order`: those it names first, in its order, then the
 * others (columns added since) in their default order.
 */
export function ordered<T extends Col>(columns: T[], order: string[]): T[] {
  const byId = new Map(columns.map((c) => [c.id, c]));
  const first = [...new Set(order)].flatMap((id) => byId.get(id) ?? []);
  const named = new Set(first.map((c) => c.id));
  return [...first, ...columns.filter((c) => !named.has(c.id))];
}

/**
 * The whole order (every column id) after the column `from` was dragged onto
 * `to`: it takes `to`'s place, and `to` moves aside toward where `from` was.
 */
export function moveColumn(columns: Col[], order: string[], from: string, to: string): string[] {
  const ids = ordered(columns, order).map((c) => c.id);
  const [a, b] = [ids.indexOf(from), ids.indexOf(to)];
  if (a < 0 || b < 0 || a === b) return ids;
  ids.splice(a, 1);
  ids.splice(b, 0, from);
  return ids;
}

export function isShown(c: Col, choice: ColumnChoice): boolean {
  if (c.id === ALWAYS_SHOWN) return true;
  return c.optional ? choice.shown.includes(c.id) : !choice.hidden.includes(c.id);
}

/** `columns` that are shown, in the user's order. */
export function visible<T extends Col>(columns: T[], choice: ColumnChoice): T[] {
  return ordered(columns, choice.order).filter((c) => isShown(c, choice));
}

function flip(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

/** The choice with column `c` shown or hidden. */
export function toggle(choice: ColumnChoice, c: Col): ColumnChoice {
  if (c.id === ALWAYS_SHOWN) return choice;
  return c.optional ? { ...choice, shown: flip(choice.shown, c.id) } : { ...choice, hidden: flip(choice.hidden, c.id) };
}
