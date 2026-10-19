// The browser: filter pane, table and detail pane (the desktop's main window).
import { Box, Button, Center, Group, Loader, Menu, Modal, SegmentedControl, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconArchive, IconEdit, IconFilterOff, IconSearch, IconSparkles, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";
import type { JobRequest } from "../jobs/jobs";
import { DetailPane } from "./DetailPane";
import { EditorDrawer } from "./EditorDrawer";
import { FilterPane } from "./FilterPane";
import { GridView } from "./GridView";
import { AiTaggerDrawer } from "./AiTaggerDrawer";
import { MassEditDrawer } from "./MassEditDrawer";
import { EMPTY, type FilterSpec, isEmpty } from "./filters";
import { ModelTable, type Sort, useModelIds } from "./ModelTable";
import { NONE, click, prune, type Selection } from "./selection";
import classes from "./BrowserPage.module.css";

const SHOW = ["author", "tags", "source", "date", "sculptor", "collection", "subcollection", "category", "release", "scale", "presupports", "path"];

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
  const [confirmDelete, setConfirmDelete] = useState(false);
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
    <div className={classes.layout}>
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
            {selection.ids.size > 1 ? ` · ${selection.ids.size} selected` : ""}
          </Text>
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
            <GridView ids={order} selected={selection.current} onOpen={(id) => setSelection((s) => click(s, order, id, { ctrl: false, shift: false }))} />
          ) : (
            <ModelTable
              query={query}
              onSort={setSort}
              selection={selection}
              onRowClick={(id, mods) => setSelection((s) => click(s, order, id, mods))}
              editable={editor}
            />
          )}
        </Box>
      </Box>
      <Box className={classes.detail}>
        <DetailPane id={selection.current} onEdit={setEditing} />
      </Box>
      <EditorDrawer id={editing} onClose={() => setEditing(null)} />
      <MassEditDrawer ids={order.filter((i) => selection.ids.has(i))} opened={massEdit} onClose={() => setMassEdit(false)} />
      <AiTaggerDrawer ids={order.filter((i) => selection.ids.has(i))} opened={aiTagger} onClose={() => setAiTagger(false)} />
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
