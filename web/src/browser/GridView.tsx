// The image grid: the browser's current models as thumbnails, loaded a page
// of ids at a time as they scroll into view.
import { useQueries } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";

import { api, unwrap } from "../api/client";
import { type Cell, CellGrid } from "./CellGrid";
import { GRID_PAGE, pagesFor } from "./grid";

export function GridView({ ids, selected, onOpen }: { ids: number[]; selected: number | null; onOpen: (id: number) => void }) {
  const [pages, setPages] = useState<number[]>([0]);
  const onRange = useCallback(
    (first: number, last: number, cols: number) => {
      const want = pagesFor(first, last, cols, ids.length);
      setPages((old) => (want.join() === old.join() ? old : want));
    },
    [ids.length],
  );
  const results = useQueries({
    queries: pages.map((p) => {
      const pageIds = ids.slice(p * GRID_PAGE, (p + 1) * GRID_PAGE);
      return {
        queryKey: ["grid", pageIds],
        queryFn: async () => unwrap(await api.POST("/api/views/grid", { body: { ids: pageIds } })),
        enabled: pageIds.length > 0,
        staleTime: 30_000,
      };
    }),
  });
  const byId = useMemo(() => {
    const m = new Map<number, Cell>();
    for (const r of results) for (const c of r.data ?? []) m.set(c.id, c);
    return m;
  }, [results]);
  return <CellGrid count={ids.length} cell={(i) => byId.get(ids[i])} selected={selected} onOpen={onOpen} onRange={onRange} />;
}
