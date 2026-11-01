// Choose a folder on the server (`/api/files/browse`): editors see the
// library's roots, administrators the whole machine. With `canCreate`, a new
// folder can be made in the listed one (`/api/files/folder`).
import { Breadcrumbs, Button, Group, Loader, Modal, ScrollArea, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { IconArchive, IconFolder, IconFolderPlus, IconFolderUp } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api, unwrap } from "../api/client";
import { formatSize } from "../format";

export function FolderPicker(props: { opened: boolean; onClose: () => void; onPick: (path: string) => void; canCreate?: boolean }) {
  const [path, setPath] = useState("");
  const [newName, setNewName] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const go = (p: string) => {
    setPath(p);
    setNewName(null);
    setCreateError(null);
  };
  const create = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const made = unwrap(await api.POST("/api/files/folder", { body: { parent: path, name: newName ?? "" } }));
      go(made.path);
    } catch (err) {
      setCreateError((err as Error).message);
    }
  };
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
          <UnstyledButton onClick={() => go("")}>
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
            <UnstyledButton display="block" w="100%" py={4} onClick={() => go(listing.data!.parent!)}>
              <Group gap="xs">
                <IconFolderUp size={16} />
                <Text size="sm">..</Text>
              </Group>
            </UnstyledButton>
          )}
          {listing.data?.entries.map((e) =>
            e.kind === "folder" ? (
              <UnstyledButton key={e.path} display="block" w="100%" py={4} onClick={() => go(e.path)} data-testid="folder-entry">
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
        {props.canCreate && path && newName !== null && (
          <form onSubmit={create}>
            <Group gap="xs" align="flex-start">
              <TextInput size="xs" placeholder="New folder name" value={newName} autoFocus style={{ flex: 1 }}
                onChange={(e) => { setNewName(e.currentTarget.value); setCreateError(null); }} error={createError} data-testid="new-folder-name" />
              <Button size="xs" type="submit" disabled={!newName.trim()} data-testid="create-folder">Create</Button>
              <Button size="xs" variant="default" onClick={() => go(path)}>Cancel</Button>
            </Group>
          </form>
        )}
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            {path ? `${archives} archive(s) directly in this folder` : "Pick a starting point"}
          </Text>
          <Group gap="xs">
            {props.canCreate && (
              <Button variant="default" leftSection={<IconFolderPlus size={16} />} disabled={!path || newName !== null}
                onClick={() => setNewName("")} data-testid="new-folder">
                New folder
              </Button>
            )}
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
