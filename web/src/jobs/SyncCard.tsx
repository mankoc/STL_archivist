// Sync with other servers (Settings → Backups & transfer, administrators):
// the remote servers this one pulls from and pushes to, each signed in once
// (an administrator's name and password there; only the session is kept),
// with folder rules that map its folders to this server's. A pull or a push
// shows where every folder goes before it replaces the receiving library.
import { ActionIcon, Alert, Badge, Button, Card, Checkbox, Group, Modal, PasswordInput, Stack, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCloudDownload, IconCloudUpload, IconFolders, IconKey, IconPlus, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { FolderPicker } from "./FolderPicker";
import type { Job, JobRequest } from "./jobs";
import { cleanRules, type Direction, lastSyncText, previewSummary, type RemoteInfo, type RuleRow, syncWarning } from "./sync";

const fail = (title: string) => (err: unknown) => notifications.show({ color: "red", title, message: (err as Error).message });
const when = (unix: number) => new Date(unix * 1000).toLocaleString();

export function SyncCard({ jobs, start }: { jobs: Job[] | undefined; start: (r: JobRequest) => Promise<void> }) {
  const queryClient = useQueryClient();
  // Refetch when the newest sync job changes state (its outcome is noted on the remote).
  const last = (jobs ?? []).filter((j) => j.kind === "sync").reduce<Job | undefined>((a, j) => (!a || j.id > a.id ? j : a), undefined);
  const remotes = useQuery({
    queryKey: ["sync-remotes", last?.id, last?.state],
    queryFn: async () => unwrap(await api.GET("/api/sync/remotes")),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["sync-remotes"] });
  const [adding, setAdding] = useState(false);
  const [signIn, setSignIn] = useState<RemoteInfo | null>(null);
  const [rules, setRules] = useState<RemoteInfo | null>(null);
  const [syncing, setSyncing] = useState<{ remote: RemoteInfo; direction: Direction } | null>(null);
  const [removing, setRemoving] = useState<RemoteInfo | null>(null);
  const remove = async (r: RemoteInfo) => {
    try {
      unwrap(await api.DELETE("/api/sync/remotes/{id}", { params: { path: { id: r.id } } }));
      setRemoving(null);
      await refresh();
    } catch (err) {
      fail("Could not remove")(err);
    }
  };
  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Title order={5}>Sync with other servers</Title>
        <Button size="xs" variant="default" leftSection={<IconPlus size={14} />} onClick={() => setAdding(true)} data-testid="sync-add">
          Add a server…
        </Button>
      </Group>
      <Text size="xs" c="dimmed" mb="xs">
        <b>Pull</b> makes this library a copy of another server's; <b>Push</b> makes the other server's a copy of this one. Folder
        rules map each folder to its place on the other side (<i>D:\Minis</i> here is <i>/library</i> there). Only the
        thumbnails the receiving side lacks are sent. Edit on one side at a time: a sync replaces the receiving library.
      </Text>
      {remotes.data?.length ? (
        <Table fz="sm" verticalSpacing={4}>
          <Table.Tbody>
            {remotes.data.map((r) => (
              <Table.Tr key={r.id} data-testid="sync-remote">
                <Table.Td>
                  <Text size="sm" fw={500}>{r.name}</Text>
                  <Text size="xs" c="dimmed">{r.url} · as {r.user} · {r.rules.length} folder rule{r.rules.length === 1 ? "" : "s"}</Text>
                  {lastSyncText(r, when) && (
                    <Text size="xs" c={r.last_result?.startsWith("failed") ? "red" : "dimmed"}>{lastSyncText(r, when)}</Text>
                  )}
                </Table.Td>
                <Table.Td w={290}>
                  <Group gap={4} wrap="nowrap" justify="flex-end">
                    <Button size="xs" variant="light" leftSection={<IconCloudDownload size={14} />} onClick={() => setSyncing({ remote: r, direction: "pull" })}>
                      Pull…
                    </Button>
                    <Button size="xs" variant="light" leftSection={<IconCloudUpload size={14} />} onClick={() => setSyncing({ remote: r, direction: "push" })}>
                      Push…
                    </Button>
                    <Tooltip label="Folder rules">
                      <ActionIcon variant="subtle" onClick={() => setRules(r)}><IconFolders size={16} /></ActionIcon>
                    </Tooltip>
                    <Tooltip label="Sign in again">
                      <ActionIcon variant="subtle" onClick={() => setSignIn(r)}><IconKey size={16} /></ActionIcon>
                    </Tooltip>
                    <Tooltip label="Remove">
                      <ActionIcon variant="subtle" color="red" onClick={() => setRemoving(r)}><IconTrash size={16} /></ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      ) : (
        <Text size="sm" c="dimmed">No other servers yet.</Text>
      )}
      {adding && <AddRemote onClose={() => { setAdding(false); void refresh(); }} />}
      {signIn && <SignIn remote={signIn} onClose={() => { setSignIn(null); void refresh(); }} />}
      {rules && <Rules remote={rules} onClose={() => { setRules(null); void refresh(); }} />}
      {syncing && <SyncDialog {...syncing} start={start} onClose={() => setSyncing(null)} />}
      <Modal opened={removing !== null} onClose={() => setRemoving(null)} title="Remove a server">
        <Stack>
          <Text size="sm">Forget {removing?.name} and its folder rules? Nothing changes on either server.</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRemoving(null)}>Cancel</Button>
            <Button color="red" onClick={() => removing && void remove(removing)}>Remove</Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}

function AddRemote({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ name: "", url: "", user: "", password: "" });
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.currentTarget.value });
  const add = async () => {
    setBusy(true);
    try {
      unwrap(await api.POST("/api/sync/remotes", { body: form }));
      notifications.show({ message: `Signed in to ${form.name}. Set its folder rules before the first sync.` });
      onClose();
    } catch (err) {
      fail("Could not add the server")(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal opened onClose={onClose} title="Add a server">
      <Stack>
        <TextInput label="Name" placeholder="NAS" value={form.name} onChange={set("name")} data-autofocus />
        <TextInput label="Address" placeholder="http://nas:8770" value={form.url} onChange={set("url")} />
        <TextInput label="Administrator there" value={form.user} onChange={set("user")} />
        <PasswordInput label="Password" description="Used once to sign in; only the session is kept." value={form.password} onChange={set("password")} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button loading={busy} disabled={!form.name.trim() || !form.url.trim() || !form.user.trim() || !form.password} onClick={() => void add()}>
            Sign in and add
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function SignIn({ remote, onClose }: { remote: RemoteInfo; onClose: () => void }) {
  const [user, setUser] = useState(remote.user);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      unwrap(await api.POST("/api/sync/remotes/{id}/login", { params: { path: { id: remote.id } }, body: { user, password } }));
      notifications.show({ message: `Signed in to ${remote.name} again.` });
      onClose();
    } catch (err) {
      fail("Could not sign in")(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal opened onClose={onClose} title={`Sign in to ${remote.name} again`}>
      <Stack>
        <TextInput label="Administrator there" value={user} onChange={(e) => setUser(e.currentTarget.value)} />
        <PasswordInput label="Password" value={password} onChange={(e) => setPassword(e.currentTarget.value)} data-autofocus />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button loading={busy} disabled={!user.trim() || !password} onClick={() => void go()}>Sign in</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function Rules({ remote, onClose }: { remote: RemoteInfo; onClose: () => void }) {
  const [rows, setRows] = useState<RuleRow[]>(remote.rules.length ? remote.rules : [{ local: "", remote: "" }]);
  const [backupFirst, setBackupFirst] = useState(remote.backup_first);
  const [picker, setPicker] = useState<number | null>(null);
  const { rules, error } = cleanRules(rows);
  const change = (i: number, patch: Partial<RuleRow>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const save = async () => {
    try {
      unwrap(await api.PATCH("/api/sync/remotes/{id}", { params: { path: { id: remote.id } }, body: { rules, backup_first: backupFirst } }));
      onClose();
    } catch (err) {
      fail("Could not save")(err);
    }
  };
  return (
    <Modal opened onClose={onClose} title={`Folders on ${remote.name}`} size="xl">
      <Stack>
        <Text size="sm">
          Each rule names a folder on this server and the same folder on {remote.name}; the folders below them follow. A sync
          uses them both ways.
        </Text>
        {rows.map((r, i) => (
          <Group key={i} gap="xs" align="flex-end" wrap="nowrap">
            <TextInput label={i === 0 ? "This server's folder" : undefined} value={r.local} placeholder="D:\Minis" style={{ flex: 1 }}
              onChange={(e) => change(i, { local: e.currentTarget.value })} />
            <Button variant="default" onClick={() => setPicker(i)}>Browse…</Button>
            <TextInput label={i === 0 ? `The folder on ${remote.name}` : undefined} value={r.remote} placeholder="/library" style={{ flex: 1 }}
              onChange={(e) => change(i, { remote: e.currentTarget.value })} />
            <ActionIcon variant="subtle" color="red" mb={4} onClick={() => setRows(rows.filter((_, j) => j !== i))} aria-label="Remove the rule">
              <IconTrash size={16} />
            </ActionIcon>
          </Group>
        ))}
        <Group justify="space-between">
          <Button size="xs" variant="subtle" leftSection={<IconPlus size={14} />} onClick={() => setRows([...rows, { local: "", remote: "" }])}>
            Add a rule
          </Button>
          <Checkbox label="The receiving side backs its library up before a sync" checked={backupFirst} onChange={(e) => setBackupFirst(e.currentTarget.checked)} />
        </Group>
        {error && <Text size="sm" c="red">{error}</Text>}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button disabled={error !== null} onClick={() => void save()}>Save</Button>
        </Group>
      </Stack>
      <FolderPicker opened={picker !== null} onClose={() => setPicker(null)} onPick={(p) => { if (picker !== null) change(picker, { local: p }); setPicker(null); }} />
    </Modal>
  );
}

function SyncDialog({ remote, direction, start, onClose }: { remote: RemoteInfo; direction: Direction; start: (r: JobRequest) => Promise<void>; onClose: () => void }) {
  const [typed, setTyped] = useState("");
  const preview = useQuery({
    queryKey: ["sync-preview", remote.id, direction],
    queryFn: async () => unwrap(await api.GET("/api/sync/remotes/{id}/preview", { params: { path: { id: remote.id }, query: { direction } } })),
    retry: false,
  });
  const p = preview.data;
  const [from, to] = direction === "pull" ? [remote.name, "this server"] : ["this server", remote.name];
  const go = async () => {
    await start({ kind: "sync", remote: remote.id, direction, confirm: "REPLACE" });
    notifications.show({ message: `${direction === "pull" ? "Pulling from" : "Pushing to"} ${remote.name}: follow the job below.` });
    onClose();
  };
  return (
    <Modal opened onClose={onClose} title={`${direction === "pull" ? "Pull from" : "Push to"} ${remote.name}`} size="xl">
      <Stack>
        {preview.isPending && <Text size="sm" c="dimmed">Asking {remote.name}…</Text>}
        {preview.isError && <Alert color="red" p="xs">{preview.error.message}</Alert>}
        {p && (
          <>
            <Text size="xs" c="dimmed">This server {p.local_version} · {remote.name} {p.remote_version}</Text>
            {p.problem && <Alert color="red" p="xs">{p.problem}</Alert>}
            <Text size="sm">Folders of {from}, and where they go on {to}: {previewSummary(p)}.</Text>
            <Table fz="sm" verticalSpacing={4} data-testid="sync-preview">
              <Table.Tbody>
                {p.folders.map((f) => (
                  <Table.Tr key={f.uuid}>
                    <Table.Td w="30%">
                      <Text size="sm" fw={500}>{f.name}{f.library && f.library !== f.name ? ` (${f.library})` : ""}</Text>
                      <Text size="xs" c="dimmed">{f.models.toLocaleString()} record{f.models === 1 ? "" : "s"}</Text>
                    </Table.Td>
                    <Table.Td style={{ wordBreak: "break-all" }}>
                      <Text size="xs" c="dimmed">{f.root}</Text>
                      <Text size="sm">→ {f.target ?? "not mapped: keeps its root"}</Text>
                    </Table.Td>
                    <Table.Td w={110}>
                      <Badge color={f.target && f.exists ? "green" : "orange"} variant="light">{f.target && f.exists ? "found" : "not there"}</Badge>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            <Alert color="orange" p="xs">
              {syncWarning(direction, remote.name)}
              {remote.backup_first ? ` ${to === "this server" ? "This server" : remote.name} backs its library up first.` : ""}
            </Alert>
            <Group justify="flex-end" align="flex-end">
              <TextInput size="sm" label="Type REPLACE to sync" value={typed} onChange={(e) => setTyped(e.currentTarget.value)} w={180} data-testid="sync-confirm" />
              <Button color="red" disabled={!!p.problem || typed !== "REPLACE"} onClick={() => void go()} data-testid="sync-start">
                {direction === "pull" ? `Replace this library with ${remote.name}'s` : `Replace ${remote.name}'s library with this one`}
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Modal>
  );
}
