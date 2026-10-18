// The signed-in application frame: header with the user menu, and the pages.
import { AppShell, Avatar, Button, Group, Menu, Modal, PasswordInput, Stack, Text, Title, UnstyledButton } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconKey, IconLogout } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { useServerEvents } from "../api/events";
import { BrowserPage } from "../browser/BrowserPage";

export function Shell({ user }: { user: Schemas["User"] }) {
  useServerEvents();
  const queryClient = useQueryClient();
  const [passwordOpen, password] = useDisclosure(false);
  const logout = async () => {
    await api.POST("/api/auth/logout");
    await queryClient.invalidateQueries({ queryKey: ["auth"] });
  };
  return (
    <AppShell header={{ height: 48 }} padding={0}>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Title order={4}>stlLibrary</Title>
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
        <BrowserPage />
      </AppShell.Main>
      <PasswordModal opened={passwordOpen} onClose={password.close} />
    </AppShell>
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
