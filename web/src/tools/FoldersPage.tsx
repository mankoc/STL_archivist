// The archive browser: the folders holding catalogued archives as a tree
// (folderTree.ts), and the archives of the chosen one as thumbnails. Clicking
// a folder shows its archives and opens it; its chevron opens or closes it.
import { Box, Checkbox, getTreeExpandedState, Group, Modal, ScrollArea, Stack, Text, TextInput, Tree, useTree } from "@mantine/core";
import { IconChevronRight, IconFolder } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, unwrap } from "../api/client";
import { CellGrid } from "../browser/CellGrid";
import { DetailPane } from "../browser/DetailPane";
import { folderTree, nodesTo } from "./folderTree";

export function FoldersPage() {
  const folders = useQuery({ queryKey: ["folders"], queryFn: async () => unwrap(await api.GET("/api/folders")) });
  const [filter, setFilter] = useState("");
  const [folder, setFolder] = useState<string | null>(null);
  const [subfolders, setSubfolders] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const shown = useMemo(
    () => (folders.data ?? []).filter((f) => f.toLowerCase().includes(filter.toLowerCase())),
    [folders.data, filter],
  );
  const tree = useMemo(() => folderTree(shown), [shown]);
  const controller = useTree();
  const loaded = folders.data != null;
  // The first level open (and the way to the chosen folder); everything while filtering.
  useEffect(() => {
    const opened = filter ? "*" : [...tree.data.map((n) => n.value), ...(folder ? nodesTo(tree.data, folder) : [])];
    controller.setExpandedState(getTreeExpandedState(tree.data, opened));
    // (not on every refetch, which would close what was opened; not on the controller: it is a new object each render)
  }, [filter, loaded]);
  const cells = useQuery({
    queryKey: ["folder-cells", folder, subfolders],
    queryFn: async () => unwrap(await api.POST("/api/views/folder", { body: { folder: folder!, include_subfolders: subfolders } })),
    enabled: folder != null,
  });

  // Enter shows the focused folder's archives (Mantine's tree moves the focus).
  const onTreeKey = (e: React.KeyboardEvent) => {
    if (e.key !== "Enter") return;
    const value = (e.target as HTMLElement).closest("[role=treeitem]")?.getAttribute("data-value");
    if (value == null) return;
    e.preventDefault();
    setFolder(value);
  };

  return (
    <Group align="stretch" gap={0} wrap="nowrap" style={{ height: "100%" }}>
      <Stack w={320} gap="xs" p="sm" style={{ borderRight: "1px solid var(--mantine-color-default-border)", flex: "none" }}>
        <TextInput size="xs" placeholder="Filter folders" value={filter} onChange={(e) => setFilter(e.currentTarget.value)} />
        <Checkbox size="xs" label="Include subfolders" checked={subfolders} onChange={(e) => setSubfolders(e.currentTarget.checked)} />
        <ScrollArea style={{ flex: 1 }}>
          <div onKeyDownCapture={onTreeKey}>
            <Tree
              data={tree.data}
              tree={controller}
              levelOffset={14}
              renderNode={({ node, expanded, hasChildren, elementProps }) => (
                <Group
                  gap={4}
                  wrap="nowrap"
                  {...elementProps}
                  onClick={() => {
                    setFolder(node.value);
                    controller.expand(node.value);
                  }}
                  style={{
                    ...elementProps.style,
                    minHeight: 26,
                    borderRadius: 4,
                    background: node.value === folder ? "var(--mantine-primary-color-light)" : undefined,
                  }}
                  data-testid="folder"
                >
                  {hasChildren ? (
                    <IconChevronRight
                      size={14}
                      style={{ flexShrink: 0, cursor: "pointer", transform: expanded ? "rotate(90deg)" : undefined }}
                      onClick={(e) => {
                        e.stopPropagation();
                        controller.toggleExpanded(node.value);
                      }}
                    />
                  ) : (
                    <span style={{ width: 14, flexShrink: 0 }} />
                  )}
                  <IconFolder size={14} style={{ flexShrink: 0 }} />
                  <Text size="xs" truncate style={{ flex: 1 }} c={tree.archives.has(node.value) ? undefined : "dimmed"} title={node.value}>
                    {node.label}
                  </Text>
                </Group>
              )}
            />
          </div>
        </ScrollArea>
      </Stack>
      <Box style={{ flex: 1, minWidth: 0 }}>
        {folder == null ? (
          <Text c="dimmed" p="md">Choose a folder.</Text>
        ) : (
          <Stack gap={0} style={{ height: "100%" }}>
            <Text size="xs" c="dimmed" px="sm" pt="xs">{folder} · {cells.data?.length ?? "…"} archive(s)</Text>
            {cells.data?.length === 0 && !subfolders && !tree.archives.has(folder) && (
              <Text size="sm" c="dimmed" px="sm" pt="xs">No archives are directly in this folder: tick Include subfolders to see those below it.</Text>
            )}
            <Box style={{ flex: 1, minHeight: 0 }}>
              <CellGrid count={cells.data?.length ?? 0} cell={(i) => cells.data?.[i]} selected={open} onOpen={setOpen} />
            </Box>
          </Stack>
        )}
      </Box>
      <Modal opened={open != null} onClose={() => setOpen(null)} size="md" title="Model">
        <Box h="70vh">
          <DetailPane id={open} onEdit={() => {}} />
        </Box>
      </Modal>
    </Group>
  );
}
