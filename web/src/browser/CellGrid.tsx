// A virtualised grid of model thumbnails (the image grid and the archive
// browser). Cells not loaded yet render as placeholders.
import { Box, Card, Image, Skeleton, Text, UnstyledButton } from "@mantine/core";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef, useState } from "react";

import type { Schemas } from "../api/client";
import { baseName } from "../format";
import { CARD_GAP, CARD_WIDTH, columnsFor } from "./grid";

export type Cell = Schemas["Cell"];
const ROW_HEIGHT = 220;

export function CellGrid({ count, cell, selected, onOpen, onRange }: {
  count: number;
  cell: (index: number) => Cell | undefined;
  selected?: number | null;
  onOpen: (id: number) => void;
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
  const cols = columnsFor(width);
  const rows = Math.ceil(count / cols);
  const virtualizer = useVirtualizer({ count: rows, getScrollElement: () => scrollRef.current, estimateSize: () => ROW_HEIGHT, overscan: 3 });
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
                  <Box key={i} w={CARD_WIDTH}>
                    {m ? (
                      <UnstyledButton onClick={() => onOpen(m.id)} w="100%" data-testid="cell">
                        <Card withBorder p={6} style={selected === m.id ? { outline: "2px solid var(--mantine-primary-color-filled)" } : undefined}>
                          {m.thumbnail ? (
                            <Image src={m.thumbnail} h={150} fit="contain" radius="sm" alt="" loading="lazy" />
                          ) : (
                            <Box h={150} bg="var(--mantine-color-default-hover)" style={{ borderRadius: 4 }} />
                          )}
                          <Text size="sm" truncate mt={4}>{m.title || baseName(m.archive_path)}</Text>
                          <Text size="xs" c="dimmed" truncate>{m.creator || " "}</Text>
                        </Card>
                      </UnstyledButton>
                    ) : (
                      <Skeleton h={ROW_HEIGHT - 16} radius="sm" />
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
