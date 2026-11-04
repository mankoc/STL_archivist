// Settings: this user's preferences (and, in the desktop app, this computer's
// MeshPeek), the library's settings (administrators change them), maintenance,
// sidecars (their settings and jobs), backups, compute workers, the
// downloadable components, and user administration. A tab is addressed as
// `#/settings/<tab>`.
import {
  ActionIcon, Badge, Button, Card, Checkbox, ColorSwatch, Group, Modal, NumberInput, PasswordInput, SegmentedControl, Select, SimpleGrid,
  Stack, Switch, Table, Tabs, Text, Textarea, TextInput, Title, Tooltip, useMantineColorScheme,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck, IconKey, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";

import { type Accent, ACCENT_SETTING, ACCENTS, DEFAULT_ACCENT, isAccent, useSetAccent } from "../accent";
import { api, type Schemas, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";
import { HIGHLIGHT_WARNINGS } from "../browser/warnings";
import { clearMeshPeek, clearPacker, inDesktopShell, meshpeekState, packerState, pickMeshPeek, pickPacker, type ProgramState } from "../desktop";
import { ComponentsPanel } from "../jobs/ComponentsPanel";
import { MaintenanceTab } from "../jobs/MaintenanceTab";
import { BackupsTab, SidecarsTab, WorkersTab } from "../jobs/SettingsTabs";
import { CHECK_UPDATES } from "../shell/updates";
import { type LibrarySettings, SETTINGS_QUERY } from "./librarySettings";
import { settingsTab } from "./tabs";

type Role = Schemas["Role"];
const ROLES: { value: Role; label: string }[] = [
  { value: "viewer", label: "Viewer (read only)" },
  { value: "editor", label: "Editor" },
  { value: "admin", label: "Administrator" },
];

function fail(err: unknown) {
  notifications.show({ color: "red", message: (err as Error).message });
}

export function SettingsPage() {
  const user = useUser();
  const admin = hasRole(user, "admin");
  const editor = hasRole(user, "editor");
  const components = admin || inDesktopShell();
  const tabs = [
    "account", "library", "maintenance", "sidecars", ...(admin ? ["backups"] : []), "workers",
    ...(components ? ["components"] : []), ...(admin ? ["users"] : []),
  ];
  return (
    <Tabs
      defaultValue={settingsTab(location.hash, tabs)}
      onChange={(t) => history.replaceState(null, "", `#/settings/${t}`)}
      p="md"
      keepMounted={false}
    >
      <Tabs.List mb="md">
        <Tabs.Tab value="account">Account</Tabs.Tab>
        <Tabs.Tab value="library">Library</Tabs.Tab>
        <Tabs.Tab value="maintenance">Maintenance</Tabs.Tab>
        <Tabs.Tab value="sidecars">Sidecars</Tabs.Tab>
        {admin && <Tabs.Tab value="backups">Backups &amp; transfer</Tabs.Tab>}
        <Tabs.Tab value="workers">Workers</Tabs.Tab>
        {components && <Tabs.Tab value="components">Components</Tabs.Tab>}
        {admin && <Tabs.Tab value="users">Users</Tabs.Tab>}
      </Tabs.List>
      <Tabs.Panel value="account">
        <Stack maw={520}>
          <AccountSettings />
          {inDesktopShell() && <MeshPeekSettings />}
          {inDesktopShell() && <PackerSettings />}
        </Stack>
      </Tabs.Panel>
      <Tabs.Panel value="library">
        <LibrarySettingsForm editable={admin} />
      </Tabs.Panel>
      <Tabs.Panel value="maintenance">
        <MaintenanceTab />
      </Tabs.Panel>
      <Tabs.Panel value="sidecars">
        <SidecarsTab editor={editor} admin={admin} />
      </Tabs.Panel>
      {admin && (
        <Tabs.Panel value="backups">
          <BackupsTab />
        </Tabs.Panel>
      )}
      <Tabs.Panel value="workers">
        <WorkersTab editor={editor} />
      </Tabs.Panel>
      {components && (
        <Tabs.Panel value="components">
          <ComponentsPanel admin={admin} />
        </Tabs.Panel>
      )}
      {admin && (
        <Tabs.Panel value="users">
          <Users />
        </Tabs.Panel>
      )}
    </Tabs>
  );
}

function AccountSettings() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const queryClient = useQueryClient();
  const setAccent = useSetAccent();
  const prefs = useQuery({ queryKey: ["me-settings"], queryFn: async () => unwrap(await api.GET("/api/me/settings")) as Record<string, unknown> });
  const stored = prefs.data?.[ACCENT_SETTING];
  const accent: Accent = isAccent(stored) ? stored : DEFAULT_ACCENT;
  const highlight = prefs.data?.[HIGHLIGHT_WARNINGS] === true;
  const chooseHighlight = async (on: boolean) => {
    queryClient.setQueryData(["me-settings"], (d: Record<string, unknown> | undefined) => ({ ...d, [HIGHLIGHT_WARNINGS]: on }));
    try {
      unwrap(await api.PUT("/api/me/settings/{key}", { params: { path: { key: HIGHLIGHT_WARNINGS } }, body: on as never }));
    } catch (err) {
      fail(err);
    }
  };
  const checkUpdates = prefs.data?.[CHECK_UPDATES] === true;
  const chooseCheckUpdates = async (on: boolean) => {
    queryClient.setQueryData(["me-settings"], (d: Record<string, unknown> | undefined) => ({ ...d, [CHECK_UPDATES]: on }));
    try {
      unwrap(await api.PUT("/api/me/settings/{key}", { params: { path: { key: CHECK_UPDATES } }, body: on as never }));
      await queryClient.invalidateQueries({ queryKey: ["system-info"] });
    } catch (err) {
      fail(err);
    }
  };
  const chooseAccent = async (a: Accent) => {
    setAccent(a);
    queryClient.setQueryData(["me-settings"], (d: Record<string, unknown> | undefined) => ({ ...d, [ACCENT_SETTING]: a }));
    try {
      unwrap(await api.PUT("/api/me/settings/{key}", { params: { path: { key: ACCENT_SETTING } }, body: a as never }));
    } catch (err) {
      fail(err);
    }
  };
  return (
    <Card withBorder>
      <Title order={5} mb="sm">
        Appearance
      </Title>
      <SegmentedControl
        value={colorScheme}
        onChange={async (v) => {
          setColorScheme(v as "light" | "dark" | "auto");
          await api.PUT("/api/me/settings/{key}", { params: { path: { key: "color_scheme" } }, body: v as never });
        }}
        data={[
          { value: "auto", label: "System" },
          { value: "light", label: "Light" },
          { value: "dark", label: "Dark" },
        ]}
        data-testid="color-scheme"
      />
      <Text size="sm" fw={500} mt="md" mb={6}>
        Accent colour
      </Text>
      <Group gap={8} data-testid="accent-colours">
        {ACCENTS.map((a) => (
          <Tooltip key={a} label={a[0].toUpperCase() + a.slice(1)}>
            <ColorSwatch
              component="button"
              type="button"
              color={`var(--mantine-color-${a}-filled)`}
              size={26}
              aria-label={`Accent colour ${a}`}
              aria-pressed={a === accent}
              style={{ cursor: "pointer", outline: a === accent ? "2px solid var(--mantine-color-text)" : undefined, outlineOffset: 2 }}
              onClick={() => chooseAccent(a)}
            >
              {a === accent && <IconCheck size={14} color="white" />}
            </ColorSwatch>
          </Tooltip>
        ))}
      </Group>
      <Text size="xs" c="dimmed" mt="xs">
        Buttons, selections and highlights. Saved for your account; change your password from the menu at the top right.
      </Text>
      <Switch
        mt="md"
        label="Highlight models with warnings"
        description="In the library table: unreadable archives in red, other warnings in yellow."
        checked={highlight}
        onChange={(e) => chooseHighlight(e.currentTarget.checked)}
        data-testid="highlight-warnings"
      />
      <Switch
        mt="md"
        label="Check for updates"
        description="The server asks GitHub for a new release when you open StlArchivist, and at most once a day."
        checked={checkUpdates}
        onChange={(e) => chooseCheckUpdates(e.currentTarget.checked)}
        data-testid="check-updates"
      />
    </Card>
  );
}

/** The MeshPeek program "Open in MeshPeek" runs on this computer (the desktop shell keeps it). */
function MeshPeekSettings() {
  return (
    <ProgramSettings
      title="MeshPeek on this computer" queryKey="meshpeek" state={meshpeekState} pick={pickMeshPeek} clear={clearMeshPeek}
      notFound="Not found (no meshpeek on the PATH or in Program Files)"
      found="Found automatically; choose the program if MeshPeek is somewhere else." testId="meshpeek-settings"
    />
  );
}

/** The archive program "Open in packer" runs on this computer (the desktop shell keeps it). */
function PackerSettings() {
  return (
    <ProgramSettings
      title="Archive program on this computer" queryKey="packer" state={packerState} pick={pickPacker} clear={clearPacker}
      notFound="Not found (no PeaZip or 7-Zip on the PATH or in Program Files)"
      found="Found automatically; choose another program (PeaZip, 7-Zip, WinRAR, …) to open archives with." testId="packer-settings"
    />
  );
}

function ProgramSettings({ title, queryKey, state, pick, clear, notFound, found, testId }: {
  title: string;
  queryKey: string;
  state: () => Promise<ProgramState | null>;
  pick: () => Promise<unknown>;
  clear: () => Promise<unknown>;
  notFound: string;
  found: string;
  testId: string;
}) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: [queryKey], queryFn: state });
  const change = async (call: () => Promise<unknown>) => {
    try {
      await call();
      await queryClient.invalidateQueries({ queryKey: [queryKey] });
    } catch (err) {
      fail(err);
    }
  };
  const s = query.data;
  return (
    <Card withBorder data-testid={testId}>
      <Title order={5} mb="sm">
        {title}
      </Title>
      <Text size="sm" style={{ wordBreak: "break-all" }}>
        {s?.program ?? (s?.chosen ? `Not found at ${s.chosen}` : notFound)}
      </Text>
      <Text size="xs" c="dimmed" mb="sm">
        {s?.chosen ? "Chosen here." : found}
      </Text>
      <Group gap="xs">
        <Button size="xs" variant="light" onClick={() => change(pick)}>
          Choose program…
        </Button>
        {s?.chosen && (
          <Button size="xs" variant="subtle" onClick={() => change(clear)}>
            Find automatically
          </Button>
        )}
      </Group>
    </Card>
  );
}

