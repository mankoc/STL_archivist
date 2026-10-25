// Table selection as in a desktop list: click selects one row, Ctrl/Cmd+click
// toggles a row, Shift+click selects the range from the anchor; a right-click
// acts on the selection it falls in, or on its own row.

export interface Selection {
  ids: Set<number>;
  /** The row Shift+click extends from (the last plain or Ctrl click). */
  anchor: number | null;
  /** The row whose details are shown (the last clicked). */
  current: number | null;
}

export const NONE: Selection = { ids: new Set(), anchor: null, current: null };

export function click(sel: Selection, order: number[], id: number, mods: { ctrl: boolean; shift: boolean }): Selection {
  if (mods.shift && sel.anchor != null) {
    const a = order.indexOf(sel.anchor);
    const b = order.indexOf(id);
    if (a >= 0 && b >= 0) {
      const [lo, hi] = a < b ? [a, b] : [b, a];
      const range = order.slice(lo, hi + 1);
      const ids = mods.ctrl ? new Set([...sel.ids, ...range]) : new Set(range);
      return { ids, anchor: sel.anchor, current: id };
    }
  }
  if (mods.ctrl) {
    const ids = new Set(sel.ids);
    if (ids.has(id)) ids.delete(id);
    else ids.add(id);
    return { ids, anchor: id, current: ids.has(id) ? id : sel.current === id ? null : sel.current };
  }
  return { ids: new Set([id]), anchor: id, current: id };
}

/** Keep only ids still in the table (after a filter or a delete). */
export function prune(sel: Selection, order: number[]): Selection {
  const present = new Set(order);
  const ids = new Set([...sel.ids].filter((i) => present.has(i)));
  if (ids.size === sel.ids.size) return sel;
  return {
    ids,
    anchor: sel.anchor != null && present.has(sel.anchor) ? sel.anchor : null,
    current: sel.current != null && present.has(sel.current) ? sel.current : null,
  };
}

/**
 * A right-click on row `id`: the selection after it and the rows its menu acts
 * on, in table order. Inside the selection it acts on the whole selection; on
 * any other row that row is selected alone first, as in a file manager.
 */
export function contextClick(sel: Selection, order: number[], id: number): { selection: Selection; ids: number[] } {
  if (sel.ids.has(id)) return { selection: sel, ids: order.filter((i) => sel.ids.has(i)) };
  return { selection: click(sel, order, id, { ctrl: false, shift: false }), ids: [id] };
}
