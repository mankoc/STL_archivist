// The browser: filter pane, table and detail pane (the desktop's main window).
import { Box, Button, Center, Checkbox, Group, Loader, Menu, Modal, SegmentedControl, Text, TextInput } from "@mantine/core";
import { useDebouncedValue, useHotkeys } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconArchive, IconColumns, IconEdit, IconFilterOff, IconRefresh, IconSearch, IconSparkles, IconSquare, IconSquareCheck, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";
import type { JobRequest } from "../jobs/jobs";
import { DetailPane } from "./DetailPane";
import { EditorDrawer } from "./EditorDrawer";
import { FilterPane } from "./FilterPane";
import { GridView } from "./GridView";
import { clampWidth, DEFAULT_WIDTH, loadWidth, saveWidth } from "./paneWidth";
import { SortModal } from "./SortModal";
import { AiTaggerDrawer } from "./AiTaggerDrawer";
import { MassEditDrawer } from "./MassEditDrawer";
import { EMPTY, type FilterSpec, isEmpty } from "./filters";
import { ALWAYS_SHOWN, choiceFrom, COLUMN_ORDER_KEY, HIDDEN_COLUMNS_KEY, isShown, moveColumn, SHOWN_COLUMNS_KEY, toggle } from "./columns";
import { COLUMNS, ModelTable, type Sort, useModelIds } from "./ModelTable";
import { NONE, click, contextClick, coverage, prune, selectAll, type Selection } from "./selection";
import classes from "./BrowserPage.module.css";

const SHOW = ["library", "author", "tags", "source", "date", "sculptor", "collection", "subcollection", "category", "release", "scale", "presupports", "path"];

