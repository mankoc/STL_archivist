// The browser: filter pane, table and detail pane (the desktop's main window).
import {
  ActionIcon, Anchor, Box, Button, Center, Checkbox, CloseButton, Group, Loader, Menu, Modal, Popover, SegmentedControl, SimpleGrid, Stack, Switch, Text,
  TextInput, Tooltip, VisuallyHidden,
} from "@mantine/core";
import { useDebouncedValue, useHotkeys } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconAdjustmentsHorizontal, IconChevronDown, IconColumns, IconEdit, IconFilterOff, IconPhoto, IconRefresh, IconSearch, IconTable } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode, useEffect, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";
import type { JobRequest } from "../jobs/jobs";
import { DetailPane } from "./DetailPane";
import { EditorDrawer } from "./EditorDrawer";
import { FilterPane } from "./FilterPane";
import { GridView } from "./GridView";
import { clampWidth, DEFAULT_WIDTH, FILTER_PANE, loadWidth, saveWidth } from "./paneWidth";
import { PatternSample } from "./PatternSample";
import { folderPattern } from "./patterns";
import { SortModal } from "./SortModal";
import { AiTaggerDrawer } from "./AiTaggerDrawer";
import { MassEditDrawer } from "./MassEditDrawer";
import { EMPTY, type FilterSpec, isEmpty } from "./filters";
import { ALWAYS_SHOWN, choiceFrom, COLUMN_ORDER_KEY, HIDDEN_COLUMNS_KEY, isShown, moveColumn, SHOWN_COLUMNS_KEY, toggle } from "./columns";
import { COLUMNS, ModelTable, type Sort, useModelIds } from "./ModelTable";
import { customised, DEFAULT_OPTIONS, loadOptions, placeholder, saveOptions, SEARCH_FIELDS, type SearchOptions } from "./search";
import { NONE, click, contextClick, coverage, prune, selectAll, type Selection } from "./selection";
import { HIGHLIGHT_WARNINGS } from "./warnings";
import { RELATIVE_PATHS } from "./cells";
import { type ModelActionHandlers, ModelActionItems } from "./ModelActions";
import classes from "./BrowserPage.module.css";

const SHOW = ["library", "author", "tags", "source", "date", "sculptor", "collection", "subcollection", "category", "release", "scale", "presupports", "path"];

