// Choose a folder on the server (`/api/files/browse`): editors see the
// library's roots, administrators the whole machine.
import { Breadcrumbs, Button, Group, Loader, Modal, ScrollArea, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconArchive, IconFolder, IconFolderUp } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { formatSize } from "../format";

export function FolderPicker(props: { opened: boolean; onClose: () => void; onPick: (path: string) => void }) {
  const [path, setPath] = useState("");
  const listing = useQuery({
    queryKey: ["browse", path],
    queryFn: async () => unwrap(await api.GET("/api/files/browse", { params: { query: { path } } })),
    enabled: props.opened,
  });
  const archives = listing.data?.entries.filter((e) => e.kind === "archive").length ?? 0;
  return (
    <Modal opened={props.opened} onClose={props.onClose} title="Choose a folder on the server" size="lg">
      <Stack gap="xs">
        <Breadcrumbs>
          <UnstyledButton onClick={() => setPath("")}>
            <Text size="sm" c="blue">
              Start
            </Text>
          </UnstyledButton>
          {path && <Text size="sm">{path}</Text>}
        </Breadcrumbs>
        <ScrollArea h={360} type="auto">
          {listing.isPending && <Loader size="sm" />}
          {listing.isError && <Text c="red">{listing.error.message}</Text>}
          {listing.data?.parent && (
            <UnstyledButton display="block" w="100%" py={4} onClick={() => setPath(listing.data!.parent!)}>
              <Group gap="xs">
                <IconFolderUp size={16} />
                <Text size="sm">..</Text>
              </Group>
            </UnstyledButton>
          )}
          {listing.data?.entries.map((e) =>
            e.kind === "folder" ? (
              <UnstyledButton key={e.path} display="block" w="100%" py={4} onClick={() => setPath(e.path)} data-testid="folder-entry">
                <Group gap="xs" wrap="nowrap">
                  <IconFolder size={16} />
                  <Text size="sm" truncate>
                    {e.name}
                  </Text>
                </Group>
              </UnstyledButton>
            ) : (
              <Group key={e.path} gap="xs" py={4} wrap="nowrap" c="dimmed">
                <IconArchive size={16} />
                <Text size="sm" truncate flex={1}>
                  {e.name}
                </Text>
                <Text size="xs">{formatSize(e.size)}</Text>
              </Group>
            ),
          )}
          {listing.data?.truncated && (
            <Text size="xs" c="dimmed">
              (more entries not shown)
            </Text>
          )}
        </ScrollArea>
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            {path ? `${archives} archive(s) directly in this folder` : "Pick a starting point"}
          </Text>
          <Group gap="xs">
            <Button variant="default" onClick={props.onClose}>
              Cancel
            </Button>
            <Button disabled={!path} onClick={() => props.onPick(path)} data-testid="pick-folder">
              Use this folder
            </Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}
