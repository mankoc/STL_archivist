// Jobs → Libraries: the relocatable collections crawls catalogue. Each has a
// name, a root on the server, a path pattern and records stored relative to
// the root; it can be crawled, monitored, renamed, moved to a new root (checked
// first, nothing re-read) or removed.
import { ActionIcon, Alert, Badge, Button, Card, Checkbox, Group, Modal, Stack, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowsMove, IconEye, IconEyeOff, IconFolderPlus, IconPencil, IconPlayerPlay, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { HelpLink } from "../help/HelpPage";
import { FolderPicker } from "./FolderPicker";
import { type JobRequest } from "./jobs";
import { libraryBadges, type LibraryInfo, type Relocation, relocationText, removalText } from "./libraries";

const fail = (title: string) => (err: unknown) => notifications.show({ color: "red", title, message: (err as Error).message });

export function LibrariesCard({ editor, onCrawl, start }: {
  editor: boolean;
  /** Open the crawl dialog for a folder (a library's root) with its pattern. */
  onCrawl: (folder: string, pattern: string) => void;
  start: (r: JobRequest) => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const libs = useQuery({ queryKey: ["libraries"], queryFn: async () => unwrap(await api.GET("/api/libraries")) });
  const all = libs.data ?? [];
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["libraries"] });
  const [picker, setPicker] = useState(false);
  const [renaming, setRenaming] = useState<{ lib: LibraryInfo; name: string } | null>(null);
  const [removing, setRemoving] = useState<LibraryInfo | null>(null);
  const [moving, setMoving] = useState<LibraryInfo | null>(null);

  const patch = async (id: number, body: { name?: string; pattern?: string; monitored?: boolean }) => {
    try {
      unwrap(await api.PATCH("/api/libraries/{id}", { params: { path: { id } }, body }));
    } catch (err) {
      fail("Library")(err);
    }
    await refresh();
  };

  return (
    <Card withBorder data-testid="libraries-card">
      <Group justify="space-between" mb="xs">
        <Group gap={4}>
          <Title order={5}>Libraries</Title>
          <HelpLink topic="libraries" label="Help: libraries" />
        </Group>
        {editor && (
          <Group gap="xs">
            <Button size="xs" variant="light" leftSection={<IconFolderPlus size={14} />} onClick={() => setPicker(true)}>
              Add library
            </Button>
            <Button size="xs" variant="light" leftSection={<IconPlayerPlay size={14} />} disabled={!all.length} onClick={() => void start({ kind: "update_folders" })}>
              Update all
            </Button>
          </Group>
        )}
      </Group>
      <Table fz="sm" highlightOnHover>
        <Table.Tbody>
          {all.map((l) => (
            <Table.Tr key={l.id} data-testid="library-row">
              <Table.Td style={{ wordBreak: "break-all" }}>
                <Group gap={6}>
                  <Text size="sm" fw={600}>{l.name}</Text>
                  <Text size="xs" c="dimmed">{l.models} record{l.models === 1 ? "" : "s"}</Text>
                  {libraryBadges(l).map(([color, label]) => (
                    <Badge key={label} size="xs" variant="light" color={color}>{label}</Badge>
                  ))}
                </Group>
                <Text size="xs" c="dimmed">{l.root || "(no folder)"}{l.pattern ? ` · ${l.pattern}` : ""}</Text>
              </Table.Td>
              {editor && (
                <Table.Td w={190}>
                  <Group gap={2} wrap="nowrap">
                    <Tooltip label={l.monitored ? "Stop monitoring" : "Monitor: check for new or changed archives and crawl automatically"}>
                      <ActionIcon variant={l.monitored ? "light" : "subtle"} aria-label={l.monitored ? "Stop monitoring" : "Monitor"} onClick={() => void patch(l.id, { monitored: !l.monitored })}>
                        {l.monitored ? <IconEye size={16} /> : <IconEyeOff size={16} />}
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Crawl">
                      <ActionIcon variant="subtle" aria-label="Crawl" disabled={!l.reachable} onClick={() => onCrawl(l.root, l.pattern ?? "")}>
                        <IconPlayerPlay size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Rename">
                      <ActionIcon variant="subtle" aria-label="Rename" onClick={() => setRenaming({ lib: l, name: l.name })}>
                        <IconPencil size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Move to another folder (after moving or re-mounting its files)">
                      <ActionIcon variant={l.reachable ? "subtle" : "light"} color={l.reachable ? undefined : "red"} aria-label="Move library" onClick={() => setMoving(l)}>
                        <IconArrowsMove size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Remove the library">
                      <ActionIcon variant="subtle" color="red" aria-label="Remove library" onClick={() => setRemoving(l)}>
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              )}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {libs.data?.length === 0 && (
        <Text size="sm" c="dimmed">No libraries yet{editor ? ": add a folder of archives to catalogue it." : "."}</Text>
      )}

      <FolderPicker
        opened={picker}
        onClose={() => setPicker(false)}
        onPick={async (path) => {
          setPicker(false);
          try {
            const l = unwrap(await api.POST("/api/libraries", { body: { path } }));
            await refresh();
            onCrawl(l.root, l.pattern ?? "");
          } catch (err) {
            fail("Add library")(err);
          }
        }}
      />

      <Modal opened={renaming != null} onClose={() => setRenaming(null)} title="Rename library" size="sm">
        {renaming && (
          <Stack>
            <TextInput label="Name" value={renaming.name} onChange={(e) => setRenaming({ ...renaming, name: e.currentTarget.value })} data-autofocus />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setRenaming(null)}>Cancel</Button>
              <Button
                disabled={!renaming.name.trim()}
                onClick={() => {
                  void patch(renaming.lib.id, { name: renaming.name.trim() });
                  setRenaming(null);
                }}
              >
                Rename
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      <Modal opened={removing != null} onClose={() => setRemoving(null)} title={`Remove ${removing?.name ?? ""}`} size="sm">
        {removing && (
          <Stack>
            <Text size="sm">{removalText(removing, all)}</Text>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setRemoving(null)}>Cancel</Button>
              <Button
                color="red"
                data-testid="confirm-remove-library"
                onClick={async () => {
                  const l = removing;
                  setRemoving(null);
                  try {
                    unwrap(await api.POST("/api/libraries/{id}/remove", { params: { path: { id: l.id } } }));
                  } catch (err) {
                    fail("Remove library")(err);
                  }
                  await refresh();
                }}
              >
                Remove
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      {moving && <MoveLibrary lib={moving} onClose={() => { setMoving(null); void refresh(); }} />}
    </Card>
  );
}

/** Move a library to a new root: check every archive there first, then move. */
function MoveLibrary({ lib, onClose }: { lib: LibraryInfo; onClose: () => void }) {
  const [root, setRoot] = useState("");
  const [fixCase, setFixCase] = useState(true);
  const [picker, setPicker] = useState(false);
  const [checkJob, setCheckJob] = useState<number | null>(null);
  const job = useQuery({
    queryKey: ["relocation", checkJob],
    queryFn: async () => unwrap(await api.GET("/api/jobs/{id}", { params: { path: { id: checkJob! } } })),
    enabled: checkJob != null,
    refetchInterval: (q) => (q.state.data && !["queued", "running"].includes(q.state.data.job.state) ? false : 700),
  });
  const state = job.data?.job.state;
  const result = job.data?.result && job.data.result.type === "relocation" ? (job.data.result as Relocation) : null;
  const running = checkJob != null && (state === undefined || state === "queued" || state === "running");

  const submit = async (apply: boolean) => {
    try {
      const j = unwrap(await api.POST("/api/jobs", { body: { kind: "relocate_library", id: lib.id, root: root.trim(), apply, fix_case: fixCase } }));
      if (apply) {
        notifications.show({ message: `Moving ${lib.name}: follow the job on the right.` });
        onClose();
      } else {
        setCheckJob(j.id);
      }
    } catch (err) {
      fail("Move library")(err);
    }
  };

  return (
    <Modal opened onClose={onClose} title={`Move ${lib.name}`} size="lg">
      <Stack>
        <Text size="sm">
          After moving or re-mounting the library's folder (another disk, another machine), point it at the new folder.
          Records, tags, edits and thumbnails stay; nothing is re-read.
        </Text>
        <Text size="xs" c="dimmed">Now: {lib.root || "(no folder)"}{lib.reachable ? "" : " — not found"}</Text>
        <Group align="flex-end" gap="xs">
          <TextInput label="New folder" value={root} onChange={(e) => { setRoot(e.currentTarget.value); setCheckJob(null); }} style={{ flex: 1 }} data-testid="new-root" />
          <Button variant="default" onClick={() => setPicker(true)}>Browse…</Button>
        </Group>
        <Checkbox label="Adopt the letter case found on disk (after moving from Windows to Linux)" checked={fixCase} onChange={(e) => setFixCase(e.currentTarget.checked)} />
        {job.data?.job.state === "failed" && <Alert color="red" p="xs">{job.data.job.message}</Alert>}
        {result && (
          <Alert color={result.missing ? "orange" : "green"} p="xs" data-testid="relocation-check">
            <Text size="sm">{relocationText(result)}.</Text>
            {result.missing_examples.length > 0 && (
              <Text size="xs" c="dimmed" mt={4}>Not found: {result.missing_examples.slice(0, 5).join(", ")}{result.missing > 5 ? ", …" : ""}</Text>
            )}
            {result.missing > 0 && <Text size="xs" mt={4}>Records not found stay, and are marked missing by the next crawl.</Text>}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button variant="light" disabled={!root.trim() || running} loading={running} onClick={() => void submit(false)} data-testid="check-move">
            Check
          </Button>
          <Button disabled={!result || running} onClick={() => void submit(true)} data-testid="apply-move">
            Move
          </Button>
        </Group>
      </Stack>
      <FolderPicker opened={picker} onClose={() => setPicker(false)} onPick={(p) => { setPicker(false); setRoot(p); setCheckJob(null); }} />
    </Modal>
  );
}
