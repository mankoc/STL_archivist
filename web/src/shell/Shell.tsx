// The signed-in application frame: header with the user menu, and the pages.
import { AppShell, Avatar, Button, Group, Menu, Modal, PasswordInput, SegmentedControl, Stack, Text, Title, UnstyledButton, useMantineColorScheme } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconKey, IconLogout } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { useServerEvents } from "../api/events";
import { UserContext } from "../auth/user";
import { BrowserPage } from "../browser/BrowserPage";
import { JobsPage } from "../jobs/JobsPage";
import { MaintenancePage } from "../jobs/MaintenancePage";
import { LookupsPage } from "../lookups/LookupsPage";
import { DuplicatesPage } from "../tools/DuplicatesPage";
import { FindByImagePage } from "../tools/FindByImagePage";
import { FoldersPage } from "../tools/FoldersPage";
import { StatsPage } from "../tools/StatsPage";
import { SettingsPage } from "../settings/SettingsPage";
import { type Page, usePage } from "./route";

export function Shell({ user }: { user: Schemas["User"] }) {
  useServerEvents();
  const queryClient = useQueryClient();
  const [passwordOpen, password] = useDisclosure(false);
  const [page, setPage] = usePage();
  // The colour scheme this user chose (Settings → Account).
  const { setColorScheme } = useMantineColorScheme();
  const prefs = useQuery({ queryKey: ["me-settings"], queryFn: async () => unwrap(await api.GET("/api/me/settings")) as Record<string, unknown> });
  useEffect(() => {
    const scheme = prefs.data?.color_scheme;
    if (scheme === "light" || scheme === "dark" || scheme === "auto") setColorScheme(scheme);
  }, [prefs.data, setColorScheme]);
  const logout = async () => {
    await api.POST("/api/auth/logout");
    await queryClient.invalidateQueries({ queryKey: ["auth"] });
  };
  return (
    <UserContext.Provider value={user}>
      <AppShell header={{ height: 48 }} padding={0}>
        <AppShell.Header>
          <Group h="100%" px="md" justify="space-between">
            <Group gap="lg">
              <Title order={4}>stlLibrary</Title>
              <SegmentedControl
                size="xs"
                value={page}
                onChange={(v) => setPage(v as Page)}
                data={[
                  { value: "library", label: "Library" },
                  { value: "folders", label: "Folders" },
                  { value: "find", label: "Find by Image" },
                  { value: "duplicates", label: "Duplicates" },
                  { value: "lookups", label: "Lookups" },
                  { value: "jobs", label: "Jobs" },
                  { value: "maintenance", label: "Maintenance" },
                  { value: "stats", label: "Statistics" },
                  { value: "settings", label: "Settings" },
                ]}
                data-testid="pages"
              />
            </Group>
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
                <Menu.Item leftSection={<IconKey size={14} />} onClick={password.open}>
                  Change password
                </Menu.Item>
                <Menu.Item leftSection={<IconLogout size={14} />} onClick={logout}>
                  Sign out
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </AppShell.Header>
        <AppShell.Main style={{ height: "100vh", paddingTop: 48, display: "flex", flexDirection: "column" }}>
          {page === "jobs" ? (
            <JobsPage />
          ) : page === "maintenance" ? (
            <MaintenancePage />
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
        <PasswordModal opened={passwordOpen} onClose={password.close} />
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
