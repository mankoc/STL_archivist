// The signed-in application frame: header with the user menu, the pages, and
// the status bar at the bottom.
import { ActionIcon, Anchor, AppShell, Avatar, Button, Group, Image, Menu, Modal, PasswordInput, Stack, Text, Title, Tooltip, UnstyledButton, useMantineColorScheme } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconBooks,
  IconChartBar,
  IconCopy,
  IconFolders,
  IconHelp,
  IconInfoCircle,
  IconKey,
  IconLibrary,
  IconListCheck,
  IconLogout,
  IconPhotoSearch,
  IconSettings,
  IconTags,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";

import { ACCENT_SETTING, DEFAULT_ACCENT, isAccent, useSetAccent } from "../accent";
import { api, type Schemas, unwrap } from "../api/client";
import { useServerEvents } from "../api/events";
import { UserContext } from "../auth/user";
import { BrowserPage } from "../browser/BrowserPage";
import { HelpPage } from "../help/HelpPage";
import splash from "../help/splash.jpg";
import { JobsPage } from "../jobs/JobsPage";
import { LibrariesPage } from "../jobs/LibrariesPage";
import { LookupsPage } from "../lookups/LookupsPage";
import { DuplicatesPage } from "../tools/DuplicatesPage";
import { FindByImagePage } from "../tools/FindByImagePage";
import { FoldersPage } from "../tools/FoldersPage";
import { StatsPage } from "../tools/StatsPage";
import { SettingsPage } from "../settings/SettingsPage";
import { type Page, usePage } from "./route";
import { showJobsButton } from "./status";
import { STATUS_BAR_HEIGHT, StatusBar } from "./StatusBar";

/** The header's page buttons, in order (Help is the ? button). */
const PAGE_BUTTONS: { value: Page; label: string; icon: typeof IconBooks }[] = [
  { value: "library", label: "Library", icon: IconBooks },
  { value: "folders", label: "Folders", icon: IconFolders },
  { value: "find", label: "Find by Image", icon: IconPhotoSearch },
  { value: "duplicates", label: "Duplicates", icon: IconCopy },
  { value: "lookups", label: "Lookups", icon: IconTags },
  { value: "libraries", label: "Libraries", icon: IconLibrary },
  { value: "stats", label: "Statistics", icon: IconChartBar },
  { value: "settings", label: "Settings", icon: IconSettings },
  // Last, and only while jobs wait or run (showJobsButton); the status bar also leads to it.
  { value: "jobs", label: "Jobs", icon: IconListCheck },
];