function LibrarySettingsForm({ editable }: { editable: boolean }) {
  const queryClient = useQueryClient();
  const settings = useQuery(SETTINGS_QUERY);
  const [form, setForm] = useState<LibrarySettings | null>(null);
  const [patterns, setPatterns] = useState("");
  useEffect(() => {
    if (settings.data) {
      setForm(settings.data);
      setPatterns(settings.data.presupport_patterns.join("\n"));
    }
  }, [settings.data]);
  if (!form) return null;
  const set = <K extends keyof LibrarySettings>(k: K, v: LibrarySettings[K]) => setForm({ ...form, [k]: v });
  const check = (k: keyof LibrarySettings, label: string) => (
    <Checkbox label={label} checked={Boolean(form[k])} disabled={!editable} onChange={(e) => set(k, e.currentTarget.checked as never)} />
  );
  const save = async () => {
    try {
      const body = { ...form, presupport_patterns: patterns.split("\n").map((p) => p.trim()).filter(Boolean) };
      const saved = unwrap(await api.PUT("/api/settings/library", { body }));
      queryClient.setQueryData(["settings"], saved);
      notifications.show({ message: "Library settings saved." });
    } catch (err) {
      fail(err);
    }
  };
  return (
    <Stack maw={760}>
      {!editable && (
        <Text size="sm" c="dimmed">
          Only administrators can change these settings.
        </Text>
      )}
      <Card withBorder>
        <Title order={5} mb="sm">
          Crawler
        </Title>
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <NumberInput label="Thumbnails per archive" min={0} max={1000} value={form.max_thumbnails} disabled={!editable} onChange={(v) => set("max_thumbnails", Number(v) || 0)} />
          <NumberInput label="Thumbnail size (px)" min={1} max={10000} value={form.max_thumb_size} disabled={!editable} onChange={(v) => set("max_thumb_size", Number(v) || 1)} />
          <NumberInput
            label="Model viewer captures (px)"
            min={1}
            max={10000}
            value={form.view_thumb_size}
            disabled={!editable}
            onChange={(v) => set("view_thumb_size", Number(v) || 1)}
          />
          <NumberInput label="Parallel workers" min={1} max={64} value={form.max_workers} disabled={!editable} onChange={(v) => set("max_workers", Number(v) || 1)} />
          <NumberInput
            label="Check monitored folders every (minutes)"
            min={1}
            max={1440}
            value={form.monitor_interval_minutes}
            disabled={!editable}
            onChange={(v) => set("monitor_interval_minutes", Number(v) || 1)}
          />
        </SimpleGrid>
        <Stack gap="xs" mt="sm">
          {check("recurse_subdirs", "Crawl subfolders")}
          {check("detect_changes_by_size", "Detect changed archives by size only (ignore modification times)")}
          {check("ai_embeddings_enabled", "AI image embeddings")}
          {check("ai_embeddings_during_crawl", "Compute embeddings during crawls")}
        </Stack>
      </Card>
      <Card withBorder>
        <Title order={5} mb="sm">
          Warnings
        </Title>
        <Stack gap="xs">
          {check("warn_presupported_stl", "Models with presupported STL files")}
          {check("warn_no_images", "Archives without images")}
          {check("warn_nested_archive", "Nested archives")}
          {check("warn_no_printable", "Archives without printable files")}
          <Group align="flex-end">
            {check("warn_low_compression", "Low compression rate, above")}
            <NumberInput
              size="xs"
              w={100}
              min={0}
              max={1}
              step={0.01}
              decimalScale={2}
              value={form.low_compression_threshold}
              disabled={!editable || !form.warn_low_compression}
              onChange={(v) => set("low_compression_threshold", Number(v))}
            />
          </Group>
          <Textarea
            label="Presupported file name patterns"
            description="One regular expression per line, matched case-insensitively against STL file names"
            autosize
            minRows={3}
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
            value={patterns}
            disabled={!editable}
            onChange={(e) => setPatterns(e.currentTarget.value)}
          />
        </Stack>
      </Card>
      <Card withBorder>
        <Title order={5} mb="sm">
          Archive files
        </Title>
        <Stack gap="xs">
          {check("file_sorting_enabled", "Allow Sort files (moves archives)")}
          {check("allow_file_delete", "Allow Delete files")}
          {check("use_trash_folder", "Move deleted archives to the trash folder instead of deleting them")}
          <TextInput
            label="Trash folder"
            description="A folder on the server; without one, deleted archives are gone for good"
            value={form.trash_folder_path}
            disabled={!editable}
            onChange={(e) => set("trash_folder_path", e.currentTarget.value)}
          />
        </Stack>
      </Card>
      <Card withBorder>
        <Title order={5} mb="sm">
          AI tagger
        </Title>
        <NumberInput
          label="Temperature"
          description="Lower keeps suggestions literal and grounded in the images; higher is more creative"
          min={0}
          max={2}
          step={0.05}
          decimalScale={2}
          w={220}
          value={form.ai_temperature}
          disabled={!editable}
          onChange={(v) => set("ai_temperature", Number(v) || 0)}
        />
      </Card>
      {editable && (
        <Group>
          <Button onClick={save} data-testid="save-settings">
            Save
          </Button>
        </Group>
      )}
    </Stack>
  );
}

