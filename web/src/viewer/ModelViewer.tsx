// The model viewer: a model's archive, its model files on the left (read from
// the archive now) and the chosen one drawn by MeshPeek's renderer. In the
// desktop app, with MeshPeek installed and the archive reachable from this
// computer, "Open in MeshPeek" opens the chosen file there.
import { Alert, Box, Button, Center, Group, Loader, Modal, NavLink, ScrollArea, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconExternalLink } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, unwrap } from "../api/client";
import { inDesktopShell, meshpeekInstalled, openInMeshPeek } from "../desktop";
import { formatSize } from "../format";
import { ViewerCanvas } from "./ViewerCanvas";

export function ModelViewer({ id, title, file, clientPath, onClose }: {
  id: number;
  title: string;
  /** The model file to show first; the archive's first one when null. */
  file: string | null;
  /** The archive's path on this computer, when it is reachable from here. */
  clientPath: string | null;
  onClose: () => void;
}) {
  const files = useQuery({
    queryKey: ["model-viewable", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}/viewable", { params: { path: { id } } })),
  });
  const meshpeek = useQuery({ queryKey: ["meshpeek-installed"], queryFn: meshpeekInstalled, enabled: inDesktopShell(), staleTime: Infinity });
  const [shown, setShown] = useState<string | null>(file);
  useEffect(() => {
    if (shown == null && files.data?.length) setShown(files.data[0].name);
  }, [shown, files.data]);

  const list = files.data ?? [];
  return (
    <Modal
      opened
      onClose={onClose}
      fullScreen
      padding="xs"
      title={
        <Group gap="sm" wrap="nowrap">
          <Text fw={600} truncate>{title}</Text>
          {shown && <Text size="sm" c="dimmed" truncate>{shown}</Text>}
          {shown && clientPath && meshpeek.data && (
            <Button
              size="compact-xs"
              variant="light"
              leftSection={<IconExternalLink size={12} />}
              onClick={() => openInMeshPeek(clientPath, shown).catch((e) => notifications.show({ color: "red", message: String(e) }))}
            >
              Open in MeshPeek
            </Button>
          )}
        </Group>
      }
    >
      <Group wrap="nowrap" gap="xs" align="stretch" h="calc(100vh - 70px)" data-testid="model-viewer">
        {list.length > 1 && (
          <ScrollArea w={260} style={{ flexShrink: 0 }}>
            {list.map((f) => (
              <NavLink
                key={f.name}
                active={f.name === shown}
                onClick={() => setShown(f.name)}
                label={<Text size="xs" style={{ wordBreak: "break-all" }}>{f.name}</Text>}
                description={formatSize(f.size)}
              />
            ))}
          </ScrollArea>
        )}
        <Box style={{ flex: 1, minWidth: 0 }}>
          {files.isPending && !shown ? (
            <Center h="100%"><Loader /></Center>
          ) : files.isError && !shown ? (
            <Center h="100%"><Alert color="red">{files.error.message}</Alert></Center>
          ) : shown ? (
            <ViewerCanvas id={id} name={shown} />
          ) : (
            <Center h="100%"><Text c="dimmed">No model files the viewer can show in this archive.</Text></Center>
          )}
        </Box>
      </Group>
    </Modal>
  );
}
