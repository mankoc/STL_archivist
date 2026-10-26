// A virtualised grid of model thumbnails (the image grid and the archive
// browser). Cells not loaded yet render as placeholders.
import { Box, Card, Image, Skeleton, Text, UnstyledButton } from "@mantine/core";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef, useState } from "react";

import type { Schemas } from "../api/client";
import { baseName } from "../format";
import { CARD_GAP, CARD_WIDTH, cardLayout, columnsFor } from "./grid";

export type Cell = Schemas["Cell"];

export function CellGrid({ count, cell, cardWidth = CARD_WIDTH, selected, onOpen, onContextMenu, onRange }: {
  count: number;
  /** Card width in pixels (images and rows scale with it). */
  cardWidth?: number;
  cell: (index: number) => Cell | undefined;
  selected?: number | null;
  onOpen: (id: number) => void;
  /** A cell was right-clicked (none: the browser's own menu). */
  onContextMenu?: (id: number, e: React.MouseEvent) => void;
  /** Rows first..last are visible, in `cols` columns (to load their cells). */
  onRange?: (first: number, last: number, cols: number) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth - 16));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const cols = columnsFor(width, cardWidth);
  const rows = Math.ceil(count / cols);
  const layout = cardLayout(cardWidth);
  const virtualizer = useVirtualizer({ count: rows, getScrollElement: () => scrollRef.current, estimateSize: () => layout.row, overscan: 3 });
  // A new card size changes every row's height.
  useEffect(() => virtualizer.measure(), [virtualizer, layout.row]);
  const items = virtualizer.getVirtualItems();
  const first = items[0]?.index ?? 0;
  const last = items[items.length - 1]?.index ?? -1;
  useEffect(() => onRange?.(first, last, cols), [first, last, cols, onRange]);

  return (
    <div ref={scrollRef} style={{ height: "100%", overflowY: "auto", padding: 8 }} data-testid="cell-grid">
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {items.map((row) => (
          <div key={row.key} style={{ position: "absolute", top: row.start, left: 0, right: 0, display: "flex", gap: CARD_GAP }}>
            {Array.from({ length: cols }, (_, c) => row.index * cols + c)
              .filter((i) => i < count)
              .map((i) => {
                const m = cell(i);
                return (
                  <Box key={i} w={cardWidth}>
                    {m ? (
                      <UnstyledButton
                        onClick={() => onOpen(m.id)}
                        onContextMenu={onContextMenu && ((e) => {
                          e.preventDefault();
                          onContextMenu(m.id, e);
                        })}
                        w="100%"
                        data-testid="cell"
                      >
                        <Card withBorder p={6} style={selected === m.id ? { outline: "2px solid var(--mantine-primary-color-filled)" } : undefined}>
                          {m.thumbnail ? (
                            <Image src={m.thumbnail} h={layout.image} fit="contain" radius="md" alt="" loading="lazy" />
                          ) : (
                            <Box h={layout.image} bg="var(--mantine-color-default-hover)" style={{ borderRadius: 4 }} />
                          )}
                          <Text size="sm" truncate mt={4}>{m.title || baseName(m.archive_path)}</Text>
                          <Text size="xs" c="dimmed" truncate>{m.creator || " "}</Text>
                        </Card>
                      </UnstyledButton>
                    ) : (
                      <Skeleton h={layout.row - 16} radius="md" />
                    )}
                  </Box>
                );
              })}
          </div>
        ))}
      </div>
    </div>
  );
}
