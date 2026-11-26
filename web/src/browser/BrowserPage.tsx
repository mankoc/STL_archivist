// The browser: filter pane, table and detail pane (the desktop's main window).
import { Box, Button, Center, CloseButton, Group, Loader, Menu, SegmentedControl, Text, Tooltip, VisuallyHidden } from "@mantine/core";
import { useHotkeys } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconChevronDown, IconEdit, IconFilterOff, IconPhoto, IconTable } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";
import type { JobRequest } from "../jobs/jobs";
import { DetailPane } from "./DetailPane";
import { EditorDrawer } from "./EditorDrawer";
import { FilterPane } from "./FilterPane";
import { GridView } from "./GridView";
import { usePaneWidths } from "./usePaneWidths";
import { ColumnsMenu } from "./ColumnsMenu";
import { ConfirmModal } from "./ConfirmModal";
import { FolderMenu, type FolderMenuAt } from "./FolderMenu";
import { SortModal } from "./SortModal";
import { AiCheckDrawer } from "./AiCheckDrawer";
import { AiTaggerDrawer } from "./AiTaggerDrawer";
import { MassEditDrawer } from "./MassEditDrawer";
import { EMPTY, type FilterSpec, isEmpty } from "./filters";
import { choiceFrom, COLUMN_ORDER_KEY, moveColumn } from "./columns";
import { COLUMNS, ModelTable, type Sort, useModelIds } from "./ModelTable";
import { loadOptions, saveOptions, type SearchOptions } from "./search";
import { SearchBox } from "./SearchBox";
import { NONE, click, contextClick, coverage, prune, selectAll, type Selection } from "./selection";
import { HIGHLIGHT_WARNINGS } from "./warnings";
import { RELATIVE_PATHS } from "./cells";
import { type ModelActionHandlers, ModelActionItems } from "./ModelActions";
import classes from "./BrowserPage.module.css";

/** The order while the ids load (one array, so that effects and memos keep still). */
const NO_IDS: number[] = [];

const SHOW = ["library", "author", "tags", "source", "date", "sculptor", "collection", "subcollection", "category", "release", "scale", "presupports", "path"];

