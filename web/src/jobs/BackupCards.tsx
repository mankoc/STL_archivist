// Maintenance page cards for METADATA.7z sidecars and library backups.
import { ActionIcon, Anchor, Button, Card, Code, Group, Modal, Stack, Table, Text, Title, Tooltip } from "@mantine/core";
import { IconDownload, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { formatSize } from "../format";
import type { Job, JobRequest } from "./jobs";

export function SidecarCard({ editor, start }: { editor: boolean; start: (r: JobRequest) => Promise<void> }) {
  const [confirm, setConfirm] = useState(false);
  return (
    <Card withBorder>
      <Title order={5} mb="xs">
        Metadata sidecars
      </Title>
      <Text size="xs" c="dimmed" mb="xs">
        METADATA.7z in each archive folder lets a new library restore its records without re-crawling.
      </Text>
      <Group gap="xs">
        <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "generate_sidecars" })}>
          Generate sidecars
        </Button>
        <Button size="xs" variant="default" color="red" disabled={!editor} onClick={() => setConfirm(true)}>
          Remove all sidecars
        </Button>
      </Group>
      <Modal opened={confirm} onClose={() => setConfirm(false)} title="Remove all sidecars">
        <Stack>
          <Text size="sm">Delete the METADATA.7z file of every folder holding catalogued archives?</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirm(false)}>Cancel</Button>
            <Button color="red" onClick={() => { setConfirm(false); void start({ kind: "remove_sidecars" }); }}>Remove</Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}

/** Backups (administrators): create, download, delete; restoring is offline. */
export function BackupCard({ jobs, start }: { jobs: Job[] | undefined; start: (r: JobRequest) => Promise<void> }) {
  const queryClient = useQueryClient();
  // Refetch when the newest backup job changes state.
  const last = (jobs ?? []).filter((j) => j.kind === "backup").reduce<Job | undefined>((a, j) => (!a || j.id > a.id ? j : a), undefined);
  const backups = useQuery({
    queryKey: ["backups", last?.id, last?.state],
    queryFn: async () => unwrap(await api.GET("/api/backups")),
  });
  const remove = async (name: string) => {
    await api.DELETE("/api/backups/{name}", { params: { path: { name } } });
    await queryClient.invalidateQueries({ queryKey: ["backups"] });
  };
  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Title order={5}>Backups</Title>
        <Button size="xs" variant="default" onClick={() => start({ kind: "backup" })} data-testid="backup">
          Back up now
        </Button>
      </Group>
      <Text size="xs" c="dimmed" mb="xs">
        The database, thumbnails and library settings in one .7z, readable by the desktop app's Backup / Restore. To restore,
        stop the server and run <Code>stl-server restore FILE.7z --data-dir DIR</Code>.
      </Text>
      {backups.data?.length ? (
        <Table fz="sm" verticalSpacing={4}>
          <Table.Tbody>
            {backups.data.map((b) => (
              <Table.Tr key={b.name} data-testid="backup-row">
                <Table.Td>
                  <Anchor href={`/api/backups/${encodeURIComponent(b.name)}`} size="sm" download>
                    {b.name}
                  </Anchor>
                </Table.Td>
                <Table.Td w={90} ta="right">{formatSize(b.size)}</Table.Td>
                <Table.Td w={70}>
                  <Group gap={4} wrap="nowrap">
                    <Tooltip label="Download">
                      <ActionIcon variant="subtle" component="a" href={`/api/backups/${encodeURIComponent(b.name)}`} download>
                        <IconDownload size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Delete">
                      <ActionIcon variant="subtle" color="red" onClick={() => remove(b.name)}>
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      ) : (
        <Text size="sm" c="dimmed">No backups on the server yet.</Text>
      )}
    </Card>
  );
}
