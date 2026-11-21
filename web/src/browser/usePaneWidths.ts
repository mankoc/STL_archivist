// The browser's side panes' widths, dragged by their inner edges and remembered
// in this browser (paneWidth.ts).
import { type PointerEvent as ReactPointerEvent, type RefObject, useState } from "react";

import { clampWidth, DEFAULT_WIDTH, FILTER_PANE, loadWidth, saveWidth } from "./paneWidth";

export type Pane = "filter" | "detail";

export function usePaneWidths(layoutRef: RefObject<HTMLDivElement | null>) {
  const [detailWidth, setDetailWidth] = useState(loadWidth);
  const [filterWidth, setFilterWidth] = useState(() => loadWidth(FILTER_PANE.key, FILTER_PANE.fallback, FILTER_PANE.min));
  const [dragging, setDragging] = useState<Pane | null>(null);
  const keep = (pane: Pane, width: number) => {
    if (pane === "filter") {
      setFilterWidth(width);
      saveWidth(width, FILTER_PANE.key);
    } else {
      setDetailWidth(width);
      saveWidth(width);
    }
  };
  const startDrag = (pane: Pane) => (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const layout = layoutRef.current;
    if (!layout) return;
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    setDragging(pane);
    let width = pane === "filter" ? filterWidth : detailWidth;
    // While dragging, only the layout's width variable changes: the panes are laid out
    // again, but not rendered again on every move. The state follows on release.
    const move = (ev: PointerEvent) => {
      const box = layout.getBoundingClientRect();
      if (pane === "filter") {
        width = clampWidth(ev.clientX - box.left, box.width, FILTER_PANE.maxShare, FILTER_PANE.min);
        layout.style.setProperty("--filter-width", `${width}px`);
      } else {
        width = clampWidth(box.right - ev.clientX, box.width);
        layout.style.setProperty("--detail-width", `${width}px`);
      }
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      setDragging(null);
      keep(pane, width);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
  };
  /** Back to the default width (a double-click on the edge). */
  const reset = (pane: Pane) => keep(pane, pane === "filter" ? FILTER_PANE.fallback : DEFAULT_WIDTH);
  return { detailWidth, filterWidth, dragging, startDrag, reset };
}
