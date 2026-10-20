// Database Maintenance (the desktop dialog): checks that produce a record list
// (Verify, duplicate entries), the actions on that list (Find Moved in DB,
// Search in Folder, Merge Moved, Remove), warning re-checks, the store jobs,
// sidecars and backups.
import { Alert, Badge, Button, Card, Checkbox, Group, Modal, NumberInput, Progress, Stack, Table, Text, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";
import { BackupCard, DangerCard, SidecarCard } from "./BackupCards";
import { ComponentsCard } from "./ComponentsCard";
import { FolderPicker } from "./FolderPicker";
import { addJob, describe, isActive, type Job, type JobRequest, percent } from "./jobs";
import { JobLog } from "./JobsPage";
import classes from "./JobsPage.module.css";
import { isMissing, isMoved, latestRecordsJob, type RecordRow, STATUS_COLOR, targets } from "./maintenance";

export function MaintenancePage() {
  const user = useUser();
  const editor = hasRole(user, "editor");
  const admin = hasRole(user, "admin");
  const queryClient = useQueryClient();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")) });
  const [keep, setKeep] = useState<number>(5);
  const [last, setLast] = useState<number | null>(null);

  const start = async (request: JobRequest) => {
    try {
      const job = unwrap(await api.POST("/api/jobs", { body: request }));
      queryClient.setQueryData<Job[]>(["jobs"], (old) => addJob(old, job));
      setLast(job.id);
    } catch (err) {
      notifications.show({ color: "red", title: "Could not start", message: (err as Error).message });
    }
  };
  const current = (jobs.data ?? []).find((j) => j.id === last);

  return (
    <div className={classes.layout}>
      <Stack gap="md" className={classes.column}>
        {!editor && (
          <Alert>Maintenance runs as server jobs, which need the editor role.</Alert>
        )}
        <Card withBorder>
          <Title order={5} mb="xs">
            Records
          </Title>
          <Group gap="xs">
            <Tooltip label="Check every archive path for existence on disk, unchanged in size">
              <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "verify_archives" })} data-testid="verify">
                Verify archives
              </Button>
            </Tooltip>
            <Tooltip label="Records sharing the same content hash, or the same file name and size">
              <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "find_duplicate_entries" })}>
                Find duplicate entries
              </Button>
            </Tooltip>
          </Group>
        </Card>
        <Card withBorder>
          <Title order={5} mb="xs">
            Warnings
          </Title>
          <Text size="xs" c="dimmed" mb="xs">
            Re-read every archive's file list and fix one warning, without a rescan.
          </Text>
          <Group gap="xs">
            <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "recheck_warnings", warning: "no_images" })}>
              Check images warning
            </Button>
            <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "recheck_warnings", warning: "presupported" })}>
              Check presupport warning
            </Button>
          </Group>
        </Card>
        <Card withBorder>
          <Title order={5} mb="xs">
            Images and storage
          </Title>
          <Stack gap="xs">
            <Group gap="xs">
              <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "recompute_dhashes", all: false })}>
                Compute missing image hashes
              </Button>
              <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "recompute_dhashes", all: true })}>
                Recompute all image hashes
              </Button>
            </Group>
            <Group gap="xs">
              <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "compute_embeddings", all: false })}>
                Compute missing embeddings
              </Button>
              <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "compute_embeddings", all: true })}>
                Recompute all embeddings
              </Button>
            </Group>
            <Group gap="xs" align="flex-end">
              <NumberInput size="xs" label="Images per model" min={0} w={130} value={keep} onChange={(v) => setKeep(Number(v) || 0)} />
              <Button size="xs" variant="default" disabled={!editor} onClick={() => start({ kind: "prune_images", keep })}>
                Prune images
              </Button>
              {admin && (
                <Button size="xs" variant="default" onClick={() => start({ kind: "compact_database" })}>
                  Compact database
                </Button>
              )}
            </Group>
          </Stack>
        </Card>
        <SidecarCard editor={editor} start={start} />
        {admin && <ComponentsCard jobs={jobs.data} start={start} />}
        {admin && <BackupCard jobs={jobs.data} start={start} />}
        {admin && <DangerCard start={start} />}
        {current && <JobLog id={current.id} job={current} />}
      </Stack>
      <Stack gap="md" className={classes.column}>
        <RecordList job={latestRecordsJob(jobs.data)} editor={editor} start={start} />
      </Stack>
    </div>
  );
}

