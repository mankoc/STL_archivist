// Import a library package (Settings → Backups, administrators): choose a
// package on the server or upload one, check its folders at their roots here
// (a rule moves every root below a path start), then replace this library
// with it. Checking changes nothing; importing backs this library up first.
import { Alert, Button, Checkbox, FileButton, Group, Modal, Progress, Select, Stack, Table, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, type Schemas, unwrap, uploadPackage } from "../api/client";
import { FolderPicker } from "./FolderPicker";
import type { JobRequest } from "./jobs";
import { type ImportPreview, importFolderText, rootsToSend, shownRoot, upToDate, withRule } from "./transfer";

const fail = (title: string) => (err: unknown) => notifications.show({ color: "red", title, message: (err as Error).message });

export function ImportDialog({ initial, packages, onClose, start }: {
  initial: string | null;
  packages: Schemas["BackupFile"][];
  onClose: () => void;
  start: (r: JobRequest) => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState<string | null>(initial);
  const [uploading, setUploading] = useState<number | null>(null);
  const [roots, setRoots] = useState<Record<string, string>>({});
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [picker, setPicker] = useState<string | null>(null);
  const [checkJob, setCheckJob] = useState<number | null>(null);
  const [typed, setTyped] = useState("");
  const [backup, setBackup] = useState(true);

  const job = useQuery({
    queryKey: ["import-check", checkJob],
    queryFn: async () => unwrap(await api.GET("/api/jobs/{id}", { params: { path: { id: checkJob! } } })),
    enabled: checkJob != null,
    refetchInterval: (q) => (q.state.data && !["queued", "running"].includes(q.state.data.job.state) ? false : 700),
  });
  const running = checkJob != null && (!job.data || ["queued", "running"].includes(job.data.job.state));
  const preview = job.data?.result?.type === "import" ? (job.data.result as ImportPreview) : null;
  const folders = preview?.folders ?? [];
  const checked = preview != null && upToDate(folders, roots);

  const check = async (pkg: string | null, r: Record<string, string>) => {
    if (!pkg) return;
    try {
      const j = unwrap(await api.POST("/api/jobs", { body: { kind: "import_library", name: pkg, roots: r } }));
      setCheckJob(j.id);
    } catch (err) {
      fail("Could not check the package")(err);
    }
  };
  const choose = (pkg: string | null) => {
    setName(pkg);
    setRoots({});
    setCheckJob(null);
    void check(pkg, {});
  };
  // A package chosen in the list opens checked.
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    if (initial) void check(initial, {});
  });

  const upload = async (file: File | null) => {
    if (!file) return;
    setUploading(0);
    try {
      const b = await uploadPackage(file, setUploading);
      await queryClient.invalidateQueries({ queryKey: ["backups"] });
      choose(b.name);
    } catch (err) {
      fail("Upload failed")(err);
    } finally {
      setUploading(null);
    }
  };
  const apply = async () => {
    if (!name) return;
    await start({ kind: "import_library", name, roots: rootsToSend(folders, roots), apply: true, confirm: "REPLACE", backup_first: backup });
    notifications.show({ message: `Importing: follow the job below.${backup ? " The library before it is backed up first." : ""}` });
    onClose();
  };

  const choices = packages.map((p) => p.name);
  if (name && !choices.includes(name)) choices.unshift(name);
  return (
    <Modal opened onClose={onClose} title="Import a library" size="xl">
      <Stack>
        <Text size="sm">
          Replaces this server's library (models, libraries, tags, thumbnails and library settings) with a package exported
          by another server or by this one. User accounts stay.
        </Text>
        <Group align="flex-end" gap="xs">
          <Select label="Package" data={choices} value={name} onChange={choose} placeholder="Choose a package on the server"
            style={{ flex: 1 }} allowDeselect={false} data-testid="import-package" />
          <FileButton onChange={(f) => void upload(f)} accept=".7z">
            {(props) => <Button variant="default" {...props} disabled={uploading !== null}>Upload a package…</Button>}
          </FileButton>
        </Group>
        {uploading !== null && <Progress value={uploading * 100} animated />}
        {job.data?.job.state === "failed" && <Alert color="red" p="xs">{job.data.job.message}</Alert>}
        {running && <Text size="sm" c="dimmed">Checking the package…</Text>}

        {preview && (
          <>
            <Text size="sm">
              {preview.models.toLocaleString()} record{preview.models === 1 ? "" : "s"} in {folders.length} folder
              {folders.length === 1 ? "" : "s"}. Give each folder its place on this server:
            </Text>
            <Group align="flex-end" gap="xs">
              <TextInput label="Replace the path start" placeholder={folders[0]?.root} value={from} onChange={(e) => setFrom(e.currentTarget.value)} style={{ flex: 1 }} />
              <TextInput label="with" placeholder="/library" value={to} onChange={(e) => setTo(e.currentTarget.value)} style={{ flex: 1 }} />
              <Button variant="default" disabled={!from.trim() || !to.trim()} onClick={() => setRoots(withRule(folders, roots, from, to))}>
                Apply to all
              </Button>
            </Group>
            <Table fz="sm" verticalSpacing={6} data-testid="import-folders">
              <Table.Tbody>
                {folders.map((f) => (
                  <Table.Tr key={f.uuid}>
                    <Table.Td w="35%">
                      <Text size="sm" fw={500}>{f.name}{f.library && f.library !== f.name ? ` (${f.library})` : ""}</Text>
                      <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>{f.root}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap">
                        <TextInput size="xs" style={{ flex: 1 }} value={shownRoot(f, roots)} placeholder={`keeps ${f.root}`}
                          onChange={(e) => setRoots({ ...roots, [f.uuid]: e.currentTarget.value })} />
                        <Button size="xs" variant="default" onClick={() => setPicker(f.uuid)}>Browse…</Button>
                      </Group>
                      {checked && (
                        <Text size="xs" mt={4} c={!f.root_exists || f.missing ? "orange" : "green"}>
                          {importFolderText(f)}
                          {f.root_exists && f.missing_examples.length > 0 ? ` (not found: ${f.missing_examples.slice(0, 3).join(", ")}${f.missing > 3 ? ", …" : ""})` : ""}
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {!checked && <Text size="xs" c="orange">The folders changed since the last check: check again before importing.</Text>}
            <Checkbox label="Back up this library first (…_pre_import.7z)" checked={backup} onChange={(e) => setBackup(e.currentTarget.checked)} data-testid="import-backup" />
            <Group justify="space-between" align="flex-end">
              <Button variant="light" loading={running} onClick={() => void check(name, rootsToSend(folders, roots))} data-testid="import-check">
                Check again
              </Button>
              <Group gap="xs" align="flex-end">
                <TextInput size="sm" label="Type REPLACE to import" value={typed} onChange={(e) => setTyped(e.currentTarget.value)} w={180} data-testid="import-confirm" />
                <Button color="red" disabled={!checked || running || typed !== "REPLACE"} onClick={() => void apply()} data-testid="import-apply">
                  Import and replace this library
                </Button>
              </Group>
            </Group>
          </>
        )}
      </Stack>
      <FolderPicker
        opened={picker !== null}
        onClose={() => setPicker(null)}
        onPick={(p) => {
          if (picker) setRoots({ ...roots, [picker]: p });
          setPicker(null);
        }}
      />
    </Modal>
  );
}
