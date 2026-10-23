// Libraries page: the relocatable collections crawls catalogue. A library
// has a name and one or more folders; each folder has a root on the server, a
// path pattern and records stored relative to the root. A library can be
// renamed, given another folder or removed; a folder can be crawled,
// monitored, moved to a new root (checked first, nothing re-read), moved to
// another library with its records, or removed.
import { ActionIcon, Alert, Badge, Button, Card, Checkbox, Group, Modal, Select, Stack, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowsExchange, IconArrowsMove, IconEye, IconEyeOff, IconFolderPlus, IconPencil, IconPlayerPlay, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";

import { api, unwrap } from "../api/client";
import { HelpLink } from "../help/HelpPage";
import { FolderPicker } from "./FolderPicker";
import { type JobRequest } from "./jobs";
import {
  folderAt,
  folderBadges,
  folderMoveText,
  folderRemovalText,
  type LibraryFolder,
  type LibraryInfo,
  libraryRemovalText,
  moveTargets,
  type Relocation,
  relocationText,
} from "./libraries";

const fail = (title: string) => (err: unknown) => notifications.show({ color: "red", title, message: (err as Error).message });

/** What is being removed: a whole library, or one of its folders. */
type Removal = { lib: LibraryInfo; folder?: LibraryFolder };

/** The move-to-library dialog's choice of a new library. */
const NEW = "new";

export function LibrariesCard({ editor, onCrawl, start }: {
  editor: boolean;
  /** Open the crawl dialog for a folder (a library folder's root) with its pattern. */
  onCrawl: (folder: string, pattern: string) => void;
  start: (r: JobRequest) => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const libs = useQuery({ queryKey: ["libraries"], queryFn: async () => unwrap(await api.GET("/api/libraries")) });
  const all = libs.data ?? [];
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["libraries"] });
  /** The folder picker is open: for a new library (`null`) or to add a folder to one. */
  const [adding, setAdding] = useState<LibraryInfo | null | undefined>(undefined);
  const [renaming, setRenaming] = useState<{ lib: LibraryInfo; name: string } | null>(null);
  const [removing, setRemoving] = useState<Removal | null>(null);
  const [moving, setMoving] = useState<LibraryFolder | null>(null);
  const [transfer, setTransfer] = useState<{ lib: LibraryInfo; folder: LibraryFolder; to: string | null; name: string } | null>(null);

  const rename = async (id: number, name: string) => {
    try {
      unwrap(await api.PATCH("/api/libraries/{id}", { params: { path: { id } }, body: { name } }));
    } catch (err) {
      fail("Library")(err);
    }
    await refresh();
  };
  const patchFolder = async (id: number, body: { pattern?: string; monitored?: boolean; library?: number; new_library?: string }) => {
    try {
      unwrap(await api.PATCH("/api/libraries/folders/{id}", { params: { path: { id } }, body }));
    } catch (err) {
      fail("Library folder")(err);
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
            <Button size="xs" variant="light" leftSection={<IconFolderPlus size={14} />} onClick={() => setAdding(null)}>
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
            <Fragment key={l.id}>
              <Table.Tr data-testid="library-row">
                <Table.Td>
                  <Group gap={6}>
                    <Text size="sm" fw={600}>{l.name}</Text>
                    <Text size="xs" c="dimmed">
                      {l.models} record{l.models === 1 ? "" : "s"}
                      {l.folders.length > 1 ? ` · ${l.folders.length} folders` : ""}
                    </Text>
                  </Group>
                </Table.Td>
                {editor && (
                  <Table.Td w={220}>
                    <Group gap={2} wrap="nowrap">
                      <Tooltip label="Add a folder to this library">
                        <ActionIcon variant="subtle" aria-label="Add folder" onClick={() => setAdding(l)}>
                          <IconFolderPlus size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Rename">
                        <ActionIcon variant="subtle" aria-label="Rename" onClick={() => setRenaming({ lib: l, name: l.name })}>
                          <IconPencil size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Remove the library and all its folders">
                        <ActionIcon variant="subtle" color="red" aria-label="Remove library" onClick={() => setRemoving({ lib: l })}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                )}
              </Table.Tr>
              {l.folders.map((f) => (
                <Table.Tr key={f.id} data-testid="library-folder-row">
                  <Table.Td pl="xl" style={{ wordBreak: "break-all" }}>
                    <Group gap={6}>
                      <Text size="sm">{f.root || "(no folder)"}</Text>
                      {l.folders.length > 1 && <Text size="xs" c="dimmed">{f.models} record{f.models === 1 ? "" : "s"}</Text>}
                      {folderBadges(f).map(([color, label]) => (
                        <Badge key={label} size="xs" variant="light" color={color}>{label}</Badge>
                      ))}
                    </Group>
                    {f.pattern && <Text size="xs" c="dimmed">{f.pattern}</Text>}
                  </Table.Td>
                  {editor && (
                    <Table.Td w={220}>
                      <Group gap={2} wrap="nowrap">
                        <Tooltip label={f.monitored ? "Stop monitoring" : "Monitor: check for new or changed archives and crawl automatically"}>
                          <ActionIcon variant={f.monitored ? "light" : "subtle"} aria-label={f.monitored ? "Stop monitoring" : "Monitor"} onClick={() => void patchFolder(f.id, { monitored: !f.monitored })}>
                            {f.monitored ? <IconEye size={16} /> : <IconEyeOff size={16} />}
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Crawl">
                          <ActionIcon variant="subtle" aria-label="Crawl" disabled={!f.reachable} onClick={() => onCrawl(f.root, f.pattern ?? "")}>
                            <IconPlayerPlay size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Move to another folder (after moving or re-mounting its files)">
                          <ActionIcon variant={f.reachable ? "subtle" : "light"} color={f.reachable ? undefined : "red"} aria-label="Move folder" onClick={() => setMoving(f)}>
                            <IconArrowsMove size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Move to another library, with its records">
                          <ActionIcon
                            variant="subtle"
                            aria-label="Move to another library"
                            onClick={() => setTransfer({ lib: l, folder: f, to: moveTargets(l, all)[0]?.value ?? NEW, name: "" })}
                          >
                            <IconArrowsExchange size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Remove this folder from the library">
                          <ActionIcon variant="subtle" color="red" aria-label="Remove folder" onClick={() => setRemoving({ lib: l, folder: f })}>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  )}
                </Table.Tr>
              ))}
            </Fragment>
          ))}
        </Table.Tbody>
      </Table>
      {libs.data?.length === 0 && (
        <Text size="sm" c="dimmed">No libraries yet{editor ? ": add a folder of archives to catalogue it." : "."}</Text>
      )}

      <FolderPicker
        opened={adding !== undefined}
        onClose={() => setAdding(undefined)}
        onPick={async (path) => {
          const into = adding;
          setAdding(undefined);
          try {
            const l = unwrap(await api.POST("/api/libraries", { body: into ? { path, library: into.id } : { path } }));
            await refresh();
            const f = folderAt(l, path);
            if (f) onCrawl(f.root, f.pattern ?? "");
          } catch (err) {
            fail(into ? `Add a folder to ${into.name}` : "Add library")(err);
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
                  void rename(renaming.lib.id, renaming.name.trim());
                  setRenaming(null);
                }}
              >
                Rename
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      <Modal
        opened={removing != null}
        onClose={() => setRemoving(null)}
        title={removing?.folder ? `Remove ${removing.folder.root} from ${removing.lib.name}` : `Remove ${removing?.lib.name ?? ""}`}
        size="sm"
      >
        {removing && (
          <Stack>
            <Text size="sm">{removing.folder ? folderRemovalText(removing.folder, removing.lib, all) : libraryRemovalText(removing.lib, all)}</Text>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setRemoving(null)}>Cancel</Button>
              <Button
                color="red"
                data-testid="confirm-remove-library"
                onClick={async () => {
                  const r = removing;
                  setRemoving(null);
                  try {
                    if (r.folder) unwrap(await api.POST("/api/libraries/folders/{id}/remove", { params: { path: { id: r.folder.id } } }));
                    else unwrap(await api.POST("/api/libraries/{id}/remove", { params: { path: { id: r.lib.id } } }));
                  } catch (err) {
                    fail("Remove")(err);
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

      <Modal opened={transfer != null} onClose={() => setTransfer(null)} title={`Move ${transfer?.folder.root ?? ""} to another library`} size="md">
        {transfer && (
          <Stack>
            <Select
              label="Library"
              data={[...moveTargets(transfer.lib, all), { value: NEW, label: "A new library…" }]}
              value={transfer.to}
              onChange={(to) => setTransfer({ ...transfer, to })}
              allowDeselect={false}
              data-testid="move-to-library"
            />
            {transfer.to === NEW && (
              <TextInput label="Name of the new library" value={transfer.name} onChange={(e) => setTransfer({ ...transfer, name: e.currentTarget.value })} data-autofocus />
            )}
            <Text size="sm">{folderMoveText(transfer.lib)}</Text>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setTransfer(null)}>Cancel</Button>
              <Button
                disabled={!transfer.to || (transfer.to === NEW && !transfer.name.trim())}
                onClick={() => {
                  const t = transfer;
                  setTransfer(null);
                  void patchFolder(t.folder.id, t.to === NEW ? { new_library: t.name.trim() } : { library: Number(t.to) });
                }}
              >
                Move
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      {moving && <MoveFolder folder={moving} onClose={() => { setMoving(null); void refresh(); }} />}
    </Card>
  );
}

/** Move a library folder to a new root: check every archive there first, then move. */
function MoveFolder({ folder, onClose }: { folder: LibraryFolder; onClose: () => void }) {
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
      const j = unwrap(await api.POST("/api/jobs", { body: { kind: "relocate_library", id: folder.id, root: root.trim(), apply, fix_case: fixCase } }));
      if (apply) {
        notifications.show({ message: `Moving ${folder.root}: follow the job on the right.` });
        onClose();
      } else {
        setCheckJob(j.id);
      }
    } catch (err) {
      fail("Move folder")(err);
    }
  };

  return (
    <Modal opened onClose={onClose} title={`Move ${folder.root || folder.name}`} size="lg">
      <Stack>
        <Text size="sm">
          After moving or re-mounting this folder (another disk, another machine), point it at its new place.
          Records, tags, edits and thumbnails stay; nothing is re-read.
        </Text>
        <Text size="xs" c="dimmed">Now: {folder.root || "(no folder)"}{folder.reachable ? "" : " — not found"}</Text>
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
