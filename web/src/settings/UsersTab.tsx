// Settings → Users (administrators): accounts, their roles and passwords.
import { ActionIcon, Badge, Button, Card, Group, Modal, PasswordInput, Select, Stack, Switch, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconKey, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { useUser } from "../auth/user";

type Role = Schemas["Role"];
const ROLES: { value: Role; label: string }[] = [
  { value: "viewer", label: "Viewer (read only)" },
  { value: "editor", label: "Editor" },
  { value: "admin", label: "Administrator" },
];

function fail(err: unknown) {
  notifications.show({ color: "red", message: (err as Error).message });
}

export function Users() {
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