export function BrowserPage() {
  const [filter, setFilter] = useState<FilterSpec>(EMPTY);
  const [searchText, setSearchText] = useState("");
  const [search] = useDebouncedValue(searchText, 250);
  const [sort, setSort] = useState<Sort | null>(null);
  const [selection, setSelection] = useState<Selection>(NONE);
  const [editing, setEditing] = useState<number | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [massEdit, setMassEdit] = useState(false);
  const [aiTagger, setAiTagger] = useState(false);
  const [view, setView] = useState<"table" | "grid">("table");
  // The detail pane's width, dragged by its left edge.
  const layoutRef = useRef<HTMLDivElement>(null);
  const [detailWidth, setDetailWidth] = useState(loadWidth);
  const [dragging, setDragging] = useState(false);
  const startDrag = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const layout = layoutRef.current;
    if (!layout) return;
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    setDragging(true);
    let width = detailWidth;
    const move = (ev: PointerEvent) => {
      const box = layout.getBoundingClientRect();
      width = clampWidth(box.right - ev.clientX, box.width);
      setDetailWidth(width);
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      setDragging(false);
      saveWidth(width);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
  };
  // Hidden table columns: a per-user setting, so it follows the account.
  const prefs = useQuery({ queryKey: ["me-settings"], queryFn: async () => unwrap(await api.GET("/api/me/settings")) as Record<string, unknown> });
  const choice = choiceFrom(prefs.data);
  const toggleColumn = async (c: (typeof COLUMNS)[number]) => {
    const next = toggle(choice, c);
    const [key, value] = c.optional ? [SHOWN_COLUMNS_KEY, next.shown] : [HIDDEN_COLUMNS_KEY, next.hidden];
    queryClient.setQueryData<Record<string, unknown>>(["me-settings"], (old) => ({ ...old, [key]: value }));
    await api.PUT("/api/me/settings/{key}", { params: { path: { key } }, body: value as never });
  };
  // The column order: dragged headers, and back to the default.
  const moveCol = async (from: string, to: string) => {
    const order = moveColumn(COLUMNS, choice.order, from, to);
    queryClient.setQueryData<Record<string, unknown>>(["me-settings"], (old) => ({ ...old, [COLUMN_ORDER_KEY]: order }));
    await api.PUT("/api/me/settings/{key}", { params: { path: { key: COLUMN_ORDER_KEY } }, body: order as never });
  };
  const resetOrder = async () => {
    queryClient.setQueryData<Record<string, unknown>>(["me-settings"], (old) => {
      const next = { ...old };
      delete next[COLUMN_ORDER_KEY];
      return next;
    });
    await api.DELETE("/api/me/settings/{key}", { params: { path: { key: COLUMN_ORDER_KEY } } });
  };
  const columnItem = (c: (typeof COLUMNS)[number]) => (
    <Menu.Item key={c.id} disabled={c.id === ALWAYS_SHOWN} onClick={() => toggleColumn(c)}
      leftSection={<Checkbox size="xs" readOnly checked={isShown(c, choice)} tabIndex={-1} />}>
      {c.header}
    </Menu.Item>
  );
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [sorting, setSorting] = useState(false);
  // The right-click menu: where, and the models it acts on.
  const [menu, setMenu] = useState<{ x: number; y: number; ids: number[] } | null>(null);
  const selectedIds = () => order.filter((i) => selection.ids.has(i));
  const queueJob = async (body: JobRequest, what: string) => {
    try {
      unwrap(await api.POST("/api/jobs", { body }));
      notifications.show({ message: `${what} queued: follow it on the Jobs page` });
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
  };
  const user = useUser();
  const editor = hasRole(user, "editor");
  const queryClient = useQueryClient();
  const query = { filter, search, sort };
  const ids = useModelIds(query);
  const order = ids.data ?? [];
  useEffect(() => setSelection((s) => prune(s, order)), [order]);
  const covered = coverage(selection, order);
  const selectEvery = (all: boolean) => setSelection((s) => (all ? selectAll(s, order) : NONE));
  useHotkeys([["mod+A", () => selectEvery(true)]]);
  // Editors get a menu on a right-clicked row (viewers keep the browser's own).
  const openMenu = editor
    ? (id: number, e: React.MouseEvent) => {
        const r = contextClick(selection, order, id);
        setSelection(r.selection);
        setMenu({ x: e.clientX, y: e.clientY, ids: r.ids });
      }
    : undefined;

  const remove = async () => {
    setConfirmRemove(false);
    try {
      const r = unwrap(await api.POST("/api/models/delete", { body: { ids: [...selection.ids] } }));
      notifications.show({ message: `Removed ${r.models} model(s) from the library.` });
      setSelection(NONE);
      for (const key of ["ids", "rows", "tree"]) await queryClient.invalidateQueries({ queryKey: [key] });
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
  };

  const tree = useQuery({
    queryKey: ["tree", filter],
    queryFn: async () => unwrap(await api.POST("/api/filter-tree", { body: { filter, show: SHOW, counts: true } })),
    placeholderData: (previous) => previous,
  });

  return (
    <div className={classes.layout} ref={layoutRef} style={{ "--detail-width": `${detailWidth}px` } as CSSProperties}>
      <Box className={classes.filters}>
        <Group justify="space-between" px="xs" py={6} className={classes.toolbar}>
          <Text size="sm" fw={600}>
            Filter
          </Text>
          <Button
            size="compact-xs"
            variant="subtle"
            leftSection={<IconFilterOff size={14} />}
            disabled={isEmpty(filter)}
            onClick={() => setFilter(EMPTY)}
          >
            Clear
          </Button>
        </Group>
        {tree.data ? (
          <FilterPane tree={tree.data} spec={filter} onChange={setFilter} />
        ) : (
          <Center p="md">{tree.isError ? <Text c="red">{tree.error.message}</Text> : <Loader size="sm" />}</Center>
        )}
      </Box>
      <Box className={classes.table}>
        <Group px="xs" py={6} className={classes.toolbar}>
          <TextInput
            size="xs"
            placeholder="Search title, creator, path, tags"
            leftSection={<IconSearch size={14} />}
            value={searchText}
            onChange={(e) => setSearchText(e.currentTarget.value)}
            w={320}
          />
          <Text size="xs" c="dimmed">
            {ids.data ? `${ids.data.length.toLocaleString()} model${ids.data.length === 1 ? "" : "s"}` : ""}
            {selection.ids.size > 1 ? ` · ${selection.ids.size.toLocaleString()} selected` : ""}
          </Text>
          {order.length > 0 && (
            <Button size="compact-xs" variant="subtle" leftSection={covered === "all" ? <IconSquare size={14} /> : <IconSquareCheck size={14} />}
              onClick={() => selectEvery(covered !== "all")} title="Ctrl+A selects all" data-testid="select-all">
              {covered === "all" ? "Select none" : "Select all"}
            </Button>
          )}
          {view === "table" && (
            <Menu closeOnItemClick={false} position="bottom-start">
              <Menu.Target>
                <Button size="compact-xs" variant="subtle" leftSection={<IconColumns size={14} />} data-testid="columns-menu">
                  Columns
                </Button>
              </Menu.Target>
              <Menu.Dropdown mah="70vh" style={{ overflowY: "auto" }}>
                {COLUMNS.filter((c) => !c.optional).map(columnItem)}
                <Menu.Label>More columns</Menu.Label>
                {COLUMNS.filter((c) => c.optional).map(columnItem)}
                <Menu.Divider />
                <Text size="xs" c="dimmed" px="sm" py={4} maw={250}>
                  To move a column, drag the ⋮⋮ grip left of its name in the table header and drop it on the
                  column whose place it should take.
                </Text>
                <Menu.Item disabled={!choice.order.length} onClick={resetOrder} data-testid="reset-column-order">
                  Reset column order
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          )}
          <SegmentedControl size="xs" value={view} onChange={(v) => setView(v as "table" | "grid")}
            data={[{ value: "table", label: "Table" }, { value: "grid", label: "Images" }]} data-testid="view-toggle" />
          {editor && selection.ids.size > 0 && (
            <Group gap="xs" ml="auto">
              <Button size="compact-xs" variant="subtle" leftSection={<IconEdit size={14} />} onClick={() => setMassEdit(true)}>
                Mass edit
              </Button>
              <Button size="compact-xs" variant="subtle" leftSection={<IconSparkles size={14} />} onClick={() => setAiTagger(true)} data-testid="ai-tagger">
                AI tagger
              </Button>
              <Menu position="bottom-end">
                <Menu.Target>
                  <Button size="compact-xs" variant="subtle" leftSection={<IconArchive size={14} />} data-testid="archive-menu">
                    Archives
                  </Button>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Item onClick={() => queueJob({ kind: "rescan", ids: selectedIds() }, "Rescan")}>Rescan</Menu.Item>
                  <Menu.Item onClick={() => queueJob({ kind: "generate_thumbnails", ids: selectedIds() }, "Thumbnail generation")}>
                    Generate thumbnails
                  </Menu.Item>
                  <Menu.Item onClick={() => setSorting(true)}>Sort files…</Menu.Item>
                  <Menu.Divider />
                  <Menu.Item color="red" onClick={() => setConfirmDelete(true)}>Delete files…</Menu.Item>
                </Menu.Dropdown>
              </Menu>
              <Button size="compact-xs" variant="subtle" color="red" leftSection={<IconTrash size={14} />} onClick={() => setConfirmRemove(true)}>
                Remove from library
              </Button>
            </Group>
          )}
        </Group>
        <Box className={classes.tableBody}>
          {view === "grid" ? (
            <GridView ids={order} selected={selection.current} onOpen={(id) => setSelection((s) => click(s, order, id, { ctrl: false, shift: false }))}
              onContextMenu={openMenu} />
          ) : (
            <ModelTable
              query={query}
              onSort={setSort}
              selection={selection}
              onRowClick={(id, mods) => setSelection((s) => click(s, order, id, mods))}
              onRowContextMenu={openMenu}
              editable={editor}
              choice={choice}
              onMoveColumn={moveCol}
              checkboxes={covered === "none" ? undefined : { coverage: covered, onAll: selectEvery }}
            />
          )}
        </Box>
      </Box>
      <Box className={classes.detail}>
        <div
          className={classes.splitter}
          data-dragging={dragging || undefined}
          onPointerDown={startDrag}
          onDoubleClick={() => { setDetailWidth(DEFAULT_WIDTH); saveWidth(DEFAULT_WIDTH); }}
          title="Drag to resize; double-click to reset"
          data-testid="detail-splitter"
        />
        <DetailPane id={selection.current} onEdit={setEditing} />
      </Box>
      <EditorDrawer id={editing} onClose={() => setEditing(null)} />
      <MassEditDrawer ids={order.filter((i) => selection.ids.has(i))} opened={massEdit} onClose={() => setMassEdit(false)} />
      <AiTaggerDrawer ids={order.filter((i) => selection.ids.has(i))} opened={aiTagger} onClose={() => setAiTagger(false)} />
      <Menu opened={menu != null} onChange={(o) => !o && setMenu(null)} position="bottom-start" withinPortal>
        <Menu.Target>
          <div style={{ position: "fixed", left: menu?.x ?? 0, top: menu?.y ?? 0, width: 0, height: 0 }} />
        </Menu.Target>
        <Menu.Dropdown data-testid="row-menu">
          <Menu.Item leftSection={<IconRefresh size={14} />} onClick={() => menu && queueJob({ kind: "rescan", ids: menu.ids }, "Rescan")}>
            {menu && menu.ids.length > 1 ? `Rescan ${menu.ids.length} archives` : "Rescan"}
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      {sorting && <SortModal ids={selectedIds()} opened onClose={() => setSorting(false)} />}
      <Modal opened={confirmDelete} onClose={() => setConfirmDelete(false)} title="Delete archive files">
        <Text size="sm">
          Delete the archive files of {selection.ids.size} model(s) and remove them from the library? With a trash folder set in
          the library settings the files are moved there; otherwise they are deleted. Needs “allow file delete” in the settings.
        </Text>
        <Group justify="flex-end" mt="md">
          <Button variant="default" onClick={() => setConfirmDelete(false)}>Cancel</Button>
          <Button color="red" onClick={() => { setConfirmDelete(false); void queueJob({ kind: "delete_files", ids: selectedIds() }, "Deleting files"); }}>
            Delete files
          </Button>
        </Group>
      </Modal>
      <Modal opened={confirmRemove} onClose={() => setConfirmRemove(false)} title="Remove from library">
        <Text size="sm">
          Remove {selection.ids.size} model(s) from the library? Their records, images and thumbnails are deleted;
          the archive files stay where they are.
        </Text>
        <Group justify="flex-end" mt="md">
          <Button variant="default" onClick={() => setConfirmRemove(false)}>
            Cancel
          </Button>
          <Button color="red" onClick={remove} data-testid="confirm-remove">
            Remove
          </Button>
        </Group>
      </Modal>
    </div>
  );
}