/** `active`: shown. The page stays mounted while another one shows (Shell). */
export function BrowserPage({ active = true }: { active?: boolean }) {
  const [filter, setFilter] = useState<FilterSpec>(EMPTY);
  // The search text once typing pauses (the box keeps what is being typed).
  const [search, setSearch] = useState("");
  const [searchOptions, setSearchOptionsState] = useState<SearchOptions>(loadOptions);
  const setSearchOptions = (o: SearchOptions) => {
    setSearchOptionsState(o);
    saveOptions(o);
  };
  const [sort, setSort] = useState<Sort | null>(null);
  const [selection, setSelection] = useState<Selection>(NONE);
  const [editing, setEditing] = useState<number | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [massEdit, setMassEdit] = useState(false);
  const [aiTagger, setAiTagger] = useState(false);
  // The models the AI check is about: the selection, or the right-clicked ones.
  const [aiCheck, setAiCheck] = useState<number[] | null>(null);
  const [view, setView] = useState<"table" | "grid">("table");
  // The side panes' widths, dragged by their inner edges.
  const layoutRef = useRef<HTMLDivElement>(null);
  const { detailWidth, filterWidth, dragging, startDrag, reset } = usePaneWidths(layoutRef);
  // The table's columns and paths: per-user settings (ColumnsMenu changes them).
  const prefs = useQuery({ queryKey: ["me-settings"], queryFn: async () => unwrap(await api.GET("/api/me/settings")) as Record<string, unknown> });
  const choice = choiceFrom(prefs.data);
  // The column order, by dragging a header.
  const moveCol = async (from: string, to: string) => {
    const order = moveColumn(COLUMNS, choice.order, from, to);
    queryClient.setQueryData<Record<string, unknown>>(["me-settings"], (old) => ({ ...old, [COLUMN_ORDER_KEY]: order }));
    await api.PUT("/api/me/settings/{key}", { params: { path: { key: COLUMN_ORDER_KEY } }, body: order as never });
  };
  const relativePaths = prefs.data?.[RELATIVE_PATHS] === true;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [sorting, setSorting] = useState(false);
  // The right-click menu: where, and the models it acts on.
  const [menu, setMenu] = useState<{ x: number; y: number; ids: number[] } | null>(null);
  // The Path section's right-click menu: where, and the folder.
  const [folderMenu, setFolderMenu] = useState<FolderMenuAt | null>(null);
  // Leaving the page closes what floats over it (drawers, dialogs, menus): they are
  // portals, so hiding the page would leave them over the next one.
  useEffect(() => {
    if (active) {
      // Text selected on the other page (Ctrl+A there) would cover this one too.
      window.getSelection()?.removeAllRanges();
      return;
    }
    setEditing(null);
    setMassEdit(false);
    setAiTagger(false);
    setConfirmRemove(false);
    setConfirmDelete(false);
    setSorting(false);
    setMenu(null);
    setFolderMenu(null);
  }, [active]);
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
  const query = { filter, search, searchFields: searchOptions.fields, fuzzy: searchOptions.fuzzy, sort };
  const ids = useModelIds(query);
  const order = ids.data ?? NO_IDS;
  // Only against a real list: while loading, or after an error (no placeholder then), nothing is pruned.
  useEffect(() => {
    if (ids.data) setSelection((s) => prune(s, ids.data));
  }, [ids.data]);
  // The selected models in table order, worked out again only when either changes
  // (the table can hold every model of the library).
  const selected = useMemo(() => order.filter((i) => selection.ids.has(i)), [order, selection.ids]);
  const covered = coverage(selection, order);
  const selectEvery = (all: boolean) => setSelection((s) => (all ? selectAll(s, order) : NONE));
  useHotkeys(active ? [["mod+A", () => selectEvery(true)]] : []);
  // The right-click menu of a row or image: editors act on the selection it
  // makes, viewers on the clicked model (its archive).
  const openMenu = (id: number, e: React.MouseEvent) => {
    const r = contextClick(selection, order, id);
    setSelection(r.selection);
    setMenu({ x: e.clientX, y: e.clientY, ids: editor ? r.ids : [id] });
  };

  const clearWarnings = async (ids: number[]) => {
    try {
      const r = unwrap(await api.POST("/api/models/clear-warnings", { body: { ids } }));
      notifications.show({ message: `Cleared the warnings of ${r.models} model(s).` });
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
  };

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

  // The toolbar's Actions menu and the right-click menu (ModelActions.tsx).
  const actions: ModelActionHandlers = {
    edit: setEditing,
    massEdit: () => setMassEdit(true),
    aiTagger: () => setAiTagger(true),
    aiCheck: (ids) => setAiCheck(ids),
    clearWarnings: (ids) => void clearWarnings(ids),
    queueJob: (body, what) => void queueJob(body, what),
    sortFiles: () => setSorting(true),
    deleteFiles: () => setConfirmDelete(true),
    remove: () => setConfirmRemove(true),
  };

  const tree = useQuery({
    queryKey: ["tree", filter],
    queryFn: async () => unwrap(await api.POST("/api/filter-tree", { body: { filter, show: SHOW, counts: true } })),
    placeholderData: (previous) => previous,
  });

  return (
    <div className={classes.layout} ref={layoutRef} style={{ "--detail-width": `${detailWidth}px`, "--filter-width": `${filterWidth}px` } as CSSProperties}>
      <Box className={classes.filters}>
        <div
          className={`${classes.splitter} ${classes.splitterRight}`}
          data-dragging={dragging === "filter" || undefined}
          onPointerDown={startDrag("filter")}
          onDoubleClick={() => reset("filter")}
          title="Drag to resize; double-click to reset"
          data-testid="filter-splitter"
        />
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
          <FilterPane
            tree={tree.data}
            spec={filter}
            onChange={setFilter}
            onFolderMenu={editor ? (folder, e) => setFolderMenu({ x: e.clientX, y: e.clientY, folder }) : undefined}
          />
        ) : (
          <Center p="md">{tree.isError ? <Text c="red">{tree.error.message}</Text> : <Loader size="sm" />}</Center>
        )}
      </Box>
      <Box className={classes.table}>
        <Group px="xs" py={6} className={classes.toolbar}>
          <SearchBox options={searchOptions} onOptions={setSearchOptions} onSearch={setSearch} />
          <Group gap={4} wrap="nowrap">
            <Text size="xs" c="dimmed">
              {ids.data ? `${ids.data.length.toLocaleString()} model${ids.data.length === 1 ? "" : "s"}` : ""}
              {selection.ids.size > 0 ? ` · ${selection.ids.size.toLocaleString()} selected` : ""}
            </Text>
            {selection.ids.size > 0 && (
              <CloseButton size="xs" onClick={() => setSelection(NONE)} aria-label="Clear the selection" title="Clear the selection" data-testid="clear-selection" />
            )}
          </Group>
          <Group gap="xs" ml="auto" wrap="nowrap">
            {editor && selection.ids.size > 0 && (
              <>
                <Button size="compact-xs" variant="subtle" leftSection={<IconEdit size={14} />} onClick={() => setMassEdit(true)}>
                  Mass edit
                </Button>
                <Menu position="bottom-end">
                  <Menu.Target>
                    <Button size="compact-xs" variant="subtle" rightSection={<IconChevronDown size={14} />} data-testid="actions-menu">
                      Actions
                    </Button>
                  </Menu.Target>
                  <Menu.Dropdown>
                    <ModelActionItems ids={selected} where="toolbar" editor={editor} on={actions} />
                  </Menu.Dropdown>
                </Menu>
              </>
            )}
            <SegmentedControl
              size="xs"
              value={view}
              onChange={(v) => setView(v as "table" | "grid")}
              data={[
                { value: "table", label: <ViewLabel label="Table" icon={<IconTable size={14} />} /> },
                { value: "grid", label: <ViewLabel label="Images" icon={<IconPhoto size={14} />} /> },
              ]}
              data-testid="view-toggle"
            />
          </Group>
        </Group>
        <Box className={classes.tableBody}>
          {view === "grid" ? (
            <GridView ids={order} selected={selection.current} sort={sort} onSort={setSort} onOpen={(id) => setSelection((s) => click(s, order, id, { ctrl: false, shift: false }))}
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
              highlightWarnings={prefs.data?.[HIGHLIGHT_WARNINGS] === true}
              onMoveColumn={moveCol}
              checkboxes={{ coverage: covered, onAll: selectEvery }}
              relativePaths={relativePaths}
              corner={<ColumnsMenu choice={choice} relativePaths={relativePaths} />}
            />
          )}
        </Box>
      </Box>
      <Box className={classes.detail}>
        <div
          className={classes.splitter}
          data-dragging={dragging === "detail" || undefined}
          onPointerDown={startDrag("detail")}
          onDoubleClick={() => reset("detail")}
          title="Drag to resize; double-click to reset"
          data-testid="detail-splitter"
        />
        <DetailPane id={selection.current} onEdit={setEditing} />
      </Box>
      <AiCheckDrawer ids={aiCheck ?? []} opened={aiCheck != null} onClose={() => setAiCheck(null)} onEdit={setEditing} />
      <EditorDrawer id={editing} onClose={() => setEditing(null)} />
      <MassEditDrawer ids={selected} opened={massEdit} onClose={() => setMassEdit(false)} />
      <AiTaggerDrawer ids={selected} opened={aiTagger} onClose={() => setAiTagger(false)} />
      <Menu opened={menu != null} onChange={(o) => !o && setMenu(null)} position="bottom-start" withinPortal>
        <Menu.Target>
          <div style={{ position: "fixed", left: menu?.x ?? 0, top: menu?.y ?? 0, width: 0, height: 0 }} />
        </Menu.Target>
        <Menu.Dropdown data-testid="row-menu">
          {menu && <ModelActionItems ids={menu.ids} where="menu" editor={editor} on={actions} />}
        </Menu.Dropdown>
      </Menu>
      {/* Only while shown: leaving the page drops a half-typed pattern crawl. */}
      {active && <FolderMenu at={folderMenu} onClose={() => setFolderMenu(null)} queueJob={queueJob} />}
      {sorting && <SortModal ids={selected} opened onClose={() => setSorting(false)} />}
      <ConfirmModal
        opened={confirmDelete}
        title="Delete archive files"
        confirm="Delete files"
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => { setConfirmDelete(false); void queueJob({ kind: "delete_files", ids: selected }, "Deleting files"); }}
      >
        Delete the archive files of {selection.ids.size} model(s) and remove them from the library? With a trash folder set in
        the library settings the files are moved there; otherwise they are deleted. Needs “allow file delete” in the settings.
      </ConfirmModal>
      <ConfirmModal
        opened={confirmRemove}
        title="Remove from library"
        confirm="Remove"
        onCancel={() => setConfirmRemove(false)}
        onConfirm={remove}
        testId="confirm-remove"
      >
        Remove {selection.ids.size} model(s) from the library? Their records, images and thumbnails are deleted;
        the archive files stay where they are.
      </ConfirmModal>
    </div>
  );
}

/** A view toggle choice: its icon, named in a tooltip and to screen readers. */
function ViewLabel({ label, icon }: { label: string; icon: ReactNode }) {
  return (
    <Tooltip label={label} openDelay={400}>
      <Center>
        {icon}
        <VisuallyHidden>{label}</VisuallyHidden>
      </Center>
    </Tooltip>
  );
}
