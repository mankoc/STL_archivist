// The image grid: the browser's current models as thumbnails, loaded a page
// of ids at a time as they scroll into view. Its toolbar sizes the cards,
// sorts (the table's sort) and picks the lines below each card; a double click
// opens the model's images at full size.
import { ActionIcon, Box, Group, MultiSelect, Select, Slider, Tooltip } from "@mantine/core";
import { IconSortAscending, IconSortDescending } from "@tabler/icons-react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";

import { api, unwrap } from "../api/client";
import { type Cell, CellGrid } from "./CellGrid";
import {
  CARD_INFO, CARD_MAX, CARD_MIN, type CardInfo, GRID_CARD_KEY, GRID_PAGE, GRID_SORTS, loadCardInfo, loadCardWidth, pagesFor,
  saveCardInfo, saveCardWidth,
} from "./grid";
import { ImageViewer, type ViewerImage } from "./ImageViewer";
import type { Sort } from "./ModelTable";

export function GridView({ ids, selected, sort, onSort, onOpen, onContextMenu }: {
  ids: number[];
  selected: number | null;
  /** The browser's sort (shared with the table). */
  sort: Sort | null;
  onSort: (sort: Sort | null) => void;
  onOpen: (id: number) => void;
  onContextMenu?: (id: number, e: React.MouseEvent) => void;
}) {
  const queryClient = useQueryClient();
  const [cardWidth, setCardWidth] = useState(() => loadCardWidth(GRID_CARD_KEY));
  const [info, setInfo] = useState<CardInfo[]>(loadCardInfo);
  const [viewer, setViewer] = useState<{ images: ViewerImage[]; index: number } | null>(null);
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
  // The detail pane's query: its images, often cached already.
  const openImages = async (id: number) => {
    const d = await queryClient.fetchQuery({
      queryKey: ["model", id],
      queryFn: async () => unwrap(await api.GET("/api/models/{id}", { params: { path: { id } } })),
    });
    const images = d.images.flatMap((i) => (i.thumbnail ? [{ id: i.id, thumbnail: i.thumbnail }] : []));
    if (images.length) setViewer({ images, index: 0 });
  };

  return (
    <Box style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <Group gap="sm" px="xs" py={4} wrap="nowrap" data-testid="grid-toolbar">
        <Slider size="xs" w={140} min={CARD_MIN} max={CARD_MAX} step={20} value={cardWidth} label={(v) => `${v} px`}
          onChange={setCardWidth} onChangeEnd={(v) => saveCardWidth(v, GRID_CARD_KEY)} aria-label="Image size" data-testid="grid-size" />
        <Select size="xs" w={140} placeholder="Sort by" data={GRID_SORTS} value={sort?.column ?? null} clearable
          onChange={(v) => onSort(v ? { column: v, descending: sort?.descending ?? false } : null)} aria-label="Sort by" data-testid="grid-sort" />
        <Tooltip label={sort?.descending ? "Descending" : "Ascending"}>
          <ActionIcon variant="subtle" disabled={!sort} onClick={() => sort && onSort({ ...sort, descending: !sort.descending })}
            aria-label="Sort direction" data-testid="grid-sort-direction">
            {sort?.descending ? <IconSortDescending size={18} /> : <IconSortAscending size={18} />}
          </ActionIcon>
        </Tooltip>
        <MultiSelect size="xs" miw={180} maw={420} placeholder="Show below the images" data={CARD_INFO as unknown as { value: string; label: string }[]}
          value={info} clearable aria-label="Show below the images" data-testid="grid-info"
          onChange={(v) => {
            const shown = CARD_INFO.map((i) => i.value).filter((k) => v.includes(k));
            setInfo(shown);
            saveCardInfo(shown);
          }} />
      </Group>
      <Box style={{ flex: 1, minHeight: 0 }}>
        <CellGrid count={ids.length} cell={(i) => byId.get(ids[i])} cardWidth={cardWidth} info={info} selected={selected}
          onOpen={onOpen} onDoubleClick={(id) => void openImages(id)} onContextMenu={onContextMenu} onRange={onRange} />
      </Box>
      {viewer && (
        <ImageViewer images={viewer.images} index={viewer.index} onIndex={(index) => setViewer((v) => v && { ...v, index })} onClose={() => setViewer(null)} />
      )}
    </Box>
  );
}