/** `active`: shown. The page stays mounted while another one shows (Shell). */
export function BrowserPage({ active = true }: { active?: boolean }) {
  const [filter, setFilter] = useState<FilterSpec>(EMPTY);
  const [searchText, setSearchText] = useState("");
  const [search] = useDebouncedValue(searchText, 250);
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
  const [view, setView] = useState<"table" | "grid">("table");
  // The side panes' widths, dragged by their inner edges.
  const layoutRef = useRef<HTMLDivElement>(null);
  const [detailWidth, setDetailWidth] = useState(loadWidth);
  const [filterWidth, setFilterWidth] = useState(() => loadWidth(FILTER_PANE.key, FILTER_PANE.fallback, FILTER_PANE.min));
  const [dragging, setDragging] = useState<"filter" | "detail" | null>(null);
  const startDrag = (pane: "filter" | "detail") => (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const layout = layoutRef.current;
    if (!layout) return;
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    setDragging(pane);
    let width = pane === "filter" ? filterWidth : detailWidth;
    const move = (ev: PointerEvent) => {
      const box = layout.getBoundingClientRect();
      if (pane === "filter") {
        width = clampWidth(ev.clientX - box.left, box.width, FILTER_PANE.maxShare, FILTER_PANE.min);
        setFilterWidth(width);
      } else {
        width = clampWidth(box.right - ev.clientX, box.width);
        setDetailWidth(width);
      }
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      setDragging(null);
      if (pane === "filter") saveWidth(width, FILTER_PANE.key);
      else saveWidth(width);
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
  const relativePaths = prefs.data?.[RELATIVE_PATHS] === true;
  const toggleRelativePaths = async () => {
    const value = !relativePaths;
    queryClient.setQueryData<Record<string, unknown>>(["me-settings"], (old) => ({ ...old, [RELATIVE_PATHS]: value }));
    await api.PUT("/api/me/settings/{key}", { params: { path: { key: RELATIVE_PATHS } }, body: value as never });
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
  // The Path section's right-click menu: where, and the folder.
  const [folderMenu, setFolderMenu] = useState<{ x: number; y: number; folder: string } | null>(null);
  // *Crawl with a pattern…*: the folder, and the pattern being typed.
  const [patternCrawl, setPatternCrawl] = useState<{ folder: string; pattern: string } | null>(null);
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
    setPatternCrawl(null);
  }, [active]);
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: async () => unwrap(await api.GET("/api/libraries")) });
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
  const query = { filter, search, searchFields: searchOptions.fields, fuzzy: searchOptions.fuzzy, sort };
  const ids = useModelIds(query);
  const order = ids.data ?? [];
  useEffect(() => setSelection((s) => prune(s, order)), [order]);
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
          onDoubleClick={() => { setFilterWidth(FILTER_PANE.fallback); saveWidth(FILTER_PANE.fallback, FILTER_PANE.key); }}
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
          <TextInput
            size="xs"
            placeholder={placeholder(searchOptions)}
            leftSection={<IconSearch size={14} />}
            value={searchText}
            onChange={(e) => setSearchText(e.currentTarget.value)}
            w={320}
            rightSectionPointerEvents="all"
            rightSection={
              <Popover position="bottom-end" shadow="md" withArrow>
                <Popover.Target>
                  <Tooltip label="Search options" openDelay={400}>
                    <ActionIcon size="sm" variant={customised(searchOptions) ? "light" : "subtle"} color={customised(searchOptions) ? undefined : "gray"}
                      aria-label="Search options" data-testid="search-options">
                      <IconAdjustmentsHorizontal size={14} />
                    </ActionIcon>
                  </Tooltip>
                </Popover.Target>
                <Popover.Dropdown>
                  <Stack gap="xs">
                    <Text size="xs" fw={600}>Search in</Text>
                    <SimpleGrid cols={2} spacing="xs" verticalSpacing={6}>
                      {SEARCH_FIELDS.map((f) => {
                        const on = searchOptions.fields.includes(f.value);
                        return (
                          <Checkbox key={f.value} size="xs" label={f.label} checked={on}
                            disabled={on && searchOptions.fields.length === 1}
                            onChange={() => setSearchOptions({
                              ...searchOptions,
                              fields: SEARCH_FIELDS.map((x) => x.value).filter((v) => (v === f.value ? !on : searchOptions.fields.includes(v))),
                            })} />
                        );
                      })}
                    </SimpleGrid>
                    <Switch size="xs" label="Fuzzy: ignore accents, allow typos" checked={searchOptions.fuzzy}
                      onChange={(e) => setSearchOptions({ ...searchOptions, fuzzy: e.currentTarget.checked })} />
                    <Text size="xs" c="dimmed" maw={260}>
                      {searchOptions.fuzzy
                        ? "Every word must appear in one of the fields; words of four letters or more may have a typo (two from seven letters)."
                        : "Finds the text as typed inside any of the fields."}
                    </Text>
                    {customised(searchOptions) && (
                      <Anchor size="xs" component="button" onClick={() => setSearchOptions(DEFAULT_OPTIONS)}>Reset</Anchor>
                    )}
                  </Stack>
                </Popover.Dropdown>
              </Popover>
            }
          />
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
                    <ModelActionItems ids={selectedIds()} where="toolbar" editor={editor} on={actions} />
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
              highlightWarnings={prefs.data?.[HIGHLIGHT_WARNINGS] === true}
              onMoveColumn={moveCol}
              checkboxes={{ coverage: covered, onAll: selectEvery }}
              relativePaths={relativePaths}
              corner={
                <Menu closeOnItemClick={false} position="bottom-end">
                  <Menu.Target>
                    <Tooltip label="Columns" openDelay={400}>
                      <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Columns" data-testid="columns-menu">
                        <IconColumns size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Menu.Target>
                  <Menu.Dropdown mah="70vh" style={{ overflowY: "auto" }}>
                    {COLUMNS.filter((c) => !c.optional).map(columnItem)}
                    <Menu.Label>More columns</Menu.Label>
                    {COLUMNS.filter((c) => c.optional).map(columnItem)}
                    <Menu.Divider />
                    <Menu.Item onClick={toggleRelativePaths} leftSection={<Checkbox size="xs" readOnly checked={relativePaths} tabIndex={-1} />}
                      data-testid="relative-paths">
                      Paths relative to their library
                    </Menu.Item>
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
              }
            />
          )}
        </Box>
      </Box>
      <Box className={classes.detail}>
        <div
          className={classes.splitter}
          data-dragging={dragging === "detail" || undefined}
          onPointerDown={startDrag("detail")}
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
          {menu && <ModelActionItems ids={menu.ids} where="menu" editor={editor} on={actions} />}
        </Menu.Dropdown>
      </Menu>
      <Menu opened={folderMenu != null} onChange={(o) => !o && setFolderMenu(null)} position="bottom-start" withinPortal>
        <Menu.Target>
          <div style={{ position: "fixed", left: folderMenu?.x ?? 0, top: folderMenu?.y ?? 0, width: 0, height: 0 }} />
        </Menu.Target>
        <Menu.Dropdown data-testid="folder-menu">
          <Menu.Label maw={320} style={{ overflowWrap: "anywhere" }}>{folderMenu?.folder}</Menu.Label>
          <Menu.Item
            leftSection={<IconRefresh size={14} />}
            onClick={() => folderMenu && queueJob({ kind: "crawl", folder: folderMenu.folder, in_library: true }, "Crawl")}
          >
            Crawl this folder (new and changed archives)
          </Menu.Item>
          <Menu.Item
            leftSection={<IconRefresh size={14} />}
            onClick={() => folderMenu && queueJob({ kind: "crawl", folder: folderMenu.folder, in_library: true, force_rescan: true }, "Rescan")}
          >
            Rescan this folder (read every archive again)
          </Menu.Item>
          <Menu.Item
            leftSection={<IconRefresh size={14} />}
            onClick={() => folderMenu && setPatternCrawl({ folder: folderMenu.folder, pattern: folderPattern(folderMenu.folder, libraries.data ?? []) })}
          >
            Crawl with a pattern…
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      <Modal opened={patternCrawl != null} onClose={() => setPatternCrawl(null)} title="Crawl with a pattern">
        {patternCrawl && (
          <Stack>
            <Text size="sm" style={{ overflowWrap: "anywhere" }}>{patternCrawl.folder}</Text>
            <TextInput
              label="Path pattern"
              description="Kept for this folder: later crawls and rescans use it. Read from the library folder's root; empty goes back to the library's pattern."
              value={patternCrawl.pattern}
              onChange={(e) => setPatternCrawl({ ...patternCrawl, pattern: e.currentTarget.value })}
              data-autofocus
            />
            <PatternSample folder={patternCrawl.folder} pattern={patternCrawl.pattern} />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setPatternCrawl(null)}>Cancel</Button>
              <Button
                onClick={async () => {
                  const c = patternCrawl;
                  setPatternCrawl(null);
                  await queueJob({ kind: "crawl", folder: c.folder, in_library: true, pattern: c.pattern.trim() }, "Crawl");
                }}
              >
                Start
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
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