function RecordList({ job, editor, start }: { job: Job | undefined; editor: boolean; start: (r: JobRequest) => Promise<void> }) {
  const queryClient = useQueryClient();
  const finished = job != null && !isActive(job);
  const detail = useQuery({
    queryKey: ["job-result", job?.id],
    queryFn: async () => unwrap(await api.GET("/api/jobs/{id}", { params: { path: { id: job!.id } } })),
    enabled: finished,
  });
  const result = detail.data?.result;
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [gone, setGone] = useState<Set<number>>(new Set());
  const [picker, setPicker] = useState(false);
  const [confirm, setConfirm] = useState<RecordRow[] | null>(null);
  const rows = useMemo(
    () => (result?.type === "records" ? result.rows.filter((r) => !gone.has(r.id)) : []),
    [result, gone],
  );
  const pct = job ? percent(job) : null;
  const missing = targets(rows, selected, isMissing);
  const moved = targets(rows, selected, isMoved);
  const toggle = (id: number) => setSelected((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  });
  const dropped = (ids: number[]) => {
    setGone((g) => new Set([...g, ...ids]));
    setSelected(new Set());
    void queryClient.invalidateQueries({ queryKey: ["ids"] });
  };

  const merge = async () => {
    try {
      const r = unwrap(await api.POST("/api/maintenance/merge-moved", { body: { items: moved.map((m) => ({ id: m.id, path: m.match_path })) } }));
      notifications.show({ message: `${r.merged} record(s) merged into their new location${r.errors ? `; ${r.errors} no longer exist` : ""}` });
      dropped(moved.map((m) => m.id));
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
  };
  const remove = async (list: RecordRow[]) => {
    setConfirm(null);
    try {
      const r = unwrap(await api.POST("/api/maintenance/remove", { body: { ids: list.map((m) => m.id) } }));
      const pruned = Object.entries(r.pruned).map(([t, n]) => `${n} ${t}`).join(", ");
      notifications.show({ message: `${r.removed} record(s) removed${pruned ? `; unused lookups pruned: ${pruned}` : ""}` });
      dropped(list.map((m) => m.id));
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
  };

  return (
    <Card withBorder style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      <Group justify="space-between" mb="xs">
        <Title order={5}>{job ? describe(job) : "Record list"}</Title>
        {job?.message && finished && <Text size="xs" c="dimmed" data-testid="records-message">{job.message}</Text>}
      </Group>
      {!job && <Text size="sm" c="dimmed">Run Verify archives or Find duplicate entries to list records here.</Text>}
      {job && !finished && (
        <Stack gap={4}>
          <Text size="xs" c="dimmed">{job.phase ?? "Working…"}</Text>
          <Progress value={pct ?? 100} animated striped={pct == null} />
        </Stack>
      )}
      {finished && result?.type === "records" && (
        <>
          {editor && (
            <Group gap="xs" mb="xs">
              <Button size="xs" variant="light" disabled={!missing.length} onClick={() => start({ kind: "find_moved", ids: missing.map((r) => r.id) })}>
                Find moved in DB ({missing.length})
              </Button>
              <Button size="xs" variant="light" disabled={!missing.length} onClick={() => setPicker(true)}>
                Search in folder… ({missing.length})
              </Button>
              <Button size="xs" variant="light" disabled={!moved.length} onClick={merge} data-testid="merge-moved">
                Merge moved ({moved.length})
              </Button>
              <Button size="xs" variant="light" color="red" disabled={!selected.size} onClick={() => setConfirm(rows.filter((r) => selected.has(r.id)))}>
                Remove selected ({selected.size})
              </Button>
              <Button size="xs" variant="light" color="red" disabled={!rows.some(isMissing)} onClick={() => setConfirm(rows.filter(isMissing))}>
                Remove all missing
              </Button>
            </Group>
          )}
          {rows.length === 0 ? (
            <Text size="sm" c="dimmed">Nothing to show: no problems found.</Text>
          ) : (
            <div style={{ overflow: "auto", minHeight: 0 }}>
              <Table fz="sm" striped highlightOnHover stickyHeader>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th w={32}>
                      <Checkbox size="xs" checked={selected.size > 0 && selected.size === rows.length}
                        indeterminate={selected.size > 0 && selected.size < rows.length}
                        onChange={() => setSelected(selected.size ? new Set() : new Set(rows.map((r) => r.id)))} />
                    </Table.Th>
                    <Table.Th w={130}>Status</Table.Th>
                    <Table.Th>Record</Table.Th>
                    <Table.Th>Match</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {rows.map((r) => (
                    <Table.Tr key={`${r.id}-${r.status}`} data-testid="record-row">
                      <Table.Td><Checkbox size="xs" checked={selected.has(r.id)} onChange={() => toggle(r.id)} /></Table.Td>
                      <Table.Td><Badge size="sm" variant="light" color={STATUS_COLOR[r.status] ?? "gray"}>{r.status}</Badge></Table.Td>
                      <Table.Td>
                        <Text size="sm">{r.title || `#${r.id}`}</Text>
                        <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>{r.path}</Text>
                      </Table.Td>
                      <Table.Td><Text size="xs" style={{ wordBreak: "break-all" }}>{r.match_path}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
          )}
        </>
      )}
      <FolderPicker
        opened={picker}
        onClose={() => setPicker(false)}
        onPick={(folder) => {
          setPicker(false);
          void start({ kind: "search_folder", folder, ids: missing.map((r) => r.id) });
        }}
      />
      <Modal opened={confirm != null} onClose={() => setConfirm(null)} title="Remove records">
        <Stack>
          <Text size="sm">
            Remove {confirm?.length} record(s) from the library? Their metadata and images are deleted; the archive files are not touched.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirm(null)}>Cancel</Button>
            <Button color="red" onClick={() => confirm && remove(confirm)} data-testid="confirm-remove">Remove</Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}
