// The browser table: every matching id in display order (one request), rows
// fetched in blocks as they scroll into view, virtualised rendering. Sorting
// runs on the server (`/api/models/ids`), ties ordered by id.
import { Box, Center, Group, Loader, Text, UnstyledButton } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconChevronDown, IconChevronUp } from "@tabler/icons-react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type ColumnDef,
  type ColumnSizingState,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { type KeyboardEvent, type ReactNode, useMemo, useRef, useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { baseName, formatDate, formatSize } from "../format";
import type { FilterSpec } from "./filters";
import { type ColumnChoice, visible } from "./columns";
import { blockIds, blocksFor } from "./rowBlocks";
import type { Selection } from "./selection";
import classes from "./ModelTable.module.css";

export type Row = Schemas["Row"];
export interface Sort {
  column: string;
  descending: boolean;
}
export interface Query {
  filter: FilterSpec;
  search: string;
  sort: Sort | null;
}

interface Column {
  id: string;
  header: string;
  size: number;
  /** The server's sort column, when the column can be sorted. */
  sortKey?: string;
  render: (row: Row) => ReactNode;
  /** The cell-edit field (`PATCH /api/models/cells`) and the text to start from. */
  field?: string;
  text?: (row: Row) => string;
  /** Off until the user turns it on (Columns → More columns). */
  optional?: boolean;
}

/** `2026-10-18 14:02:08.62` → `2026-10-18 14:02`. */
const stamp = (s: string | null | undefined) => (s ? s.slice(0, 16) : null);

const text = (key: keyof Row) => (r: Row) => String(r[key] ?? "");

export const COLUMNS: Column[] = [
  { id: "title", header: "Title", size: 280, sortKey: "title", render: (r) => r.title ?? baseName(r.archive_path), field: "title", text: text("title") },
  { id: "creator", header: "Creator", size: 140, sortKey: "creator", render: (r) => r.creator, field: "creator", text: text("creator") },
  { id: "source", header: "Type", size: 110, sortKey: "source", render: (r) => r.source, field: "source", text: text("source") },
  { id: "date", header: "Date", size: 80, render: (r) => formatDate(r.release_year, r.release_month), field: "date", text: (r) => formatDate(r.release_year, r.release_month) },
  { id: "sculptor", header: "Sculptor", size: 130, sortKey: "sculptor_name", render: (r) => r.sculptor, field: "sculptor", text: text("sculptor") },
  { id: "collection", header: "Collection", size: 130, sortKey: "collection_name", render: (r) => r.collection, field: "collection", text: text("collection") },
  { id: "subcollection", header: "Subcollection", size: 130, sortKey: "subcollection_name", render: (r) => r.subcollection, field: "subcollection", text: text("subcollection") },
  { id: "category", header: "Category", size: 110, sortKey: "category_name", render: (r) => r.category, field: "category", text: text("category") },
  { id: "release", header: "Release", size: 120, sortKey: "release_name", render: (r) => r.release, field: "release", text: text("release") },
  { id: "scales", header: "Scale", size: 90, render: (r) => r.scales, field: "scale", text: text("scales") },
  { id: "tags", header: "Tags", size: 200, render: (r) => r.tags, field: "tags", text: text("tags") },
  { id: "presupports", header: "Presupports", size: 110, sortKey: "presupports", render: (r) => r.presupports, field: "presupports", text: text("presupports") },
  { id: "size", header: "File Size", size: 90, sortKey: "archive_size", render: (r) => formatSize(r.archive_size) },
  { id: "path", header: "Path", size: 360, sortKey: "archive_path", render: (r) => r.archive_path },
  { id: "library", header: "Library", size: 140, sortKey: "library_name", render: (r) => r.library },
  // Optional columns.
  { id: "warning", header: "Warning", size: 220, optional: true, render: (r) => r.warning?.split("\n").join("; ") },
  { id: "sequence", header: "Sequence", size: 90, sortKey: "sequence", optional: true, render: (r) => r.sequence, field: "sequence", text: (r) => String(r.sequence ?? "") },
  { id: "images", header: "Images", size: 80, optional: true, render: (r) => r.images },
  { id: "compression", header: "Compression", size: 110, sortKey: "compression_rate", optional: true, render: (r) => (r.compression_rate == null ? null : `${Math.round(r.compression_rate * 100)} %`) },
  { id: "to_print", header: "To print", size: 80, optional: true, render: (r) => (r.to_print ? "✓" : null) },
  { id: "missing", header: "Missing", size: 80, optional: true, render: (r) => (r.is_missing ? "missing" : null) },
  { id: "added", header: "Added", size: 140, sortKey: "created_at", optional: true, render: (r) => stamp(r.created_at) },
  { id: "updated", header: "Updated", size: 140, optional: true, render: (r) => stamp(r.updated_at) },
  { id: "url", header: "URL", size: 240, optional: true, render: (r) => r.url },
  { id: "description", header: "Description", size: 300, optional: true, render: (r) => r.description },
  { id: "folder", header: "Folder", size: 300, optional: true, render: (r) => r.archive_folder },
  { id: "hash", header: "Archive hash", size: 200, optional: true, render: (r) => r.archive_hash },
];

const ROW_HEIGHT = 30;

export function useModelIds(query: Query) {
  return useQuery({
    queryKey: ["ids", query],
    queryFn: async () =>
      unwrap(await api.POST("/api/models/ids", { body: { filter: query.filter, search: query.search, sort: query.sort } }))
        .ids,
    placeholderData: (previous) => previous,
  });
}

interface Props {
  query: Query;
  onSort: (sort: Sort | null) => void;
  selection: Selection;
  onRowClick: (id: number, mods: { ctrl: boolean; shift: boolean }) => void;
  /** A row was right-clicked (none: the browser's own menu). */
  onRowContextMenu?: (id: number, e: React.MouseEvent) => void;
  /** Cells can be edited (double-click). */
  editable: boolean;
  /** Which columns to show. */
  choice?: ColumnChoice;
}

/** Save one cell (`PATCH /api/models/cells`). */
async function saveCell(id: number, field: string, value: string) {
  unwrap(await api.PATCH("/api/models/cells", { body: { edits: [{ id, field, value }] } }));
}

function CellEditor(props: { initial: string; onDone: (value: string | null) => void }) {
  const [value, setValue] = useState(props.initial);
  const done = useRef(false);
  const finish = (v: string | null) => {
    if (done.current) return;
    done.current = true;
    props.onDone(v !== null && v !== props.initial ? v : null);
  };
  const key = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") finish(value);
    if (e.key === "Escape") finish(null);
  };
  return (
    <input
      className={classes.editor}
      value={value}
      autoFocus
      onChange={(e) => setValue(e.currentTarget.value)}
      onKeyDown={key}
      onBlur={() => finish(value)}
      onClick={(e) => e.stopPropagation()}
      data-testid="cell-editor"
    />
  );
}

