// Settings cards for METADATA.7z sidecars (their settings and jobs) and library backups.
import { ActionIcon, Anchor, Button, Card, Checkbox, Code, Group, Modal, Stack, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDownload, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { formatSize } from "../format";
import { type LibrarySettings, SETTINGS_QUERY } from "../settings/librarySettings";
import type { Job, JobRequest } from "./jobs";

/**
 * Sidecars: whether the server writes them and with thumbnails (administrators;
 * saved as soon as ticked, over the saved settings), and their jobs (editors).
 */
export function SidecarCard({ editor, admin, start }: { editor: boolean; admin: boolean; start: (r: JobRequest) => Promise<void> }) {
  const [confirm, setConfirm] = useState(false);
  const queryClient = useQueryClient();
  const settings = useQuery(SETTINGS_QUERY);
  const s = settings.data;
  const change = async (patch: Partial<LibrarySettings>) => {
    if (!s) return;
    try {
      const saved = unwrap(await api.PUT("/api/settings/library", { body: { ...s, ...patch } }));
      queryClient.setQueryData(["settings"], saved);
      notifications.show({ message: "Sidecar settings saved." });
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
  };
  return (
    <Card withBorder>
      <Title order={5} mb="xs">
        Metadata sidecars
      </Title>
      <Text size="xs" c="dimmed" mb="xs">
        METADATA.7z in each archive folder lets a new library restore its records without re-crawling.
      </Text>
      {s && (
        <Stack gap="xs" mb="sm">
          <Checkbox label="Write METADATA.7z into archive folders" checked={s.metadata_sidecar_enabled} disabled={!admin}
            onChange={(e) => change({ metadata_sidecar_enabled: e.currentTarget.checked })} data-testid="sidecars-enabled" />
          <Checkbox label="Include thumbnails in sidecars" checked={s.metadata_thumbnails} disabled={!admin}
            onChange={(e) => change({ metadata_thumbnails: e.currentTarget.checked })} />
          {!admin && <Text size="xs" c="dimmed">Only administrators can change these settings.</Text>}
        </Stack>
      )}
      <Group gap="xs">
        <Tooltip label={s?.metadata_sidecar_enabled === false ? "Sidecars are off: tick “Write METADATA.7z” first"
          : "Write METADATA.7z again in every folder holding catalogued archives, with the settings above"}>
          <Button size="xs" variant="default" disabled={!editor || s?.metadata_sidecar_enabled === false}
            onClick={() => start({ kind: "generate_sidecars" })} data-testid="rebuild-sidecars">
            Rebuild all sidecars
          </Button>
        </Tooltip>
        <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "clean_sidecars" })}
          title="Rewrite out-of-date sidecars, delete those whose archives are all gone and leftover temporary files">
          Clean sidecars
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

/** Delete every record (administrators), confirmed by typing DELETE. */
export function DangerCard({ start }: { start: (r: JobRequest) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  return (
    <Card withBorder>
      <Title order={5} mb="xs">
        Delete all records
      </Title>
      <Text size="xs" c="dimmed" mb="xs">
        Empties the library: every model, lookup, crawled folder and thumbnail. Archive files are not touched. Back up first.
      </Text>
      <Button size="xs" color="red" variant="light" onClick={() => setOpen(true)}>
        Delete all records…
      </Button>
      <Modal opened={open} onClose={() => setOpen(false)} title="Delete all records">
        <Stack>
          <Text size="sm">Type DELETE to empty the library.</Text>
          <TextInput value={typed} onChange={(e) => setTyped(e.currentTarget.value)} data-testid="confirm-text" />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setOpen(false)}>Cancel</Button>
            <Button color="red" disabled={typed !== "DELETE"} onClick={() => { setOpen(false); setTyped(""); void start({ kind: "clear_library", confirm: "DELETE" }); }}>
              Delete everything
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}