function Users() {
  const queryClient = useQueryClient();
  const me = useUser();
  const users = useQuery({ queryKey: ["users"], queryFn: async () => unwrap(await api.GET("/api/users")) });
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [resetFor, setResetFor] = useState<Schemas["User"] | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["users"] });
  const update = async (id: number, body: Schemas["UserChange"]) => {
    try {
      unwrap(await api.PATCH("/api/users/{id}", { params: { path: { id } }, body }));
    } catch (err) {
      fail(err);
    }
    await refresh();
  };
  const create = async (e: FormEvent) => {
    e.preventDefault();
    try {
      unwrap(await api.POST("/api/users", { body: { name, password, role } }));
      setName("");
      setPassword("");
      await refresh();
    } catch (err) {
      fail(err);
    }
  };
  return (
    <Stack maw={860}>
      <Card withBorder>
        <Table fz="sm" verticalSpacing={4}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th w={200}>Role</Table.Th>
              <Table.Th w={100}>Enabled</Table.Th>
              <Table.Th w={80} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(users.data ?? []).map((u) => (
              <Table.Tr key={u.id} data-testid="user-row">
                <Table.Td>
                  {u.name} {u.id === me.id && <Badge size="xs" variant="light">you</Badge>}
                </Table.Td>
                <Table.Td>
                  <Select size="xs" data={ROLES} value={u.role} allowDeselect={false} onChange={(v) => v && update(u.id, { role: v as Role })} />
                </Table.Td>
                <Table.Td>
                  <Switch checked={!u.disabled} onChange={(e) => update(u.id, { disabled: !e.currentTarget.checked })} />
                </Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    <Tooltip label="Set a new password">
                      <ActionIcon variant="subtle" aria-label="Set password" onClick={() => setResetFor(u)}>
                        <IconKey size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Delete">
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        aria-label="Delete user"
                        onClick={async () => {
                          try {
                            unwrap(await api.DELETE("/api/users/{id}", { params: { path: { id: u.id } } }));
                          } catch (err) {
                            fail(err);
                          }
                          await refresh();
                        }}
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>
      <Card withBorder component="form" onSubmit={create}>
        <Title order={5} mb="sm">
          Add a user
        </Title>
        <Group align="flex-end">
          <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required data-testid="new-user-name" />
          <PasswordInput label="Password" description="At least 8 characters" value={password} onChange={(e) => setPassword(e.currentTarget.value)} required data-testid="new-user-password" />
          <Select label="Role" data={ROLES} value={role} allowDeselect={false} onChange={(v) => v && setRole(v as Role)} w={200} />
          <Button type="submit">Add</Button>
        </Group>
      </Card>
      <Modal opened={resetFor != null} onClose={() => setResetFor(null)} title={`New password for ${resetFor?.name ?? ""}`}>
        <Stack>
          <PasswordInput label="New password" value={newPassword} onChange={(e) => setNewPassword(e.currentTarget.value)} />
          <Text size="xs" c="dimmed">
            Signs the user out everywhere.
          </Text>
          <Button
            onClick={async () => {
              if (resetFor) await update(resetFor.id, { password: newPassword });
              setNewPassword("");
              setResetFor(null);
            }}
          >
            Set password
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