export function Shell({ user }: { user: Schemas["User"] }) {
  useServerEvents();
  const queryClient = useQueryClient();
  const [passwordOpen, password] = useDisclosure(false);
  const [aboutOpen, about] = useDisclosure(false);
  const [page, setPage] = usePage();
  // The colour scheme and accent colour this user chose (Settings → Account).
  const { setColorScheme } = useMantineColorScheme();
  const setAccent = useSetAccent();
  const prefs = useQuery({ queryKey: ["me-settings"], queryFn: async () => unwrap(await api.GET("/api/me/settings")) as Record<string, unknown> });
  // The status bar's query (kept current by the server's events).
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")) });
  const buttons = PAGE_BUTTONS.filter((p) => p.value !== "jobs" || showJobsButton(jobs.data, page === "jobs"));
  useEffect(() => {
    const scheme = prefs.data?.color_scheme;
    if (scheme === "light" || scheme === "dark" || scheme === "auto") setColorScheme(scheme);
    const accent = prefs.data?.[ACCENT_SETTING];
    if (prefs.data) setAccent(isAccent(accent) ? accent : DEFAULT_ACCENT);
  }, [prefs.data, setColorScheme, setAccent]);
  const logout = async () => {
    await api.POST("/api/auth/logout");
    await queryClient.invalidateQueries({ queryKey: ["auth"] });
  };
  return (
    <UserContext.Provider value={user}>
      <AppShell header={{ height: 48 }} footer={{ height: STATUS_BAR_HEIGHT }} padding={0}>
        <AppShell.Header>
          <Group h="100%" px="md" justify="space-between">
            <Group gap="lg" wrap="nowrap" style={{ minWidth: 0 }}>
              <Title order={4} style={{ flexShrink: 0 }}>StlArchivist</Title>
              <Group gap={4} wrap="nowrap" style={{ overflowX: "auto", minWidth: 0 }} data-testid="pages">
                {buttons.map((p) => (
                  <Button
                    key={p.value}
                    h={40}
                    px={8}
                    variant={page === p.value ? "filled" : "default"}
                    aria-current={page === p.value ? "page" : undefined}
                    onClick={() => setPage(p.value)}
                    style={{ flexShrink: 0 }}
                  >
                    <Stack gap={1} align="center">
                      <p.icon size={17} stroke={1.8} />
                      <Text span fz={11} fw={500} lh={1}>{p.label}</Text>
                    </Stack>
                  </Button>
                ))}
              </Group>
            </Group>
            <Group gap="xs">
            <Tooltip label="Help">
              <ActionIcon variant={page === "help" ? "light" : "subtle"} size="lg" aria-label="Help" onClick={() => setPage("help")} data-testid="help-button">
                <IconHelp size={20} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="About StlArchivist">
              <ActionIcon variant="subtle" size="lg" aria-label="About" onClick={about.open} data-testid="about-button">
                <IconInfoCircle size={20} />
              </ActionIcon>
            </Tooltip>
            <Menu position="bottom-end">
              <Menu.Target>
                <UnstyledButton>
                  <Group gap="xs">
                    <Avatar size="sm" radius="xl">
                      {user.name.slice(0, 1).toUpperCase()}
                    </Avatar>
                    <Text size="sm">{user.name}</Text>
                    <Text size="xs" c="dimmed">
                      {user.role}
                    </Text>
                  </Group>
                </UnstyledButton>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconHelp size={14} />} onClick={() => setPage("help")}>
                  Help
                </Menu.Item>
                <Menu.Item leftSection={<IconInfoCircle size={14} />} onClick={about.open}>
                  About
                </Menu.Item>
                <Menu.Item leftSection={<IconKey size={14} />} onClick={password.open}>
                  Change password
                </Menu.Item>
                <Menu.Item leftSection={<IconLogout size={14} />} onClick={logout}>
                  Sign out
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
            </Group>
          </Group>
        </AppShell.Header>
        <AppShell.Main style={{ height: "100vh", paddingTop: 48, paddingBottom: STATUS_BAR_HEIGHT, display: "flex", flexDirection: "column" }}>
          {page === "help" ? (
            <HelpPage />
          ) : page === "libraries" ? (
            <LibrariesPage />
          ) : page === "jobs" ? (
            <JobsPage />
          ) : page === "settings" ? (
            <SettingsPage />
          ) : page === "lookups" ? (
            <LookupsPage />
          ) : page === "stats" ? (
            <StatsPage />
          ) : page === "folders" ? (
            <FoldersPage />
          ) : page === "duplicates" ? (
            <DuplicatesPage />
          ) : page === "find" ? (
            <FindByImagePage />
          ) : (
            <BrowserPage />
          )}
        </AppShell.Main>
        <AppShell.Footer>
          <StatusBar />
        </AppShell.Footer>
        <PasswordModal opened={passwordOpen} onClose={password.close} />
        <AboutModal opened={aboutOpen} onClose={about.close} />
      </AppShell>
    </UserContext.Provider>
  );
}

function PasswordModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      unwrap(await api.PUT("/api/auth/password", { body: { current, new: next } }));
      notifications.show({ message: "Password changed; sign in again." });
      onClose();
      await queryClient.invalidateQueries({ queryKey: ["auth"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <Modal opened={opened} onClose={onClose} title="Change password">
      <Stack component="form" onSubmit={submit}>
        <PasswordInput label="Current password" value={current} onChange={(e) => setCurrent(e.currentTarget.value)} required />
        <PasswordInput label="New password" description="At least 8 characters" value={next} onChange={(e) => setNext(e.currentTarget.value)} required />
        {error && (
          <Text c="red" size="sm">
            {error}
          </Text>
        )}
        <Button type="submit">Change</Button>
      </Stack>
    </Modal>
  );
}

function AboutModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const info = useQuery({ queryKey: ["system-info"], queryFn: async () => unwrap(await api.GET("/api/system/info")), enabled: opened });
  return (
    <Modal opened={opened} onClose={onClose} title="About StlArchivist" size="lg">
      <Stack gap="xs">
        <Image src={splash} alt="The archivist with a model in his hand" radius="md" />
        <Text size="sm">
          StlArchivist catalogues collections of 3D-printable model archives (.zip, .7z, .rar): metadata, thumbnails,
          tags, duplicates and AI tagging, in a server that this web client and the desktop app share.
        </Text>
        <Text size="sm">Version: {info.data?.version ?? "…"} (this server)</Text>
        <Text size="sm">
          Created by MrIxolite ·{" "}
          <Anchor href="https://github.com/mankoc/STL_archivist" target="_blank" rel="noreferrer">
            github.com/mankoc/STL_archivist
          </Anchor>
        </Text>
        <Text size="sm">Models in this library: {info.data?.models.toLocaleString() ?? "…"}</Text>
        <Text size="xs" c="dimmed">Copyright © 2026 MrIxolite. Licensed CC BY-NC-SA 4.0.</Text>
      </Stack>
    </Modal>
  );
}
