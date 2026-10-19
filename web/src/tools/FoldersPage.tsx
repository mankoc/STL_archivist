// The archive browser: the folders holding catalogued archives, and the
// archives of the chosen one as thumbnails.
import { Box, Checkbox, Group, Modal, NavLink, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, unwrap } from "../api/client";
import { CellGrid } from "../browser/CellGrid";
import { DetailPane } from "../browser/DetailPane";

/** The last two path components, for a compact list label. */
export function folderLabel(folder: string): string {
  const parts = folder.split("/").filter(Boolean);
  return parts.slice(-2).join("/") || folder;
}

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
  const cells = useQuery({
    queryKey: ["folder-cells", folder, subfolders],
    queryFn: async () => unwrap(await api.POST("/api/views/folder", { body: { folder: folder!, include_subfolders: subfolders } })),
    enabled: folder != null,
  });

  return (
    <Group align="stretch" gap={0} wrap="nowrap" style={{ height: "100%" }}>
      <Stack w={320} gap="xs" p="sm" style={{ borderRight: "1px solid var(--mantine-color-default-border)", flex: "none" }}>
        <TextInput size="xs" placeholder="Filter folders" value={filter} onChange={(e) => setFilter(e.currentTarget.value)} />
        <Checkbox size="xs" label="Include subfolders" checked={subfolders} onChange={(e) => setSubfolders(e.currentTarget.checked)} />
        <ScrollArea style={{ flex: 1 }}>
          {shown.map((f) => (
            <NavLink key={f} label={folderLabel(f)} description={f} active={f === folder} onClick={() => setFolder(f)} data-testid="folder" />
          ))}
        </ScrollArea>
      </Stack>
      <Box style={{ flex: 1, minWidth: 0 }}>
        {folder == null ? (
          <Text c="dimmed" p="md">Choose a folder.</Text>
        ) : (
          <Stack gap={0} style={{ height: "100%" }}>
            <Text size="xs" c="dimmed" px="sm" pt="xs">{folder} · {cells.data?.length ?? "…"} archive(s)</Text>
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