export function ModelTable({ query, onSort, selection, onRowClick, onRowContextMenu, editable, choice }: Props) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<{ id: number; column: number } | null>(null);
  const ids = useModelIds(query);
  const all = ids.data ?? [];
  const scrollRef = useRef<HTMLDivElement>(null);
  const [sizing, setSizing] = useState<ColumnSizingState>({});

  const shown = useMemo(() => visible(COLUMNS, choice ?? { hidden: [], shown: [] }), [choice]);
  const columns = useMemo<ColumnDef<number>[]>(
    () => shown.map((c) => ({ id: c.id, header: c.header, size: c.size, minSize: 50, enableResizing: true })),
    [shown],
  );
  const table = useReactTable({
    data: all,
    columns,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: "onChange",
    state: { columnSizing: sizing },
    onColumnSizingChange: setSizing,
    manualSorting: true,
  });

  const virtualizer = useVirtualizer({
    count: all.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 20,
  });
  const items = virtualizer.getVirtualItems();
  const blocks = blocksFor(items[0]?.index ?? 0, items[items.length - 1]?.index ?? -1, all.length);
  const rowQueries = useQueries({
    queries: blocks.map((b) => {
      const blockIdList = blockIds(all, b);
      return {
        queryKey: ["rows", blockIdList],
        queryFn: async () => unwrap(await api.POST("/api/models/rows", { body: { ids: blockIdList } })).rows,
        staleTime: 30_000,
      };
    }),
  });
  const rows = new Map<number, Row>();
  for (const q of rowQueries) for (const r of q.data ?? []) rows.set(r.id, r);

  const width = table.getTotalSize();
  const sortOf = (c: Column) => (query.sort?.column === c.sortKey ? query.sort : null);
  const clickHeader = (c: Column) => {
    if (!c.sortKey) return;
    const current = sortOf(c);
    // ascending → descending → unsorted (id order)
    onSort(!current ? { column: c.sortKey, descending: false } : !current.descending ? { column: c.sortKey, descending: true } : null);
  };

  if (ids.isPending) {
    return (
      <Center h="100%">
        <Loader />
      </Center>
    );
  }
  if (ids.isError) {
    return (
      <Center h="100%">
        <Text c="red">{String(ids.error.message)}</Text>
      </Center>
    );
  }

  return (
    <div ref={scrollRef} className={classes.scroll} data-testid="model-table">
      <div className={classes.header} style={{ width }}>
        {table.getHeaderGroups()[0].headers.map((h, i) => {
          const c = shown[i];
          const sort = sortOf(c);
          return (
            <div key={h.id} className={classes.headerCell} style={{ width: h.getSize() }}>
              <UnstyledButton className={classes.headerButton} onClick={() => clickHeader(c)} disabled={!c.sortKey}>
                <Group gap={4} wrap="nowrap">
                  <Text size="xs" fw={600} tt="uppercase" truncate>
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </Text>
                  {sort && (sort.descending ? <IconChevronDown size={14} /> : <IconChevronUp size={14} />)}
                </Group>
              </UnstyledButton>
              <div
                onMouseDown={h.getResizeHandler()}
                onTouchStart={h.getResizeHandler()}
                className={classes.resizer}
                data-resizing={h.column.getIsResizing() || undefined}
              />
            </div>
          );
        })}
      </div>
      <div style={{ height: virtualizer.getTotalSize(), width, position: "relative" }}>
        {items.map((item) => {
          const id = all[item.index];
          const row = rows.get(id);
          return (
            <Box
              key={id}
              className={classes.row}
              data-testid="row"
              data-selected={selection.ids.has(id) || undefined}
              data-current={selection.current === id || undefined}
              style={{ transform: `translateY(${item.start}px)`, height: ROW_HEIGHT, width }}
              onClick={(e) => onRowClick(id, { ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey })}
              onContextMenu={onRowContextMenu && ((e) => {
                e.preventDefault();
                onRowContextMenu(id, e);
              })}
            >
              {table.getHeaderGroups()[0].headers.map((h, i) => {
                const c = shown[i];
                const isEditing = editing?.id === id && editing.column === i && row;
                return (
                  <div
                    key={h.id}
                    className={classes.cell}
                    style={{ width: h.getSize() }}
                    data-column={c.id}
                    onDoubleClick={editable && c.field && row ? () => setEditing({ id, column: i }) : undefined}
                  >
                    {isEditing ? (
                      <CellEditor
                        initial={c.text!(row)}
                        onDone={async (value) => {
                          setEditing(null);
                          if (value === null) return;
                          try {
                            await saveCell(id, c.field!, value);
                            await queryClient.invalidateQueries({ queryKey: ["rows"] });
                          } catch (err) {
                            notifications.show({ color: "red", title: "Could not save", message: String((err as Error).message) });
                          }
                        }}
                      />
                    ) : row ? (
                      c.render(row)
                    ) : i === 0 ? (
                      "…"
                    ) : null}
                  </div>
                );
              })}
            </Box>
          );
        })}
      </div>
      {all.length === 0 && (
        <Center p="xl">
          <Text c="dimmed">No models match.</Text>
        </Center>
      )}
    </div>
  );
}
